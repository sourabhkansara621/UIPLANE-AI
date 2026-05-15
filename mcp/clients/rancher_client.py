"""Rancher MCP client.

Two discovery modes (tried in order):
1. Rancher API  — if MCP_RANCHER_API_URL + MCP_RANCHER_API_TOKEN are set.
   Uses the Rancher v3 REST API to list all active clusters and downloads
   a fresh kubeconfig for each one via the generateKubeconfig action.
2. Kubeconfig paths — if MCP_RANCHER_KUBECONFIG_PATHS is set.
   Reads local kubeconfig files (same pattern as GKE / EKS / AKS clients).
"""

import logging
import re
from typing import List, Tuple

import httpx

from mcp.clients.base import MCPClient
from mcp.schemas import MCPClientResult, MCPCluster
from config.settings import get_settings

logger = logging.getLogger(__name__)


RANCHER_TOKEN_PATTERN = re.compile(r"^token-[^:]+:.+$")


class RancherClient(MCPClient):
    name = "rancher"

    # ── Public entry point ────────────────────────────────────────────────────

    def collect(self) -> MCPClientResult:
        settings = get_settings()
        result = MCPClientResult(client=self.name)

        api_targets = self._parse_api_targets(settings)

        if api_targets:
            for api_url, api_token in api_targets:
                self._collect_from_api(api_url, api_token, settings, result)
        else:
            self._collect_from_kubeconfig_paths(settings, result)

        return result

    @staticmethod
    def _parse_api_targets(settings) -> List[Tuple[str, str]]:
        """
        Parse Rancher API targets from environment.

        Supported inputs:
        - Dedicated pairs:
          MCP_RANCHER_API_URL_SANDBOX + MCP_RANCHER_API_TOKEN_SANDBOX
          MCP_RANCHER_API_URL_EKS + MCP_RANCHER_API_TOKEN_EKS
        - MCP_RANCHER_ENDPOINTS: comma/newline-separated entries in format
          "https://rancher.example.com|token-xxx:yyy"
        - Legacy fallback: MCP_RANCHER_API_URL + MCP_RANCHER_API_TOKEN
        """
        targets: List[Tuple[str, str]] = []

        def _append_target(url: str, token: str) -> None:
            api_url = (url or "").strip().rstrip("/")
            api_token = (token or "").strip()
            if not api_url or not api_token:
                return
            if not RANCHER_TOKEN_PATTERN.match(api_token):
                logger.info(
                    "Skipping Rancher endpoint '%s' because token format is invalid or placeholder",
                    api_url,
                )
                return
            item = (api_url, api_token)
            if item not in targets:
                targets.append(item)

        raw = settings.mcp_rancher_endpoints or ""
        if raw.strip():
            normalized = raw.replace("\r\n", "\n").replace("\n", ",")
            for item in normalized.split(","):
                part = item.strip()
                if not part:
                    continue
                if "|" not in part:
                    logger.warning(
                        "Invalid MCP_RANCHER_ENDPOINTS item '%s' (expected url|token)",
                        part,
                    )
                    continue
                url, token = part.split("|", 1)
                _append_target(url, token)

        # Prefer explicit separated pairs when present.
        _append_target(settings.mcp_rancher_api_url_sandbox, settings.mcp_rancher_api_token_sandbox)
        _append_target(settings.mcp_rancher_api_url_eks, settings.mcp_rancher_api_token_eks)

        if targets:
            return targets

        _append_target(settings.mcp_rancher_api_url, settings.mcp_rancher_api_token)

        return targets

    # ── Mode 1: Rancher API ───────────────────────────────────────────────────

    def _collect_from_api(
        self, api_url: str, api_token: str, settings, result: MCPClientResult
    ) -> None:
        """Discover all active clusters via the Rancher v3 REST API."""
        timeout = max(1, settings.mcp_timeout_seconds)
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=timeout, verify=False) as http:  # noqa: S501
                clusters = self._list_rancher_clusters(http, api_url, headers, result)
                for cluster_id, cluster_name, region in clusters:
                    kubeconfig = self._generate_kubeconfig(
                        http, api_url, headers, cluster_id, cluster_name, result
                    )
                    if kubeconfig:
                        result.clusters.append(
                            MCPCluster(
                                context=cluster_name,
                                provider="rancher",
                                region=region,
                                kubeconfig=kubeconfig,
                                source_client=self.name,
                            )
                        )
        except httpx.ConnectError as exc:
            msg = f"Cannot reach Rancher API at {api_url}: {exc}"
            logger.warning(msg)
            result.errors.append(msg)
        except Exception as exc:  # pragma: no cover
            msg = f"Unexpected error talking to Rancher API: {exc}"
            logger.exception(msg)
            result.errors.append(msg)

    def _list_rancher_clusters(
        self,
        http: httpx.Client,
        api_url: str,
        headers: dict,
        result: MCPClientResult,
    ) -> List[tuple]:
        """Return list of (cluster_id, cluster_name, region) tuples for active clusters."""
        resp = http.get(f"{api_url}/v3/clusters", headers=headers)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("data", [])
        clusters: List[tuple] = []
        for item in items:
            state = item.get("state", "")
            if state not in ("active", ""):
                logger.debug("Skipping Rancher cluster %s (state=%s)", item.get("name"), state)
                continue

            cluster_id = item.get("id", "")
            cluster_name = item.get("name", cluster_id)
            region = self._detect_region(item)
            clusters.append((cluster_id, cluster_name, region))

        return clusters

    def _generate_kubeconfig(
        self,
        http: httpx.Client,
        api_url: str,
        headers: dict,
        cluster_id: str,
        cluster_name: str,
        result: MCPClientResult,
    ) -> str:
        """Call generateKubeconfig action and return the YAML string."""
        try:
            resp = http.post(
                f"{api_url}/v3/clusters/{cluster_id}?action=generateKubeconfig",
                headers=headers,
                json={},
            )
            resp.raise_for_status()
            kubeconfig_yaml = resp.json().get("config", "")
            if not kubeconfig_yaml:
                result.errors.append(f"Empty kubeconfig returned for cluster {cluster_name}")
            return kubeconfig_yaml
        except Exception as exc:
            msg = f"Failed to generate kubeconfig for {cluster_name}: {exc}"
            logger.warning(msg)
            result.errors.append(msg)
            return ""

    @staticmethod
    def _detect_region(cluster_item: dict) -> str:
        """
        Best-effort region detection from Rancher cluster metadata.
        Falls back to MCP_RANCHER_REGION setting, then 'on-prem'.
        """
        settings = get_settings()
        default_region = settings.mcp_rancher_region or "on-prem"

        # Check annotations / labels for a region hint
        annotations = cluster_item.get("annotations") or {}
        labels = cluster_item.get("labels") or {}

        for key in ("region", "topology.kubernetes.io/region", "failure-domain.beta.kubernetes.io/region"):
            if labels.get(key):
                return labels[key]
            if annotations.get(key):
                return annotations[key]

        # Infer from driver
        driver = (cluster_item.get("driver") or "").lower()
        if driver in ("eks", "gke", "aks"):
            return "cloud"
        if driver in ("rke", "rke2", "k3s", "imported"):
            return default_region

        return default_region

    # ── Mode 2: kubeconfig paths ──────────────────────────────────────────────

    def _collect_from_kubeconfig_paths(self, settings, result: MCPClientResult) -> None:
        """Load clusters from local kubeconfig file paths."""
        paths = [
            p.strip()
            for p in settings.mcp_rancher_kubeconfig_paths.split(",")
            if p.strip()
        ]

        if not paths:
            logger.debug(
                "RancherClient: no API credentials and no kubeconfig paths configured — skipping."
            )
            return

        for path in paths:
            context_name = path.replace("\\", "/").split("/")[-1]
            context = f"rancher::{context_name}"
            result.clusters.append(
                MCPCluster(
                    context=context,
                    provider="rancher",
                    region=get_settings().mcp_rancher_region or "on-prem",
                    kubeconfig_path=path,
                    source_client=self.name,
                )
            )
