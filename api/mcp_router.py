"""api/mcp_router.py
------------------
MCP server routes exposing multi-client discovery and observability data.
"""

import re
import time
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.rbac import get_current_active_user, is_infra_admin, check_mutation_permission
from models.database import User, get_db, ClusterRegistry
from sqlalchemy.orm import Session
from fastapi import Depends as FastAPIDepends
from mcp.server import MCPServer
from mcp.clients.datadog_client import DatadogObservabilityClient
from gateway.cluster_gateway import ClusterGateway, get_gateway

router = APIRouter(prefix="/api/mcp", tags=["MCP"])


@router.get("/catalog")
def get_mcp_catalog(current_user: User = Depends(get_current_active_user)):
    """Return discovered clusters and observability targets across MCP clients."""
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    server = MCPServer()
    data = server.collect()
    return data.model_dump()


@router.get("/health")
def get_mcp_health(current_user: User = Depends(get_current_active_user)):
    """Return high-level MCP status with counts per domain."""
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    server = MCPServer()
    data = server.collect()
    return {
        "status": "ok" if not data.errors else "degraded",
        "source_clients": data.source_clients,
        "cluster_count": len(data.clusters),
        "observability_targets": len(data.observability),
        "errors": data.errors,
    }


@router.get("/datadog/issues")
def get_datadog_namespace_issues(
    namespace: str,
    cluster_name: Optional[str] = None,
    range_hours: int = 6,
    limit: int = 100,
    current_user: User = Depends(get_current_active_user),
):
    """Return Datadog pod issues for a namespace within the selected time range."""
    if not namespace.strip():
        raise HTTPException(status_code=400, detail="namespace is required")

    client = DatadogObservabilityClient()
    result = client.fetch_namespace_issues(
        namespace=namespace.strip(),
        cluster_name=(cluster_name or "").strip() or None,
        range_hours=range_hours,
        limit=limit,
    )

    if not result.get("configured") and current_user.role != "infra-admin":
        raise HTTPException(status_code=503, detail=result.get("detail") or "Datadog is not configured")

    return result


# ── Autofix Models ──────────────────────────────────────────────────────────

class AutofixRequest(BaseModel):
    pod_name: str
    namespace: str
    cluster_name: Optional[str] = None
    app_name: Optional[str] = None
    action: str           # restart | scale_up | increase_memory | increase_cpu | patch_config
    params: Optional[Dict[str, Any]] = None


# ── Autofix Helpers ─────────────────────────────────────────────────────────

def _find_deployment_for_pod(cluster_name: str, namespace: str, pod_name: str, gateway: ClusterGateway) -> Optional[str]:
    """Try to resolve owning Deployment name from a pod name heuristic."""
    try:
        core = gateway.get_core_client(cluster_name)
        pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        refs = pod.metadata.owner_references or []
        for ref in refs:
            if ref.kind == "ReplicaSet":
                apps = gateway.get_apps_client(cluster_name)
                rs = apps.read_namespaced_replica_set(name=ref.name, namespace=namespace)
                for rs_ref in (rs.metadata.owner_references or []):
                    if rs_ref.kind == "Deployment":
                        return rs_ref.name
    except Exception:
        pass
    # fallback: strip last two hash segments (pod-name-<rs>-<pod>)
    parts = pod_name.rsplit("-", 2)
    if len(parts) >= 3:
        return parts[0]
    return pod_name


def _patch_deployment_resources(
    cluster_name: str, namespace: str, deployment_name: str,
    gateway: ClusterGateway, memory_limit: Optional[str], cpu_limit: Optional[str],
    memory_request: Optional[str], cpu_request: Optional[str],
) -> Dict[str, Any]:
    apps = gateway.get_apps_client(cluster_name)
    dep = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    containers = dep.spec.template.spec.containers or []
    if not containers:
        raise ValueError("No containers found in deployment")

    patched_containers = []
    for c in containers:
        res = c.resources or type("R", (), {"limits": {}, "requests": {}})()
        limits = dict(res.limits or {})
        requests = dict(res.requests or {})
        if memory_limit:
            limits["memory"] = memory_limit
        if cpu_limit:
            limits["cpu"] = cpu_limit
        if memory_request:
            requests["memory"] = memory_request
        if cpu_request:
            requests["cpu"] = cpu_request
        patched_containers.append({
            "name": c.name,
            "resources": {"limits": limits, "requests": requests},
        })

    patch = {"spec": {"template": {"spec": {"containers": patched_containers}}}}
    apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch)
    updated = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    uc = updated.spec.template.spec.containers or []
    res_info = {}
    if uc:
        r = uc[0].resources or type("R", (), {"limits": {}, "requests": {}})()
        res_info = {"limits": dict(r.limits or {}), "requests": dict(r.requests or {})}
    return {"deployment": deployment_name, "namespace": namespace, "resources": res_info}


def _restart_deployment(cluster_name: str, namespace: str, deployment_name: str, gateway: ClusterGateway) -> Dict[str, Any]:
    import datetime
    apps = gateway.get_apps_client(cluster_name)
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.datetime.utcnow().isoformat() + "Z"
                    }
                }
            }
        }
    }
    apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch)
    return {"deployment": deployment_name, "namespace": namespace, "restarted": True}


# ── Autofix Endpoint ────────────────────────────────────────────────────────

@router.post("/autofix/apply")
def apply_autofix(
    req: AutofixRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Apply a remediation action for a Datadog-detected pod issue."""
    if not req.pod_name or not req.namespace:
        raise HTTPException(status_code=400, detail="pod_name and namespace are required")

    allowed_actions = {"restart", "scale_up", "increase_memory", "increase_cpu", "patch_config"}
    if req.action not in allowed_actions:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'")

    # Resolve cluster
    cluster_name = req.cluster_name
    if not cluster_name:
        entry = db.query(ClusterRegistry).filter(ClusterRegistry.app_name == req.app_name).first() if req.app_name else None
        if entry:
            cluster_name = entry.name
        else:
            entries = db.query(ClusterRegistry).all()
            cluster_name = entries[0].name if entries else None

    if not cluster_name:
        raise HTTPException(status_code=400, detail="Cannot resolve cluster. Provide cluster_name or app_name.")

    # RBAC: mutation check
    app_name = req.app_name
    if not app_name:
        entry = db.query(ClusterRegistry).filter(ClusterRegistry.name == cluster_name).first()
        app_name = entry.app_name if entry else None

    if app_name and not check_mutation_permission(current_user, app_name, db):
        raise HTTPException(status_code=403, detail=f"Mutation denied. You do not have write access to '{app_name}'.")

    namespace = req.namespace
    pod_name = req.pod_name
    params = req.params or {}

    # Resolve deployment
    deployment_name = _find_deployment_for_pod(cluster_name, namespace, pod_name, gateway)
    if not deployment_name:
        raise HTTPException(status_code=404, detail=f"Could not resolve deployment for pod '{pod_name}'")

    try:
        if req.action == "restart":
            result = _restart_deployment(cluster_name, namespace, deployment_name, gateway)
            return {"ok": True, "action": "restart", "detail": f"Deployment '{deployment_name}' rollout restarted.", "data": result}

        elif req.action == "scale_up":
            from capabilities.k8s_writer import update_deployment
            apps_client = gateway.get_apps_client(cluster_name)
            dep = apps_client.read_namespaced_deployment(name=deployment_name, namespace=namespace)
            current_replicas = dep.spec.replicas or 1
            new_replicas = int(params.get("replicas", current_replicas + 1))
            result = update_deployment(cluster_name, namespace, deployment_name, gateway, replicas=new_replicas)
            return {"ok": True, "action": "scale_up", "detail": f"Scaled '{deployment_name}' from {current_replicas} → {new_replicas} replicas.", "data": result}

        elif req.action == "increase_memory":
            memory_limit = params.get("memory_limit") or "512Mi"
            memory_request = params.get("memory_request") or None
            result = _patch_deployment_resources(
                cluster_name, namespace, deployment_name, gateway,
                memory_limit=memory_limit, cpu_limit=None,
                memory_request=memory_request, cpu_request=None,
            )
            return {"ok": True, "action": "increase_memory", "detail": f"Memory limit updated to {memory_limit} on '{deployment_name}'.", "data": result}

        elif req.action == "increase_cpu":
            cpu_limit = params.get("cpu_limit") or "500m"
            cpu_request = params.get("cpu_request") or None
            result = _patch_deployment_resources(
                cluster_name, namespace, deployment_name, gateway,
                memory_limit=None, cpu_limit=cpu_limit,
                memory_request=None, cpu_request=cpu_request,
            )
            return {"ok": True, "action": "increase_cpu", "detail": f"CPU limit updated to {cpu_limit} on '{deployment_name}'.", "data": result}

        elif req.action == "patch_config":
            key = str(params.get("key", ""))
            value = str(params.get("value", ""))
            configmap_name = str(params.get("configmap", f"{deployment_name}-config"))
            if not key:
                raise HTTPException(status_code=400, detail="params.key is required for patch_config")
            core = gateway.get_core_client(cluster_name)
            patch = {"data": {key: value}}
            core.patch_namespaced_config_map(name=configmap_name, namespace=namespace, body=patch)
            return {"ok": True, "action": "patch_config", "detail": f"ConfigMap '{configmap_name}' key '{key}' updated.", "data": {"configmap": configmap_name, "key": key, "value": value}}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

