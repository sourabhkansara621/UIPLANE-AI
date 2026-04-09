"""GKE MCP client using configured kubeconfig paths."""

from mcp.clients.base import MCPClient
from mcp.schemas import MCPClientResult, MCPCluster
from config.settings import get_settings


class GKEClient(MCPClient):
    name = "gke"

    def collect(self) -> MCPClientResult:
        settings = get_settings()
        result = MCPClientResult(client=self.name)
        paths = [p.strip() for p in settings.mcp_gke_kubeconfig_paths.split(",") if p.strip()]

        for path in paths:
            context = f"gke::{path.split('/')[-1].split('\\\\')[-1]}"
            result.clusters.append(
                MCPCluster(
                    context=context,
                    provider="gke",
                    region="unknown",
                    kubeconfig_path=path,
                    source_client=self.name,
                )
            )

        return result
