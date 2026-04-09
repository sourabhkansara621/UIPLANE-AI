"""
models/schemas.py
-----------------
Pydantic v2 schemas for all API request/response bodies.
Keeps ORM models separate from API contracts.
"""

from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    allowed_apps: List[str] = []

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    role: str = "developer"


# ── RBAC ──────────────────────────────────────────────────────────────────────

class AppOwnershipCreate(BaseModel):
    user_id: str
    app_name: str
    can_read: bool = True
    can_mutate: bool = False


class AppOwnershipOut(BaseModel):
    id: int
    user_id: str
    app_name: str
    can_read: bool
    can_mutate: bool
    granted_by: Optional[str]
    granted_at: datetime

    model_config = {"from_attributes": True}


# ── Cluster Registry ──────────────────────────────────────────────────────────

class ClusterRegistryOut(BaseModel):
    id: int
    app_name: str
    cluster_name: str
    cloud_provider: str
    environment: str
    region: str
    namespace: str
    k8s_version: Optional[str]
    is_active: bool

    model_config = {"from_attributes": True}


class ClusterRegistryCreate(BaseModel):
    app_name: str
    cluster_name: str
    cloud_provider: str
    environment: str
    region: str
    namespace: str
    k8s_version: Optional[str] = None
    kubeconfig_secret: Optional[str] = None


# ── Chat / Query ──────────────────────────────────────────────────────────────

class SaveDeploymentRequest(BaseModel):
    session_id: str
    deployment_name: Optional[str] = None
    resource_name: Optional[str] = None
    resource_kind: Optional[str] = None
    yaml_content: str = Field(min_length=10)
    app_name: Optional[str] = None
    namespace: Optional[str] = None


class ChatQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None
    chat_mode: str = Field(default="k8-info")


class ChatQueryResponse(BaseModel):
    answer: str
    data: Optional[Dict[str, Any]] = None
    clusters_accessed: List[str] = []
    auth_checked: bool = True
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── K8s Resource Responses ────────────────────────────────────────────────────

class PodInfo(BaseModel):
    name: str
    namespace: str
    status: str
    ready: str
    restarts: int
    cpu_request: Optional[str]
    memory_request: Optional[str]
    image: str
    node: Optional[str]
    age: Optional[str]


class NamespaceInfo(BaseModel):
    name: str
    status: str
    app_name: str
    cluster_name: str
    environment: str
    labels: Dict[str, str] = {}


class ResourceQuotaInfo(BaseModel):
    name: Optional[str] = None
    namespace: str
    cpu_limit: Optional[str]
    cpu_used: Optional[str]
    cpu_percent: Optional[float]
    memory_limit: Optional[str]
    memory_used: Optional[str]
    memory_percent: Optional[float]
    pods_limit: Optional[str]
    pods_used: Optional[str]


class HPAInfo(BaseModel):
    name: str
    namespace: str
    min_replicas: int
    max_replicas: int
    current_replicas: int
    desired_replicas: int
    target_cpu_percent: Optional[int]
    current_cpu_percent: Optional[int]


class IngressInfo(BaseModel):
    name: str
    namespace: str
    host: str
    tls_enabled: bool
    backend_service: str
    backend_port: str
    address: Optional[str]


class DeploymentInfo(BaseModel):
    name: str
    namespace: str
    replicas: int
    ready_replicas: int
    image: str
    strategy: str
    age: Optional[str]


# ── Audit ─────────────────────────────────────────────────────────────────────

class AuditLogOut(BaseModel):
    id: int
    username: str
    action: str
    resource_type: str
    resource_name: Optional[str]
    app_name: Optional[str]
    cluster_name: Optional[str]
    query_text: Optional[str]
    timestamp: datetime
    success: bool

    model_config = {"from_attributes": True}


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    phase: str = "Phase 1 — Read-only + Auth"
    clusters_connected: int = 0
    db_connected: bool = False
    redis_connected: bool = False
