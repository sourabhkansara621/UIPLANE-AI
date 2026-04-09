from .auth_router import router as auth_router
from .registry_router import router as registry_router
from .chat_router import router as chat_router
from .k8s_router import router as k8s_router
from .audit_router import router as audit_router
from .mcp_router import router as mcp_router

__all__ = [
    "auth_router",
    "registry_router",
    "chat_router",
    "k8s_router",
    "audit_router",
    "mcp_router",
]
