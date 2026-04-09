"""
api/k8s_router.py
-----------------
FastAPI router for direct Kubernetes resource endpoints.
These are structured JSON endpoints — use /api/chat/query for AI summaries.

Routes
------
GET /api/k8s/{app_name}/pods                  List pods
GET /api/k8s/{app_name}/namespaces            List namespaces
GET /api/k8s/{app_name}/quota                 Resource quota
GET /api/k8s/{app_name}/deployments           List deployments
GET /api/k8s/{app_name}/hpa                   HPA config
GET /api/k8s/{app_name}/ingress               Ingress + network policy
GET /api/k8s/{app_name}/pods/{pod}/logs       Pod logs
GET /api/k8s/{app_name}/pods/{pod}/describe   Pod describe
GET /api/k8s/{app_name}/version               K8s version
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from models.database import get_db, User, ClusterRegistry
from models.schemas import (
    PodInfo, NamespaceInfo, ResourceQuotaInfo,
    DeploymentInfo, HPAInfo, IngressInfo,
)
from auth.rbac import get_current_active_user, require_app_access
from gateway.cluster_gateway import ClusterGateway, get_gateway
from capabilities.k8s_reader import (
    list_pods, list_namespaces, get_resource_quota,
    list_deployments, get_hpa, list_ingresses,
    get_pod_logs, describe_pod, check_network_policy,
    get_k8s_version,
)

router = APIRouter(prefix="/api/k8s", tags=["Kubernetes Resources"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_registry(
    app_name: str,
    db: Session,
    environment: Optional[str] = None,
) -> List[ClusterRegistry]:
    """
    Resolve an application name to its cluster registry entries.
    
    Looks up where an application is deployed by querying the ClusterRegistry.
    An app can be deployed in multiple clusters/environments.
    
    Args:
        app_name: Application name to look up
        db: Database session
        environment: Optional filter (e.g., 'prod', 'staging', 'nonprod')
        
    Returns:
        List of ClusterRegistry entries showing where the app is deployed
        
    Raises:
        HTTPException 404: If app not found in any cluster
        
    Example:
        For app_name="payments-api" might return:
        - [ClusterRegistry(cluster="gke-prod", namespace="payments-prod", env="prod")]
        Or multiple entries if deployed to staging + prod.
    """
    q = db.query(ClusterRegistry).filter(
        ClusterRegistry.app_name == app_name,
        ClusterRegistry.is_active == True,
    )
    if environment:
        q = q.filter(ClusterRegistry.environment == environment)
    entries = q.all()
    if not entries:
        raise HTTPException(
            status_code=404,
            detail=f"App '{app_name}' not found in cluster registry.",
        )
    return entries


def _first_registry(
    app_name: str,
    db: Session,
    environment: Optional[str] = None,
) -> ClusterRegistry:
    """
    Get the first (preferred) registry entry for an application.
    
    When an app exists in multiple environments, this returns the most
    relevant one - preferring production if no environment specified.
    
    Args:
        app_name: Application name
        db: Database session
        environment: Optional environment filter
        
    Returns:
        Single ClusterRegistry entry (production if available, else first entry)
        
    Raises:
        HTTPException 404: If app not found
        
    Usage:
        Used for operations that only need one cluster (e.g., fetching pod logs).
    """
    entries = _get_registry(app_name, db, environment)
    # Prefer production environment if present
    prod = [e for e in entries if e.environment == "prod"]
    return prod[0] if prod else entries[0]


# ── Pods ──────────────────────────────────────────────────────────────────────

@router.get("/{app_name}/pods", response_model=List[PodInfo])
def get_pods(
    app_name: str,
    environment: Optional[str] = Query(None, description="prod | nonprod | staging"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    List all pods for an application across its registered clusters.
    
    Returns detailed pod information including status, resource usage,
    restarts, and age.
    
    Args:
        app_name: Application name (e.g., 'payments-api')
        environment: Optional filter (prod/staging/nonprod)
        db: Database session
        current_user: Authenticated user
        gateway: Cluster gateway for K8s API access
        
    Returns:
        List of PodInfo objects containing:
        - name, namespace, status, ready
        - restarts, age, node
        - cpu_request, memory_request, image
        
    Raises:
        HTTPException 403: If user lacks access to this app
        HTTPException 404: If app not found in registry
        
    RBAC:
        Requires read access to the application (checked via require_app_access).
    """
    # Check user has permission to access this app
    require_app_access(current_user, app_name, db)
    
    # Get all clusters where this app is deployed
    entries = _get_registry(app_name, db, environment)
    
    # Fetch pods from all relevant clusters and aggregate
    all_pods = []
    for reg in entries:
        all_pods.extend(list_pods(reg.cluster_name, reg.namespace, gateway))
    return all_pods


# ── Namespaces ────────────────────────────────────────────────────────────────

@router.get("/{app_name}/namespaces", response_model=List[NamespaceInfo])
def get_namespaces(
    app_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """List namespaces associated with an application."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db)
    all_ns = []
    for reg in entries:
        all_ns.extend(list_namespaces(reg.cluster_name, gateway, app_name))
    return all_ns


# ── Resource Quota ────────────────────────────────────────────────────────────

@router.get("/{app_name}/quota")
def get_quota(
    app_name: str,
    environment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Fetch resource quota for an application's namespace."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db, environment)
    results = {}
    for reg in entries:
        quota = get_resource_quota(reg.cluster_name, reg.namespace, gateway)
        results[reg.cluster_name] = quota.model_dump() if quota else None
    return {"app_name": app_name, "quotas": results}


# ── Deployments ───────────────────────────────────────────────────────────────

@router.get("/{app_name}/deployments", response_model=List[DeploymentInfo])
def get_deployments(
    app_name: str,
    environment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """List all deployments for an application."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db, environment)
    all_deps = []
    for reg in entries:
        all_deps.extend(list_deployments(reg.cluster_name, reg.namespace, gateway))
    return all_deps


# ── HPA ───────────────────────────────────────────────────────────────────────

@router.get("/{app_name}/hpa", response_model=List[HPAInfo])
def get_hpa_config(
    app_name: str,
    environment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Get HorizontalPodAutoscaler config for an application."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db, environment)
    all_hpa = []
    for reg in entries:
        all_hpa.extend(get_hpa(reg.cluster_name, reg.namespace, gateway))
    return all_hpa


# ── Ingress & Network ─────────────────────────────────────────────────────────

@router.get("/{app_name}/ingress")
def get_ingress(
    app_name: str,
    environment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Get ingress config and network policies for an application."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db, environment)
    results = {}
    for reg in entries:
        ingresses = list_ingresses(reg.cluster_name, reg.namespace, gateway)
        net_policies = check_network_policy(reg.cluster_name, reg.namespace, gateway)
        results[reg.cluster_name] = {
            "ingresses": [i.model_dump() for i in ingresses],
            "network_policies": net_policies,
            "environment": reg.environment,
        }
    return {"app_name": app_name, "network": results}


# ── Pod Logs ──────────────────────────────────────────────────────────────────

@router.get("/{app_name}/pods/{pod_name}/logs")
def get_logs(
    app_name: str,
    pod_name: str,
    namespace: Optional[str] = Query(None),
    tail: int = Query(100, ge=1, le=1000),
    previous: bool = Query(False, description="Get logs from previous crashed container"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Fetch pod logs.
    Set previous=true to get logs from the last crashed container instance.
    """
    require_app_access(current_user, app_name, db)
    reg = _first_registry(app_name, db)
    ns = namespace or reg.namespace

    logs = get_pod_logs(
        cluster_name=reg.cluster_name,
        namespace=ns,
        pod_name=pod_name,
        gateway=gateway,
        tail_lines=tail,
        previous=previous,
    )
    return {
        "pod": pod_name,
        "namespace": ns,
        "cluster": reg.cluster_name,
        "tail_lines": tail,
        "logs": logs,
    }


# ── Pod Describe ──────────────────────────────────────────────────────────────

@router.get("/{app_name}/pods/{pod_name}/describe")
def describe(
    app_name: str,
    pod_name: str,
    namespace: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Describe a pod — equivalent to kubectl describe pod."""
    require_app_access(current_user, app_name, db)
    reg = _first_registry(app_name, db)
    ns = namespace or reg.namespace

    info = describe_pod(
        cluster_name=reg.cluster_name,
        namespace=ns,
        pod_name=pod_name,
        gateway=gateway,
    )
    return info


# ── K8s Version ───────────────────────────────────────────────────────────────

@router.get("/{app_name}/version")
def get_version(
    app_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Return the Kubernetes version for the cluster(s) hosting this app."""
    require_app_access(current_user, app_name, db)
    entries = _get_registry(app_name, db)
    return {
        "app_name": app_name,
        "clusters": {
            reg.cluster_name: {
                "k8s_version": get_k8s_version(reg.cluster_name, gateway),
                "cloud_provider": reg.cloud_provider,
                "environment": reg.environment,
            }
            for reg in entries
        },
    }
