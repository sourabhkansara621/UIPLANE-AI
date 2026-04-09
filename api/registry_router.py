"""
api/registry_router.py
----------------------
FastAPI router for cluster registry management.

Routes
------
GET    /api/registry/clusters               List all registered cluster entries
POST   /api/registry/clusters               Register a new app→cluster mapping
GET    /api/registry/clusters/{app_name}    Get all clusters for an app
DELETE /api/registry/clusters/{id}          Deactivate a registry entry
GET    /api/registry/where/{app_name}       Human-friendly cluster location query
GET    /api/registry/health                 Test connectivity to all clusters
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from models.database import get_db, User, ClusterRegistry
from models.schemas import ClusterRegistryOut, ClusterRegistryCreate
from auth.rbac import (
    get_current_active_user, is_infra_admin,
    check_app_access,
)
from gateway.cluster_gateway import ClusterGateway, get_gateway

router = APIRouter(prefix="/api/registry", tags=["Cluster Registry"])


# ── List all entries ──────────────────────────────────────────────────────────

@router.get("/clusters", response_model=List[ClusterRegistryOut])
def list_cluster_entries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    List cluster registry entries visible to the current user.
    infra-admin sees all; others see only their own apps.
    """
    q = db.query(ClusterRegistry).filter(ClusterRegistry.is_active == True)

    if not is_infra_admin(current_user):
        # Get the apps the user is allowed to access
        from auth.rbac import get_user_allowed_apps
        allowed_apps = get_user_allowed_apps(current_user, db)
        q = q.filter(ClusterRegistry.app_name.in_(allowed_apps))

    return q.order_by(ClusterRegistry.app_name, ClusterRegistry.environment).all()


# ── Register new entry ────────────────────────────────────────────────────────

@router.post(
    "/clusters",
    response_model=ClusterRegistryOut,
    status_code=status.HTTP_201_CREATED,
)
def register_cluster_entry(
    data: ClusterRegistryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Register a new app→cluster→namespace mapping.
    Only infra-admin can add entries.
    """
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    entry = ClusterRegistry(
        app_name=data.app_name,
        cluster_name=data.cluster_name,
        cloud_provider=data.cloud_provider,
        environment=data.environment,
        region=data.region,
        namespace=data.namespace,
        k8s_version=data.k8s_version,
        kubeconfig_secret=data.kubeconfig_secret,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ── Get clusters for a specific app ──────────────────────────────────────────

@router.get("/clusters/{app_name}", response_model=List[ClusterRegistryOut])
def get_clusters_for_app(
    app_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return all cluster entries for a given application.
    Enforces RBAC — user must have access to the app.
    """
    if not check_app_access(current_user, app_name, db):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied to '{app_name}'.",
        )

    entries = (
        db.query(ClusterRegistry)
        .filter(
            ClusterRegistry.app_name == app_name,
            ClusterRegistry.is_active == True,
        )
        .all()
    )
    if not entries:
        raise HTTPException(
            status_code=404,
            detail=f"No cluster entries found for '{app_name}'.",
        )
    return entries


# ── Where is app deployed ─────────────────────────────────────────────────────

@router.get("/where/{app_name}")
def where_is_app(
    app_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Human-friendly answer: where is this app deployed?
    Returns cluster name, environment, cloud, region, namespace.
    """
    if not check_app_access(current_user, app_name, db):
        raise HTTPException(status_code=403, detail=f"Access denied to '{app_name}'.")

    entries = (
        db.query(ClusterRegistry)
        .filter(
            ClusterRegistry.app_name == app_name,
            ClusterRegistry.is_active == True,
        )
        .all()
    )
    if not entries:
        raise HTTPException(status_code=404, detail=f"'{app_name}' not found in registry.")

    return {
        "app_name": app_name,
        "deployments": [
            {
                "cluster": e.cluster_name,
                "environment": e.environment,
                "cloud_provider": e.cloud_provider,
                "region": e.region,
                "namespace": e.namespace,
                "k8s_version": e.k8s_version,
            }
            for e in entries
        ],
        "total_clusters": len(entries),
    }


# ── Deactivate entry ──────────────────────────────────────────────────────────

@router.delete("/clusters/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_cluster_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Soft-delete a cluster registry entry (sets is_active=False)."""
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    entry = db.query(ClusterRegistry).filter(ClusterRegistry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Registry entry not found.")

    entry.is_active = False
    db.commit()


# ── Cluster health check ──────────────────────────────────────────────────────

@router.get("/health")
def cluster_health(
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Test connectivity to all loaded clusters.
    Returns status for each cluster. infra-admin only.
    """
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    results = {}
    for cluster_name in gateway.list_clusters():
        results[cluster_name] = {
            "reachable": gateway.test_connection(cluster_name),
            "cluster": cluster_name,
        }
    return {"clusters": results, "total": len(results)}
