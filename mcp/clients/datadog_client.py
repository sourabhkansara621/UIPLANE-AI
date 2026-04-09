"""Datadog observability MCP client."""

from mcp.clients.base import MCPClient
from mcp.schemas import MCPClientResult, MCPObservabilityTarget
from config.settings import get_settings


class DatadogObservabilityClient(MCPClient):
    name = "datadog"

    def collect(self) -> MCPClientResult:
        settings = get_settings()
        result = MCPClientResult(client=self.name)

        targets = [t.strip() for t in settings.mcp_datadog_targets.split(",") if t.strip()]
        for target in targets:
            result.observability.append(
                MCPObservabilityTarget(
                    name=target,
                    provider="datadog",
                    signal_type="logs+metrics",
                )
            )

        if not settings.datadog_api_key:
            result.errors.append("DATADOG_API_KEY is not configured")

        return result
