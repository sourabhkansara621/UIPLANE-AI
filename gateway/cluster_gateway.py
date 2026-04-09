"""
gateway/cluster_gateway.py
---------------------------
Multi-cluster Kubernetes API gateway.
Loads kubeconfigs, maintains one client per cluster,
routes operations to the correct cluster based on the app registry.

Functions
---------
ClusterGateway.load_clusters()                          -> None
ClusterGateway.get_client(cluster_name)                 -> CoreV1Api
ClusterGateway.get_apps_client(cluster_name)            -> AppsV1Api
ClusterGateway.get_autoscaling_client(cluster_name)     -> AutoscalingV1Api
ClusterGateway.list_clusters()                          -> List[str]
ClusterGateway.test_connection(cluster_name)            -> bool
ClusterGateway.get_clusters_for_app(app_name, db)       -> List[ClusterRegistry]
"""

import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import tempfile
import os

import httpx

from kubernetes import client, config as k8s_config
from kubernetes.client import (
    CoreV1Api, AppsV1Api, AutoscalingV1Api,
    NetworkingV1Api, ApiException,
)
from sqlalchemy.orm import Session

from models.database import ClusterRegistry
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ClusterGateway:
    """
    Singleton gateway that holds one Kubernetes API client per cluster.
    All K8s operations go through here — never instantiate k8s clients elsewhere.
    """

    def __init__(self):
        # cluster_name -> k8s ApiClient
        self._clients: Dict[str, client.ApiClient] = {}
        self._mcp_temp_files: List[str] = []
        self._loaded = False

    # ── Initialisation ────────────────────────────────────────────────────────

    def load_clusters(self) -> None:
        """
        Load all kubeconfigs from the paths listed in settings.
        Each file may contain multiple contexts; each context becomes one cluster entry.
        Falls back to in-cluster config when running inside a Pod.
        """
        if settings.k8s_use_in_cluster:
            try:
                k8s_config.load_incluster_config()
                api_client = client.ApiClient()
                self._clients["in-cluster"] = api_client
                logger.info("Loaded in-cluster kubeconfig")
            except Exception as exc:
                logger.error("Failed to load in-cluster config: %s", exc)
            self._loaded = True
            return

        kubeconfig_paths = [
            p.strip()
            for p in settings.k8s_kubeconfig_paths.split(",")
            if p.strip()
        ]

        for kubeconfig_path in kubeconfig_paths:
            path = Path(kubeconfig_path)
            if not path.exists():
                logger.warning("Kubeconfig not found: %s", kubeconfig_path)
                continue
            try:
                self._load_contexts_from_file(str(path))
            except Exception as exc:
                logger.error("Error loading kubeconfig %s: %s", kubeconfig_path, exc)

        if settings.mcp_enabled:
            self._load_clusters_from_mcp_endpoints()

        self._loaded = True
        logger.info("Gateway ready — %d cluster(s) loaded", len(self._clients))

    def _load_contexts_from_file(self, kubeconfig_file: str) -> None:
        contexts, _active = k8s_config.list_kube_config_contexts(config_file=kubeconfig_file)
        for ctx in contexts:
            cluster_name = ctx["name"]
            k8s_config.load_kube_config(config_file=kubeconfig_file, context=cluster_name)
            self._clients[cluster_name] = client.ApiClient()
            logger.info("Loaded cluster context: %s", cluster_name)

    def _load_clusters_from_mcp_endpoints(self) -> None:
        endpoints = [e.strip() for e in settings.mcp_cluster_endpoints.split(",") if e.strip()]
        if not endpoints:
            logger.info("MCP is enabled but no MCP_CLUSTER_ENDPOINTS were provided")
            return

        timeout = max(1, settings.mcp_timeout_seconds)
        with httpx.Client(timeout=timeout) as client_http:
            for endpoint in endpoints:
                try:
                    resp = client_http.get(endpoint)
                    resp.raise_for_status()
                    payload = resp.json()
                    clusters = payload.get("clusters", []) if isinstance(payload, dict) else []
                    loaded = 0
                    for item in clusters:
                        if not isinstance(item, dict):
                            continue

                        kubeconfig_path = item.get("kubeconfig_path")
                        kubeconfig_inline = item.get("kubeconfig")
                        context_name = item.get("context")

                        if kubeconfig_path and Path(kubeconfig_path).exists():
                            self._load_contexts_from_file(str(kubeconfig_path))
                            loaded += 1
                            continue

                        if kubeconfig_inline:
                            temp_file = self._write_temp_kubeconfig(kubeconfig_inline)
                            if temp_file:
                                self._mcp_temp_files.append(temp_file)
                                if context_name:
                                    try:
                                        k8s_config.load_kube_config(config_file=temp_file, context=context_name)
                                        self._clients[context_name] = client.ApiClient()
                                        loaded += 1
                                        logger.info("Loaded MCP cluster context: %s", context_name)
                                    except Exception as exc:
                                        logger.error("Failed loading MCP inline kubeconfig context %s: %s", context_name, exc)
                                else:
                                    self._load_contexts_from_file(temp_file)
                                    loaded += 1

                    logger.info("Loaded %d MCP cluster source(s) from %s", loaded, endpoint)
                except Exception as exc:
                    logger.error("Failed to load MCP clusters from %s: %s", endpoint, exc)

    def _write_temp_kubeconfig(self, kubeconfig_text: str) -> Optional[str]:
        try:
            fd, path = tempfile.mkstemp(prefix="mcp-kubeconfig-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(kubeconfig_text)
            return path
        except Exception as exc:
            logger.error("Failed writing temp kubeconfig from MCP payload: %s", exc)
            return None

    # ── Client accessors ──────────────────────────────────────────────────────

    def _get_api_client(self, cluster_name: str) -> client.ApiClient:
        """Return the raw ApiClient for a cluster, or raise KeyError."""
        if cluster_name not in self._clients:
            raise KeyError(
                f"Cluster '{cluster_name}' not found. "
                f"Available: {list(self._clients.keys())}"
            )
        return self._clients[cluster_name]

    def get_core_client(self, cluster_name: str) -> CoreV1Api:
        """Return CoreV1Api (pods, namespaces, services, configmaps...)."""
        return CoreV1Api(self._get_api_client(cluster_name))

    def get_apps_client(self, cluster_name: str) -> AppsV1Api:
        """Return AppsV1Api (deployments, replicasets, statefulsets...)."""
        return AppsV1Api(self._get_api_client(cluster_name))

    def get_autoscaling_client(self, cluster_name: str) -> AutoscalingV1Api:
        """Return AutoscalingV1Api (HPA)."""
        return AutoscalingV1Api(self._get_api_client(cluster_name))

    def get_networking_client(self, cluster_name: str) -> NetworkingV1Api:
        """Return NetworkingV1Api (Ingress, NetworkPolicy)."""
        return NetworkingV1Api(self._get_api_client(cluster_name))

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_clusters(self) -> List[str]:
        """Return names of all loaded clusters."""
        return list(self._clients.keys())

    def test_connection(self, cluster_name: str) -> bool:
        """Ping a cluster. Returns True if reachable."""
        try:
            core = self.get_core_client(cluster_name)
            core.list_namespace(_request_timeout=5)
            return True
        except (ApiException, KeyError, Exception):
            return False

    def get_clusters_for_app(
        self, app_name: str, db: Session
    ) -> List[ClusterRegistry]:
        """
        Look up the cluster registry and return all entries for an app.
        This is how the AI knows "payments-api lives in gke-prod-us-east".
        """
        return (
            db.query(ClusterRegistry)
            .filter(
                ClusterRegistry.app_name == app_name,
                ClusterRegistry.is_active == True,
            )
            .all()
        )

    def get_cluster_for_app_env(
        self, app_name: str, environment: str, db: Session
    ) -> Optional[ClusterRegistry]:
        """Return the single registry entry for app + environment."""
        return (
            db.query(ClusterRegistry)
            .filter(
                ClusterRegistry.app_name == app_name,
                ClusterRegistry.environment == environment,
                ClusterRegistry.is_active == True,
            )
            .first()
        )

    def get_connected_count(self) -> int:
        """Return number of clusters with loaded clients."""
        return len(self._clients)


# ── Module-level singleton ────────────────────────────────────────────────────

_gateway_instance: Optional[ClusterGateway] = None


def get_gateway() -> ClusterGateway:
    """
    FastAPI dependency / module accessor.
    Returns the singleton ClusterGateway, initialising it on first call.
    """
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = ClusterGateway()
        _gateway_instance.load_clusters()
    return _gateway_instance
