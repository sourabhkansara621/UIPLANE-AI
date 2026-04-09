"""Schemas shared by MCP server and clients."""

from typing import List, Optional

from pydantic import BaseModel, Field


class MCPCluster(BaseModel):
    context: str
    provider: str
    region: str = "unknown"
    kubeconfig_path: Optional[str] = None
    kubeconfig: Optional[str] = None
    source_client: str


class MCPObservabilityTarget(BaseModel):
    name: str
    provider: str = "datadog"
    signal_type: str = "logs"


class MCPClientResult(BaseModel):
    client: str
    clusters: List[MCPCluster] = Field(default_factory=list)
    observability: List[MCPObservabilityTarget] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class MCPServerResponse(BaseModel):
    clusters: List[MCPCluster] = Field(default_factory=list)
    observability: List[MCPObservabilityTarget] = Field(default_factory=list)
    source_clients: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
