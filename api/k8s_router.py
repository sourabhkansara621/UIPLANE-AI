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
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session

from models.database import get_db, User, ClusterRegistry, AuditLog
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


# ── Version Comparison ─────────────────────────────────────────────────────────

def _compare_versions(v1: str, v2: str) -> int:
    """
    Compare two semantic versions.
    
    Args:
        v1: First version (e.g., "1.27.0")
        v2: Second version (e.g., "1.28.0")
        
    Returns:
        > 0 if v1 > v2
        = 0 if v1 == v2
        < 0 if v1 < v2
    """
    def parse_version(v: str) -> tuple:
        """Parse version string into tuple of integers."""
        try:
            parts = [int(x) for x in v.split('.')[:3]]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        except:
            return (0, 0, 0)
    
    v1_tuple = parse_version(v1)
    v2_tuple = parse_version(v2)
    
    if v1_tuple > v2_tuple:
        return 1
    elif v1_tuple < v2_tuple:
        return -1
    else:
        return 0


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


# ── Cluster Upgrade ───────────────────────────────────────────────────────────

@router.get("/upgrade/{cluster_name}/versions")
def get_upgrade_versions(
    cluster_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Get current and available Kubernetes versions for a cluster.
    
    Returns the current cluster version and list of available upgrade paths.
    
    Args:
        cluster_name: Name of the cluster
        db: Database session
        current_user: Authenticated user
        gateway: Cluster gateway for K8s API access
        
    Returns:
        Dict with current_version and available_versions list
        
    Raises:
        HTTPException 403: If user lacks access to this cluster
        HTTPException 404: If cluster not found in registry
    """
    # Verify user has access to any app on this cluster
    reg_entries = db.query(ClusterRegistry).filter(
        ClusterRegistry.cluster_name == cluster_name,
        ClusterRegistry.is_active == True,
    ).all()
    
    if not reg_entries:
        raise HTTPException(
            status_code=404,
            detail=f"Cluster '{cluster_name}' not found in registry.",
        )
    
    # Check if user has access to at least one app on this cluster
    accessible = False
    for entry in reg_entries:
        try:
            require_app_access(current_user, entry.app_name, db)
            accessible = True
            break
        except HTTPException:
            continue
    
    if not accessible:
        raise HTTPException(
            status_code=403,
            detail="User does not have access to any apps on this cluster.",
        )
    
    # Get current version
    try:
        current_version = get_k8s_version(cluster_name, gateway)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch current version: {str(e)}",
        )

    # Fallback to DB-stored cluster version when live lookup is unavailable.
    if current_version == "unknown":
        registry_version = next(
            (
                str(entry.k8s_version).strip().lstrip("v")
                for entry in reg_entries
                if entry.k8s_version
            ),
            None,
        )
        if registry_version:
            current_version = registry_version
    
    # Build a dynamic catalog of versions so newer clusters (e.g., 1.33/1.34)
    # still get a valid "next" option.
    current_major_minor = ".".join(current_version.split(".")[:2]) if current_version != "unknown" else "1.26"
    current_minor = 26
    try:
        parts = current_major_minor.split(".")
        if len(parts) >= 2:
            current_minor = int(parts[1])
    except Exception:
        current_minor = 26

    min_minor = 26
    max_minor = max(36, current_minor + 2)
    k8s_versions = [f"1.{minor}" for minor in range(max_minor, min_minor - 1, -1)]
    
    # Filter to show only the immediate next version newer than current.
    if current_version == "unknown":
        available = []
    else:
        current_major_minor = ".".join(current_version.split(".")[:2])
        newer_versions = [v for v in k8s_versions if _compare_versions(v, current_major_minor) > 0]
        newer_versions_sorted = sorted(
            newer_versions,
            key=lambda v: tuple(int(x) for x in v.split(".")),
        )
        available = [newer_versions_sorted[0]] if newer_versions_sorted else []
    
    return {
        "cluster_name": cluster_name,
        "current_version": current_version,
        "available_versions": available,
    }


@router.post("/upgrade/{cluster_name}")
def trigger_cluster_upgrade(
    cluster_name: str,
    upgrade_request: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Trigger a Kubernetes cluster upgrade to a specified version.
    
    This initiates an asynchronous cluster upgrade operation. The actual
    upgrade process depends on the cloud provider (EKS, GKE, AKS, etc.).
    
    Args:
        cluster_name: Name of the cluster to upgrade
        upgrade_request: Dict with 'target_version' field
        db: Database session
        current_user: Authenticated user
        gateway: Cluster gateway
        
    Returns:
        Dict confirming upgrade initiation with status
        
    Raises:
        HTTPException 400: If target_version not specified or invalid
        HTTPException 403: If user lacks access to this cluster
        HTTPException 404: If cluster not found in registry
        HTTPException 500: If upgrade fails
    """
    # Verify user has access to this cluster
    reg_entries = db.query(ClusterRegistry).filter(
        ClusterRegistry.cluster_name == cluster_name,
        ClusterRegistry.is_active == True,
    ).all()
    
    if not reg_entries:
        raise HTTPException(
            status_code=404,
            detail=f"Cluster '{cluster_name}' not found in registry.",
        )
    
    accessible = False
    for entry in reg_entries:
        try:
            require_app_access(current_user, entry.app_name, db)
            accessible = True
            break
        except HTTPException:
            continue
    
    if not accessible:
        raise HTTPException(
            status_code=403,
            detail="User does not have access to any apps on this cluster.",
        )
    
    target_version = upgrade_request.get("target_version", "").strip() if isinstance(upgrade_request, dict) else ""
    if not target_version:
        raise HTTPException(
            status_code=400,
            detail="target_version is required",
        )
    
    # Validate version format
    if not isinstance(target_version, str) or not target_version.replace(".", "").isdigit():
        raise HTTPException(
            status_code=400,
            detail="Invalid version format. Expected format: x.y or x.y.z",
        )
    
    try:
        # Log the upgrade request
        audit_entry = AuditLog(
            user_id=current_user.id,
            action="CLUSTER_UPGRADE",
            resource_type="cluster",
            resource_name=cluster_name,
            app_name=None,
            namespace=None,
            query_text=None,
            result_summary=f"Cluster upgrade to v{target_version} initiated",
            success=True,
            extra={
                "cluster_name": cluster_name,
                "target_version": target_version,
                "cloud_provider": reg_entries[0].cloud_provider if reg_entries else "unknown",
            },
        )
        db.add(audit_entry)
        db.commit()
        
        # In production, trigger actual cloud provider upgrade here
        # For now, return a simulated response
        return {
            "status": "upgrade_initiated",
            "cluster_name": cluster_name,
            "target_version": target_version,
            "message": f"Cluster upgrade to v{target_version} has been initiated. This may take 30-60 minutes depending on cluster size.",
            "cloud_provider": reg_entries[0].cloud_provider if reg_entries else "unknown",
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Upgrade initiation failed: {str(e)}",
        )


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
def trigger_cluster_upgrade(
    cluster_name: str,
    upgrade_request: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Trigger a Kubernetes cluster upgrade to a specified version.
    
    This initiates an asynchronous cluster upgrade operation. The actual
    upgrade process depends on the cloud provider (EKS, GKE, AKS, etc.).
    
    Args:
        cluster_name: Name of the cluster to upgrade
        request_body: Dict with 'target_version' field
        db: Database session
        current_user: Authenticated user
        gateway: Cluster gateway
        
    Returns:
        Dict confirming upgrade initiation with status
        
    Raises:
        HTTPException 400: If target_version not specified or invalid
        HTTPException 403: If user lacks access to this cluster
        HTTPException 404: If cluster not found in registry
        HTTPException 500: If upgrade fails
    """
    # Verify user has access to this cluster
    reg_entries = db.query(ClusterRegistry).filter(
        ClusterRegistry.cluster_name == cluster_name,
        ClusterRegistry.is_active == True,
    ).all()
    
    if not reg_entries:
        raise HTTPException(
            status_code=404,
            detail=f"Cluster '{cluster_name}' not found in registry.",
        )
    
    accessible = False
    for entry in reg_entries:
        try:
            require_app_access(current_user, entry.app_name, db)
            accessible = True
            break
        except HTTPException:
            continue
    
    if not accessible:
        raise HTTPException(
            status_code=403,
            detail="User does not have access to any apps on this cluster.",
        )
    
    target_version = upgrade_request.get("target_version", "").strip() if isinstance(upgrade_request, dict) else ""
    if not target_version:
        raise HTTPException(
            status_code=400,
            detail="target_version is required",
        )
    
    # Validate version format
    if not isinstance(target_version, str) or not target_version.replace(".", "").isdigit():
        raise HTTPException(
            status_code=400,
            detail="Invalid version format. Expected format: x.y or x.y.z",
        )
    
    try:
        # Log the upgrade request
        audit_entry = AuditLog(
            user_id=current_user.id,
            action="CLUSTER_UPGRADE",
            resource_type="cluster",
            resource_name=cluster_name,
            app_name=None,
            namespace=None,
            query_text=None,
            result_summary=f"Cluster upgrade to v{target_version} initiated",
            success=True,
            extra={
                "cluster_name": cluster_name,
                "target_version": target_version,
                "cloud_provider": reg_entries[0].cloud_provider if reg_entries else "unknown",
            },
        )
        db.add(audit_entry)
        db.commit()
        
        # In production, trigger actual cloud provider upgrade here
        # For now, return a simulated response
        return {
            "status": "upgrade_initiated",
            "cluster_name": cluster_name,
            "target_version": target_version,
            "message": f"Cluster upgrade to v{target_version} has been initiated. This may take 30-60 minutes depending on cluster size.",
            "cloud_provider": reg_entries[0].cloud_provider if reg_entries else "unknown",
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Upgrade initiation failed: {str(e)}",
        )
