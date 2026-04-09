"""MCP server aggregator for multiple clients."""

from typing import List

from mcp.clients import (
    MCPClient,
    GKEClient,
    EKSClient,
    AKSClient,
    DatadogObservabilityClient,
)
from mcp.schemas import MCPServerResponse


class MCPServer:
    """Aggregates cluster and observability records from multiple MCP clients."""

    def __init__(self, clients: List[MCPClient] | None = None):
        self._clients = clients or [
            GKEClient(),
            EKSClient(),
            AKSClient(),
            DatadogObservabilityClient(),
        ]

    def collect(self) -> MCPServerResponse:
        aggregated = MCPServerResponse()

        for client in self._clients:
            result = client.collect()
            aggregated.source_clients.append(result.client)
            aggregated.clusters.extend(result.clusters)
            aggregated.observability.extend(result.observability)
            aggregated.errors.extend([f"{result.client}: {err}" for err in result.errors])

        return aggregated
