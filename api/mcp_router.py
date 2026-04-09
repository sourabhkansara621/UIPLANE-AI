"""api/mcp_router.py
------------------
MCP server routes exposing multi-client discovery and observability data.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth.rbac import get_current_active_user, is_infra_admin
from models.database import User
from mcp.server import MCPServer

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
