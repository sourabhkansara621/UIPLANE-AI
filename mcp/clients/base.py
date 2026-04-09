"""Base contract for MCP clients."""

from abc import ABC, abstractmethod

from mcp.schemas import MCPClientResult


class MCPClient(ABC):
    """Abstract MCP client contract."""

    name: str

    @abstractmethod
    def collect(self) -> MCPClientResult:
        """Collect cluster or observability records from one provider."""
