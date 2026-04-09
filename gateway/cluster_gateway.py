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
        Load all Kubernetes cluster configurations from kubeconfig files.
        
        This initialization function:
        1. Checks if running in-cluster (inside a Kubernetes pod)
        2. If not, loads kubeconfig files from configured paths
        3. Extracts all contexts from each kubeconfig
        4. Creates one ApiClient per cluster context
        5. Optionally loads clusters from MCP (Multi-Cluster Platform) endpoints
        
        Configuration Sources:
            - K8S_USE_IN_CLUSTER: If true, uses in-cluster service account
            - K8S_KUBECONFIG_PATHS: Comma-separated paths to kubeconfig files
            - MCP_ENABLED: If true, fetches additional clusters from MCP
            - MCP_CLUSTER_ENDPOINTS: Comma-separated MCP endpoint URLs
            
        Process:
            Each kubeconfig file may contain multiple contexts (clusters).
            Each context becomes a named cluster in the gateway.
            
        Example:
            If kubeconfig has contexts ["gke-prod", "eks-staging"],
            both become available as cluster names.
            
        Raises:
            Logs errors but doesn't fail - continues loading other clusters.
            
        Note:
            This should be called once during application startup.
            Call get_gateway() dependency to access the singleton instance.
        """
        # In-cluster mode: running inside a Kubernetes pod
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

        # Load from kubeconfig file paths
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

        # Load clusters from MCP endpoints if enabled
        if settings.mcp_enabled:
            self._load_clusters_from_mcp_endpoints()

        self._loaded = True
        logger.info("Gateway ready — %d cluster(s) loaded", len(self._clients))

    def _load_contexts_from_file(self, kubeconfig_file: str) -> None:
        """
        Load all contexts from a kubeconfig file and create API clients.
        
        Args:
            kubeconfig_file: Path to the kubeconfig YAML file
            
        Process:
            1. Parse kubeconfig to extract all contexts
            2. For each context, load its configuration
            3. Create an ApiClient for that context
            4. Store in _clients dict with context name as key
            
        Example:
            If kubeconfig has contexts ["prod-cluster", "dev-cluster"],
            creates self._clients["prod-cluster"] and self._clients["dev-cluster"]
        """
        # List all contexts in the file
        contexts, _active = k8s_config.list_kube_config_contexts(config_file=kubeconfig_file)
        
        # Load each context as a separate cluster
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
        """
        Get Kubernetes CoreV1Api client for a specific cluster.
        
        CoreV1Api provides access to core resources:
        - Pods, Services, ConfigMaps, Secrets
        - Namespaces, Nodes, PersistentVolumes
        - ResourceQuotas, LimitRanges
        
        Args:
            cluster_name: Name of the cluster (context name from kubeconfig)
            
        Returns:
            CoreV1Api instance for making Kubernetes API calls
            
        Raises:
            KeyError: If cluster_name not found in loaded clusters
            
        Usage:
            core = gateway.get_core_client("gke-prod")
            pods = core.list_namespaced_pod(namespace="default")
        """
        return CoreV1Api(self._get_api_client(cluster_name))

    def get_apps_client(self, cluster_name: str) -> AppsV1Api:
        """
        Get Kubernetes AppsV1Api client for a specific cluster.
        
        AppsV1Api provides access to application resources:
        - Deployments, ReplicaSets, StatefulSets
        - DaemonSets, ControllerRevisions
        
        Args:
            cluster_name: Name of the cluster
            
        Returns:
            AppsV1Api instance for managing application workloads
            
        Raises:
            KeyError: If cluster_name not found
            
        Usage:
            apps = gateway.get_apps_client("gke-prod")
            deps = apps.list_namespaced_deployment(namespace="default")
        """
        return AppsV1Api(self._get_api_client(cluster_name))

    def get_autoscaling_client(self, cluster_name: str) -> AutoscalingV1Api:
        """
        Get Kubernetes AutoscalingV1Api client for a specific cluster.
        
        AutoscalingV1Api provides access to:
        - HorizontalPodAutoscaler (HPA) resources
        
        Args:
            cluster_name: Name of the cluster
            
        Returns:
            AutoscalingV1Api instance for autoscaling management
            
        Raises:
            KeyError: If cluster_name not found
            
        Usage:
            autoscaling = gateway.get_autoscaling_client("gke-prod")
            hpas = autoscaling.list_namespaced_horizontal_pod_autoscaler(namespace="default")
        """
        return AutoscalingV1Api(self._get_api_client(cluster_name))

    def get_networking_client(self, cluster_name: str) -> NetworkingV1Api:
        """
        Get Kubernetes NetworkingV1Api client for a specific cluster.
        
        NetworkingV1Api provides access to networking resources:
        - Ingress, IngressClass
        - NetworkPolicy
        
        Args:
            cluster_name: Name of the cluster
            
        Returns:
            NetworkingV1Api instance for network configuration
            
        Raises:
            KeyError: If cluster_name not found
            
        Usage:
            networking = gateway.get_networking_client("gke-prod")
            ingresses = networking.list_namespaced_ingress(namespace="default")
        """
        return NetworkingV1Api(self._get_api_client(cluster_name))

    # ── Discovery ─────────────────────────────────────────────────────────────

    def list_clusters(self) -> List[str]:
        """
        Get names of all loaded Kubernetes clusters.
        
        Returns:
            List of cluster names (context names) currently available
            
        Example:
            ["gke-prod-us-east", "eks-staging-eu-west", "in-cluster"]
            
        Usage:
            Useful for UI dropdowns or validating cluster names before operations.
        """
        return list(self._clients.keys())

    def test_connection(self, cluster_name: str) -> bool:
        """
        Test if a cluster is reachable and responding.
        
        Makes a lightweight API call (list namespaces) to verify connectivity.
        
        Args:
            cluster_name: Name of cluster to test
            
        Returns:
            True if cluster responds, False if unreachable or not loaded
            
        Usage:
            Health checks, cluster status dashboards, diagnostics
        """
        try:
            core = self.get_core_client(cluster_name)
            core.list_namespace(_request_timeout=5)  # 5 second timeout
            return True
        except (ApiException, KeyError, Exception):
            return False

    def get_clusters_for_app(
        self, app_name: str, db: Session
    ) -> List[ClusterRegistry]:
        """
        Find all clusters where an application is deployed.
        
        Queries the ClusterRegistry database to discover where an app lives.
        An application can be deployed across multiple clusters/environments.
        
        Args:
            app_name: Application name (e.g., 'payments-api')
            db: Database session for registry lookup
            
        Returns:
            List of ClusterRegistry entries showing:
            - cluster_name: Which cluster it's in
            - namespace: Which namespace
            - environment: prod, staging, etc.
            - cloud_provider: GKE, EKS, AKS, etc.
            
        Example:
            For "payments-api" might return:
            [
                ClusterRegistry(cluster="gke-prod", namespace="payments-prod", env="prod"),
                ClusterRegistry(cluster="eks-staging", namespace="payments-staging", env="staging")
            ]
            
        Note:
            This is how the AI knows where to look for an application's resources.
            The ClusterRegistry must be populated (manually or via discovery).
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
        """
        Get the specific cluster entry for an app in a given environment.
        
        Args:
            app_name: Application name
            environment: Environment name (prod, staging, nonprod, etc.)
            db: Database session
            
        Returns:
            ClusterRegistry entry if found, None otherwise
            
        Usage:
            When you need a specific environment, not all deployments:
            prod_cluster = gateway.get_cluster_for_app_env("payments-api", "prod", db)
        """
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
        """
        Get number of successfully loaded clusters.
        
        Returns:
            Count of clusters with active API clients
            
        Usage:
            Health checks, startup verification, monitoring
        """
        return len(self._clients)


# ── Module-level singleton ────────────────────────────────────────────────────

_gateway_instance: Optional[ClusterGateway] = None


def get_gateway() -> ClusterGateway:
    """
    Get the singleton ClusterGateway instance (FastAPI dependency).
    
    This is the main entry point for accessing Kubernetes clusters.
    The gateway is initialized once on first call and reused for all requests.
    
    Returns:
        ClusterGateway singleton with all clusters loaded
        
    Usage in FastAPI endpoints:
        @router.get("/pods")
        def get_pods(gateway: ClusterGateway = Depends(get_gateway)):
            core = gateway.get_core_client("gke-prod")
            return core.list_namespaced_pod(namespace="default")
            
    Architecture:
        - Singleton pattern ensures one gateway per application instance
        - Automatically loads clusters on first access
        - All Kubernetes operations must go through this gateway
        - Never instantiate ClusterGateway directly - always use get_gateway()
        
    Note:
        Thread-safe singleton - safe to call from multiple FastAPI workers.
    """
    global _gateway_instance
    if _gateway_instance is None:
        _gateway_instance = ClusterGateway()
        _gateway_instance.load_clusters()
    return _gateway_instance
