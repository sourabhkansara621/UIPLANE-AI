"""
capabilities/k8s_reader.py
---------------------------
All READ operations against Kubernetes clusters.
Every function takes a cluster_name + namespace and returns
clean Pydantic models — no raw K8s objects leak out.

Functions
---------
list_namespaces(cluster_name, gateway)                          -> List[NamespaceInfo]
list_nodes(cluster_name, gateway)                               -> List[dict]
list_pods(cluster_name, namespace, gateway)                     -> List[PodInfo]
get_resource_quota(cluster_name, namespace, gateway)            -> Optional[ResourceQuotaInfo]
list_deployments(cluster_name, namespace, gateway)              -> List[DeploymentInfo]
get_deployment_manifest(cluster_name, namespace, deployment_name, gateway) -> Dict[str, Any]
list_services(cluster_name, namespace, gateway)                 -> List[dict]
list_secrets(cluster_name, namespace, gateway)                  -> List[dict]
get_hpa(cluster_name, namespace, gateway)                       -> List[HPAInfo]
list_ingresses(cluster_name, namespace, gateway)                -> List[IngressInfo]
get_pod_logs(cluster_name, namespace, pod_name, gateway, ...)   -> str
describe_pod(cluster_name, namespace, pod_name, gateway)        -> dict
describe_deployment(cluster_name, namespace, deployment_name, gateway) -> dict
describe_ingress(cluster_name, namespace, ingress_name, gateway) -> dict
describe_service(cluster_name, namespace, service_name, gateway) -> dict
describe_secret_metadata(cluster_name, namespace, secret_name, gateway) -> dict
get_k8s_version(cluster_name, gateway)                          -> str
check_network_policy(cluster_name, namespace, gateway)          -> List[dict]
"""

import logging
import re
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.client import ApiException, ApiClient
from kubernetes.client.models import (
    V1Pod, V1Namespace, V1Deployment,
    V1HorizontalPodAutoscaler, V1Ingress,
)

from models.schemas import (
    PodInfo, NamespaceInfo, ResourceQuotaInfo,
    DeploymentInfo, HPAInfo, IngressInfo,
)
from gateway.cluster_gateway import ClusterGateway

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age(timestamp) -> str:
    """
    Convert a Kubernetes timestamp to a human-readable age string.
    
    Args:
        timestamp: Kubernetes datetime object (typically from metadata.creation_timestamp)
        
    Returns:
        Human-readable age string like "5d", "3h", "45m"
        Returns "unknown" if timestamp is None
        
    Examples:
        - 5 days old: "5d"
        - 3 hours old: "3h"
        - 45 minutes old: "45m"
        - Less than a minute old: "0m"
    """
    if timestamp is None:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = now - timestamp.replace(tzinfo=timezone.utc)
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}d"
    if hours > 0:
        return f"{hours}h"
    return f"{delta.seconds // 60}m"


def _parse_resource(value: Optional[str]) -> Optional[float]:
    """
    Parse Kubernetes resource string to a numeric value.
    
    Handles Kubernetes resource formats:
    - CPU: "500m" (millicores), "2" (cores)
    - Memory: "512Mi", "2Gi", "1024Ki"
    
    Args:
        value: Kubernetes resource string (e.g., "500m", "2Gi")
        
    Returns:
        Parsed numeric value, or None if invalid/missing
        
    Examples:
        - "500m" -> 0.5 (millicores to cores)
        - "2Gi" -> 2147483648.0 (bytes)
        - "512Mi" -> 536870912.0 (bytes)
        - "1024" -> 1024.0
    """
    if value is None:
        return None
    value = str(value).strip()
    
    # CPU millicores (e.g., "500m" = 0.5 cores)
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    
    # Memory units
    if value.endswith("Ki"):
        return float(value[:-2]) * 1024
    if value.endswith("Mi"):
        return float(value[:-2]) * 1024 ** 2
    if value.endswith("Gi"):
        return float(value[:-2]) * 1024 ** 3
    
    # Plain number
    try:
        return float(value)
    except ValueError:
        return None


# ── Namespace operations ──────────────────────────────────────────────────────

def list_namespaces(
    cluster_name: str,
    gateway: ClusterGateway,
    app_name: Optional[str] = None,
) -> List[NamespaceInfo]:
    """
    List all namespaces in a Kubernetes cluster.
    
    Optionally filter by application using label selectors.
    
    Args:
        cluster_name: Name of the cluster to query
        gateway: ClusterGateway for API access
        app_name: Optional - filter to namespaces labeled with app=<app_name>
        
    Returns:
        List of NamespaceInfo objects containing:
        - name: Namespace name
        - status: Active/Terminating
        - app_name: Value from app label
        - cluster_name: Which cluster it's from
        - environment: Value from env label
        - labels: All namespace labels
        
    Example:
        namespaces = list_namespaces("gke-prod", gateway)
        # Returns all namespaces in gke-prod cluster
        
        app_namespaces = list_namespaces("gke-prod", gateway, app_name="payments-api")
        # Returns only namespaces labeled with app=payments-api
    """
    try:
        core = gateway.get_core_client(cluster_name)
        # Build label selector if app_name provided
        label_selector = f"app={app_name}" if app_name else None
        ns_list = core.list_namespace(label_selector=label_selector)
        
        results = []
        for ns in ns_list.items:
            labels = ns.metadata.labels or {}
            results.append(
                NamespaceInfo(
                    name=ns.metadata.name,
                    status=ns.status.phase,
                    app_name=labels.get("app", app_name or ""),
                    cluster_name=cluster_name,
                    environment=labels.get("env", "unknown"),
                    labels=labels,
                )
            )
        return results
    except ApiException as exc:
        logger.error("list_namespaces failed on %s: %s", cluster_name, exc)
        return []


def list_nodes(
    cluster_name: str,
    gateway: ClusterGateway,
) -> List[Dict[str, Any]]:
    """List node status summary for a cluster."""
    try:
        core = gateway.get_core_client(cluster_name)
        node_list = core.list_node()
        results: List[Dict[str, Any]] = []
        for node in node_list.items:
            conditions = node.status.conditions or []
            ready_condition = next((c for c in conditions if c.type == "Ready"), None)
            ready = (ready_condition.status == "True") if ready_condition else False
            roles = []
            labels = node.metadata.labels or {}
            for key, value in labels.items():
                if key.startswith("node-role.kubernetes.io/"):
                    role = key.split("/")[-1]
                    roles.append(role if role else "worker")
            if not roles:
                roles = ["worker"]

            alloc = node.status.allocatable or {}
            results.append(
                {
                    "name": node.metadata.name,
                    "status": "Ready" if ready else "NotReady",
                    "roles": roles,
                    "kubelet_version": node.status.node_info.kubelet_version if node.status.node_info else "unknown",
                    "os_image": node.status.node_info.os_image if node.status.node_info else "unknown",
                    "container_runtime": node.status.node_info.container_runtime_version if node.status.node_info else "unknown",
                    "cpu_allocatable": alloc.get("cpu"),
                    "memory_allocatable": alloc.get("memory"),
                    "pods_allocatable": alloc.get("pods"),
                    "age": _age(node.metadata.creation_timestamp),
                }
            )
        return results
    except ApiException as exc:
        logger.error("list_nodes failed on %s: %s", cluster_name, exc)
        return []


# ── Pod operations ────────────────────────────────────────────────────────────

def list_pods(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
    label_selector: Optional[str] = None,
) -> List[PodInfo]:
    """
    List all pods in a namespace with detailed status information.
    
    Returns clean, structured pod data suitable for display or further processing.
    No raw Kubernetes objects leak out - everything is converted to PodInfo schema.
    
    Args:
        cluster_name: Name of the Kubernetes cluster
        namespace: Namespace to query
        gateway: ClusterGateway for API access
        label_selector: Optional Kubernetes label selector (e.g., "app=payments-api,tier=backend")
        
    Returns:
        List of PodInfo objects containing:
        - name, namespace, status (Running/Pending/Failed/etc.)
        - ready: "2/2" (ready containers / total containers)
        - restarts: Total restart count across all containers
        - cpu_request, memory_request: Resource requests
        - image: Container image (from first container)
        - node: Which node the pod is running on
        - age: Human-readable age ("5d", "3h", "45m")
        
    Example:
        # Get all pods in a namespace
        pods = list_pods("gke-prod", "default", gateway)
        
        # Get pods with specific label
        pods = list_pods("gke-prod", "default", gateway, label_selector="app=nginx")
        
    Note:
        Returns empty list on errors (logs error but doesn't raise exception).
    """
    try:
        core = gateway.get_core_client(cluster_name)
        pod_list = core.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
        # Convert each pod to clean PodInfo schema
        return [_pod_to_info(pod, cluster_name) for pod in pod_list.items]
    except ApiException as exc:
        logger.error("list_pods failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def _pod_to_info(pod: V1Pod, cluster_name: str) -> PodInfo:
    """
    Convert a raw Kubernetes V1Pod object into a clean PodInfo schema.
    
    This internal function extracts and formats key pod information:
    - Container status and readiness
    - Resource requests (CPU/memory from first container)
    - Restart counts (summed across all containers)
    - Current node assignment
    - Age calculation
    
    Args:
        pod: Raw V1Pod object from Kubernetes API
        cluster_name: Name of source cluster (added to PodInfo)
        
    Returns:
        PodInfo object with all relevant pod details in a clean format
        
    Note:
        This is an internal helper - use list_pods() for public API.
    """
    containers = pod.spec.containers or []
    image = containers[0].image if containers else "unknown"

    # Count restarts across all containers
    restarts = 0
    ready_containers = 0
    total_containers = len(containers)
    if pod.status.container_statuses:
        for cs in pod.status.container_statuses:
            restarts += cs.restart_count or 0
            if cs.ready:
                ready_containers += 1

    # Resource requests from first container
    cpu_req = mem_req = None
    if containers and containers[0].resources and containers[0].resources.requests:
        reqs = containers[0].resources.requests
        cpu_req = reqs.get("cpu")
        mem_req = reqs.get("memory")

    return PodInfo(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        status=pod.status.phase or "Unknown",
        ready=f"{ready_containers}/{total_containers}",
        restarts=restarts,
        cpu_request=cpu_req,
        memory_request=mem_req,
        image=image,
        node=pod.spec.node_name,
        age=_age(pod.metadata.creation_timestamp),
    )


def get_pod_logs(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    gateway: ClusterGateway,
    tail_lines: int = 100,
    container: Optional[str] = None,
    previous: bool = False,
) -> str:
    """
    Fetch recent logs from a pod container.
    
    Equivalent to: kubectl logs <pod_name> --tail=<tail_lines>
    
    Args:
        cluster_name: Name of the Kubernetes cluster
        namespace: Namespace containing the pod
        pod_name: Name of the pod
        gateway: ClusterGateway for API access
        tail_lines: Number of recent log lines to fetch (default: 100)
        container: Specific container name (optional - uses first container if not specified)
        previous: If True, get logs from previous (crashed) container instance
        
    Returns:
        Log output as a string, or error message if fetch fails
        Returns "(no log output)" if pod has no logs
        
    Use Cases:
        - Debugging application errors
        - Checking startup logs
        - Investigating crashes (use previous=True)
        
    Example:
        # Get last 100 lines from a pod
        logs = get_pod_logs("gke-prod", "default", "nginx-abc123", gateway)
        
        # Get logs from crashed container
        crash_logs = get_pod_logs("gke-prod", "default", "nginx-abc123", gateway, previous=True)
        
        # Get last 500 lines
        logs = get_pod_logs("gke-prod", "default", "nginx-abc123", gateway, tail_lines=500)
    """
    try:
        core = gateway.get_core_client(cluster_name)
        logs = core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines,
            container=container,
            previous=previous,  # Get logs from previous crashed container
        )
        return logs or "(no log output)"
    except ApiException as exc:
        logger.error("get_pod_logs failed: %s", exc)
        return f"Error fetching logs: {exc.reason}"


def describe_pod(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """
    Get detailed pod information equivalent to 'kubectl describe pod'.
    
    Returns comprehensive pod details including:
    - Container states and reasons
    - Resource requests and limits
    - Events and conditions
    - Volume mounts and environment variables
    
    Args:
        cluster_name: Name of the Kubernetes cluster
        namespace: Namespace containing the pod
        pod_name: Name of the pod to describe
        gateway: ClusterGateway for API access
        
    Returns:
        Dictionary containing:
        - name, namespace, status, ip, node
        - containers: List of container details with:
            - name, image, state, restart_count
            - resources (requests/limits)
            - state_reason, state_message
            - last_state (for crashed containers)
        - conditions: Pod conditions (Ready, Initialized, etc.)
        - volumes: Volume mounts
        - node_name, host_ip, pod_ip
        - age, labels, annotations
        
    Example:
        info = describe_pod("gke-prod", "default", "nginx-abc123", gateway)
        print(f"Pod Status: {info['status']}")
        print(f"Containers: {len(info['containers'])}")
        for container in info['containers']:
            print(f"  {container['name']}: {container['state']}")
            
    Use Cases:
        - Debugging pod startup issues
        - Investigating container crashes
        - Checking resource allocations
        - Understanding pod scheduling decisions
    """
    try:
        core = gateway.get_core_client(cluster_name)
        api_client = ApiClient()
        pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        container_statuses = {cs.name: cs for cs in (pod.status.container_statuses or [])}
        containers = []
        for c in (pod.spec.containers or []):
            cs = container_statuses.get(c.name)
            state = "Unknown"
            state_reason = None
            state_message = None
            last_state = None
            if cs and cs.state:
                if cs.state.running:
                    state = "Running"
                    state_reason = "Running"
                elif cs.state.waiting:
                    state_reason = cs.state.waiting.reason or "Unknown"
                    state_message = cs.state.waiting.message
                    state = f"Waiting({state_reason})"
                elif cs.state.terminated:
                    state_reason = cs.state.terminated.reason or "Unknown"
                    state_message = cs.state.terminated.message
                    state = f"Terminated({state_reason})"
                if cs.last_state:
                    if cs.last_state.terminated:
                        last_state = f"Terminated({cs.last_state.terminated.reason or 'Unknown'})"
                    elif cs.last_state.waiting:
                        last_state = f"Waiting({cs.last_state.waiting.reason or 'Unknown'})"
                    elif cs.last_state.running:
                        last_state = "Running"
            elif (pod.status.phase or "").lower() == "pending":
                # Early pod lifecycle often has no container_statuses yet.
                state = "Waiting(ContainerCreating)"
                state_reason = "ContainerCreating"
            containers.append(
                {
                    "name": c.name,
                    "image": c.image,
                    "ready": str(cs.ready) if cs else "False",
                    "restart_count": str(cs.restart_count) if cs else "0",
                    "state": state,
                    "state_reason": state_reason,
                    "state_message": state_message,
                    "last_state": last_state,
                    "container_id": cs.container_id if cs else None,
                    "resources": {
                        "requests": (c.resources.requests if c.resources else {}) or {},
                        "limits": (c.resources.limits if c.resources else {}) or {},
                    },
                }
            )

        conditions = []
        if pod.status.conditions:
            conditions = [
                {"type": c.type, "status": c.status, "reason": c.reason}
                for c in pod.status.conditions
            ]
        events = _get_pod_events(cluster_name, namespace, pod_name, gateway)
        owner_refs = []
        for r in (pod.metadata.owner_references or []):
            owner_refs.append(
                {
                    "kind": r.kind,
                    "name": r.name,
                    "uid": r.uid,
                    "controller": r.controller,
                }
            )

        init_containers = []
        init_statuses = {cs.name: cs for cs in (pod.status.init_container_statuses or [])}
        for c in (pod.spec.init_containers or []):
            cs = init_statuses.get(c.name)
            init_state = "Unknown"
            if cs and cs.state:
                if cs.state.running:
                    init_state = "Running"
                elif cs.state.waiting:
                    init_state = f"Waiting({cs.state.waiting.reason or 'Unknown'})"
                elif cs.state.terminated:
                    init_state = f"Terminated({cs.state.terminated.reason or 'Unknown'})"
            init_containers.append(
                {
                    "name": c.name,
                    "image": c.image,
                    "ready": str(cs.ready) if cs else "False",
                    "restart_count": str(cs.restart_count) if cs else "0",
                    "state": init_state,
                    "resources": {
                        "requests": (c.resources.requests if c.resources else {}) or {},
                        "limits": (c.resources.limits if c.resources else {}) or {},
                    },
                }
            )

        return {
            "name": pod.metadata.name,
            "uid": pod.metadata.uid,
            "creation_timestamp": str(pod.metadata.creation_timestamp) if pod.metadata.creation_timestamp else None,
            "owner_references": owner_refs,
            "namespace": pod.metadata.namespace,
            "node": pod.spec.node_name,
            "node_selector": pod.spec.node_selector or {},
            "priority_class_name": pod.spec.priority_class_name,
            "restart_policy": pod.spec.restart_policy,
            "scheduler_name": pod.spec.scheduler_name,
            "host_network": bool(pod.spec.host_network),
            "dns_policy": pod.spec.dns_policy,
            "service_account": pod.spec.service_account_name,
            "status": pod.status.phase,
            "reason": pod.status.reason,
            "message": pod.status.message,
            "pod_ip": pod.status.pod_ip,
            "host_ip": pod.status.host_ip,
            "qos_class": pod.status.qos_class,
            "start_time": str(pod.status.start_time) if pod.status.start_time else None,
            "conditions": conditions,
            "events": events,
            "init_containers": init_containers,
            "containers": containers,
            "volumes": api_client.sanitize_for_serialization(pod.spec.volumes or []),
            "tolerations": api_client.sanitize_for_serialization(pod.spec.tolerations or []),
            "labels": pod.metadata.labels or {},
            "annotations": pod.metadata.annotations or {},
            "raw_pod": api_client.sanitize_for_serialization(pod),
        }
    except ApiException as exc:
        return {"error": str(exc.reason)}


def describe_deployment(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """Return full deployment object as a serializable dict."""
    try:
        apps = gateway.get_apps_client(cluster_name)
        api_client = ApiClient()
        dep = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        return api_client.sanitize_for_serialization(dep)
    except ApiException as exc:
        return {"error": str(exc.reason)}


def describe_ingress(
    cluster_name: str,
    namespace: str,
    ingress_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """Return full ingress object as a serializable dict."""
    try:
        net = gateway.get_networking_client(cluster_name)
        api_client = ApiClient()
        ing = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
        return api_client.sanitize_for_serialization(ing)
    except ApiException as exc:
        return {"error": str(exc.reason)}


def describe_service(
    cluster_name: str,
    namespace: str,
    service_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """Return full service object as a serializable dict."""
    try:
        core = gateway.get_core_client(cluster_name)
        api_client = ApiClient()
        svc = core.read_namespaced_service(name=service_name, namespace=namespace)
        return api_client.sanitize_for_serialization(svc)
    except ApiException as exc:
        return {"error": str(exc.reason)}


def describe_secret_metadata(
    cluster_name: str,
    namespace: str,
    secret_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """Return secret metadata and key names only (never secret values)."""
    try:
        core = gateway.get_core_client(cluster_name)
        sec = core.read_namespaced_secret(name=secret_name, namespace=namespace)
        data_keys = sorted((sec.data or {}).keys())
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": sec.metadata.name,
                "namespace": sec.metadata.namespace,
                "labels": sec.metadata.labels or {},
                "annotations": sec.metadata.annotations or {},
                "creationTimestamp": str(sec.metadata.creation_timestamp) if sec.metadata.creation_timestamp else None,
            },
            "type": sec.type,
            "immutable": sec.immutable,
            "data_keys": data_keys,
            "data_key_count": len(data_keys),
        }
    except ApiException as exc:
        return {"error": str(exc.reason)}


def _get_pod_events(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    gateway: ClusterGateway,
) -> List[Dict[str, str]]:
    """Return recent events for a specific pod."""
    try:
        core = gateway.get_core_client(cluster_name)
        events = core.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )
        return [
            {
                "reason": e.reason or "",
                "message": e.message or "",
                "type": e.type or "",
                "count": str(e.count or 0),
                "first_timestamp": str(e.first_timestamp) if e.first_timestamp else "",
                "last_timestamp": str(e.last_timestamp) if e.last_timestamp else "",
                "event_time": str(e.event_time) if e.event_time else "",
                "reporting_component": e.reporting_component or "",
                "reporting_instance": e.reporting_instance or "",
                "action": e.action or "",
            }
            for e in events.items
        ]
    except ApiException:
        return []


# ── ResourceQuota ──────────────────────────────────────────────────────────────

def get_resource_quota(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> Optional[ResourceQuotaInfo]:
    """
    Fetch ResourceQuota for a namespace.
    Returns None if no quota is configured.
    """
    try:
        core = gateway.get_core_client(cluster_name)
        quotas = core.list_namespaced_resource_quota(namespace=namespace)
        if not quotas.items:
            return None

        # Take the first quota object
        q = quotas.items[0]
        hard = q.status.hard or {}
        used = q.status.used or {}

        cpu_limit = hard.get("limits.cpu") or hard.get("cpu")
        cpu_used = used.get("limits.cpu") or used.get("cpu")
        mem_limit = hard.get("limits.memory") or hard.get("memory")
        mem_used = used.get("limits.memory") or used.get("memory")

        def pct(u, h):
            pu, ph = _parse_resource(u), _parse_resource(h)
            if pu and ph and ph > 0:
                return round((pu / ph) * 100, 1)
            return None

        return ResourceQuotaInfo(
            name=(q.metadata.name if q and q.metadata else None),
            namespace=namespace,
            cpu_limit=cpu_limit,
            cpu_used=cpu_used,
            cpu_percent=pct(cpu_used, cpu_limit),
            memory_limit=mem_limit,
            memory_used=mem_used,
            memory_percent=pct(mem_used, mem_limit),
            pods_limit=hard.get("pods"),
            pods_used=used.get("pods"),
        )
    except ApiException as exc:
        logger.error("get_resource_quota failed on %s/%s: %s", cluster_name, namespace, exc)
        return None


# ── Deployments ───────────────────────────────────────────────────────────────

def list_deployments(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[DeploymentInfo]:
    """List all deployments in a namespace."""
    try:
        apps = gateway.get_apps_client(cluster_name)
        dep_list = apps.list_namespaced_deployment(namespace=namespace)
        return [_deployment_to_info(d) for d in dep_list.items]
    except ApiException as exc:
        logger.error("list_deployments failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def list_services(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[Dict[str, Any]]:
    """List service resources in a namespace."""
    try:
        core = gateway.get_core_client(cluster_name)
        svc_list = core.list_namespaced_service(namespace=namespace)
        results: List[Dict[str, Any]] = []
        for svc in svc_list.items:
            ports = []
            for p in (svc.spec.ports or []):
                port_num = getattr(p, "port", None)
                target = getattr(p, "target_port", None)
                protocol = getattr(p, "protocol", "TCP")
                ports.append(f"{port_num}->{target}/{protocol}")

            ingress = []
            lb_status = getattr(svc.status, "load_balancer", None)
            if lb_status and lb_status.ingress:
                for i in lb_status.ingress:
                    ingress.append(i.ip or i.hostname or "")

            results.append(
                {
                    "name": svc.metadata.name,
                    "namespace": svc.metadata.namespace,
                    "type": svc.spec.type,
                    "cluster_ip": svc.spec.cluster_ip,
                    "external": ",".join([x for x in ingress if x]) or None,
                    "ports": ports,
                    "age": _age(svc.metadata.creation_timestamp),
                }
            )
        return results
    except ApiException as exc:
        logger.error("list_services failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def list_secrets(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[Dict[str, Any]]:
    """List secret metadata only (never returns secret values)."""
    try:
        core = gateway.get_core_client(cluster_name)
        sec_list = core.list_namespaced_secret(namespace=namespace)
        results: List[Dict[str, Any]] = []
        for sec in sec_list.items:
            data_keys = sorted((sec.data or {}).keys())
            results.append(
                {
                    "name": sec.metadata.name,
                    "namespace": sec.metadata.namespace,
                    "type": sec.type,
                    "data_keys": data_keys,
                    "data_key_count": len(data_keys),
                    "age": _age(sec.metadata.creation_timestamp),
                }
            )
        return results
    except ApiException as exc:
        logger.error("list_secrets failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def _deployment_to_info(dep: V1Deployment) -> DeploymentInfo:
    containers = dep.spec.template.spec.containers or []
    image = containers[0].image if containers else "unknown"
    strategy = dep.spec.strategy.type if dep.spec.strategy else "RollingUpdate"
    return DeploymentInfo(
        name=dep.metadata.name,
        namespace=dep.metadata.namespace,
        replicas=dep.spec.replicas or 0,
        ready_replicas=dep.status.ready_replicas or 0,
        image=image,
        strategy=strategy,
        age=_age(dep.metadata.creation_timestamp),
    )


def get_deployment_manifest(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    """Return full deployment object as serializable dict."""
    try:
        apps = gateway.get_apps_client(cluster_name)
        api_client = ApiClient()
        dep = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        return api_client.sanitize_for_serialization(dep)
    except ApiException as exc:
        logger.error(
            "get_deployment_manifest failed on %s/%s/%s: %s",
            cluster_name,
            namespace,
            deployment_name,
            exc,
        )
        return {"error": str(exc.reason)}


def get_service_manifest(
    cluster_name: str,
    namespace: str,
    service_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    try:
        core = gateway.get_core_client(cluster_name)
        api_client = ApiClient()
        svc = core.read_namespaced_service(name=service_name, namespace=namespace)
        return api_client.sanitize_for_serialization(svc)
    except ApiException as exc:
        logger.error("get_service_manifest failed on %s/%s/%s: %s", cluster_name, namespace, service_name, exc)
        return {"error": str(exc.reason)}


def get_ingress_manifest(
    cluster_name: str,
    namespace: str,
    ingress_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    try:
        net = gateway.get_networking_client(cluster_name)
        api_client = ApiClient()
        ing = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
        return api_client.sanitize_for_serialization(ing)
    except ApiException as exc:
        logger.error("get_ingress_manifest failed on %s/%s/%s: %s", cluster_name, namespace, ingress_name, exc)
        return {"error": str(exc.reason)}


def get_secret_manifest(
    cluster_name: str,
    namespace: str,
    secret_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    try:
        core = gateway.get_core_client(cluster_name)
        api_client = ApiClient()
        sec = core.read_namespaced_secret(name=secret_name, namespace=namespace)
        return api_client.sanitize_for_serialization(sec)
    except ApiException as exc:
        logger.error("get_secret_manifest failed on %s/%s/%s: %s", cluster_name, namespace, secret_name, exc)
        return {"error": str(exc.reason)}


def get_resourcequota_manifest(
    cluster_name: str,
    namespace: str,
    quota_name: str,
    gateway: ClusterGateway,
) -> Dict[str, Any]:
    try:
        core = gateway.get_core_client(cluster_name)
        api_client = ApiClient()
        rq = core.read_namespaced_resource_quota(name=quota_name, namespace=namespace)
        return api_client.sanitize_for_serialization(rq)
    except ApiException as exc:
        logger.error("get_resourcequota_manifest failed on %s/%s/%s: %s", cluster_name, namespace, quota_name, exc)
        return {"error": str(exc.reason)}


# ── HPA ───────────────────────────────────────────────────────────────────────

def get_hpa(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[HPAInfo]:
    """List HorizontalPodAutoscalers in a namespace."""
    try:
        autoscaling = gateway.get_autoscaling_client(cluster_name)
        hpa_list = autoscaling.list_namespaced_horizontal_pod_autoscaler(
            namespace=namespace
        )
        return [_hpa_to_info(h) for h in hpa_list.items]
    except ApiException as exc:
        logger.error("get_hpa failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def _hpa_to_info(hpa: V1HorizontalPodAutoscaler) -> HPAInfo:
    metrics = hpa.spec.metrics
    target_cpu = None
    if metrics:
        for m in metrics:
            if m.type == "Resource" and m.resource and m.resource.name == "cpu":
                if m.resource.target:
                    target_cpu = m.resource.target.average_utilization
    return HPAInfo(
        name=hpa.metadata.name,
        namespace=hpa.metadata.namespace,
        min_replicas=hpa.spec.min_replicas or 1,
        max_replicas=hpa.spec.max_replicas,
        current_replicas=hpa.status.current_replicas or 0,
        desired_replicas=hpa.status.desired_replicas or 0,
        target_cpu_percent=target_cpu,
        current_cpu_percent=hpa.status.current_cpu_utilization_percentage,
    )


# ── Ingress & Networking ──────────────────────────────────────────────────────

def list_ingresses(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[IngressInfo]:
    """List Ingress resources in a namespace."""
    try:
        net = gateway.get_networking_client(cluster_name)
        ing_list = net.list_namespaced_ingress(namespace=namespace)
        return [_ingress_to_info(i) for i in ing_list.items]
    except ApiException as exc:
        logger.error("list_ingresses failed on %s/%s: %s", cluster_name, namespace, exc)
        return []


def _ingress_to_info(ing: V1Ingress) -> IngressInfo:
    host = backend_svc = backend_port = ""
    tls_enabled = bool(ing.spec.tls)
    if ing.spec.rules:
        rule = ing.spec.rules[0]
        host = rule.host or ""
        if rule.http and rule.http.paths:
            path = rule.http.paths[0]
            if path.backend and path.backend.service:
                backend_svc = path.backend.service.name
                backend_port = str(path.backend.service.port.number or "")
    address = ""
    if ing.status.load_balancer and ing.status.load_balancer.ingress:
        lb = ing.status.load_balancer.ingress[0]
        address = lb.ip or lb.hostname or ""
    return IngressInfo(
        name=ing.metadata.name,
        namespace=ing.metadata.namespace,
        host=host,
        tls_enabled=tls_enabled,
        backend_service=backend_svc,
        backend_port=backend_port,
        address=address,
    )


def check_network_policy(
    cluster_name: str,
    namespace: str,
    gateway: ClusterGateway,
) -> List[Dict[str, Any]]:
    """List NetworkPolicy objects in a namespace."""
    try:
        net = gateway.get_networking_client(cluster_name)
        np_list = net.list_namespaced_network_policy(namespace=namespace)
        return [
            {
                "name": np.metadata.name,
                "pod_selector": str(np.spec.pod_selector.match_labels or {}),
                "ingress_rules": len(np.spec.ingress or []),
                "egress_rules": len(np.spec.egress or []),
            }
            for np in np_list.items
        ]
    except ApiException as exc:
        logger.error("check_network_policy failed: %s", exc)
        return []


# ── Cluster version ───────────────────────────────────────────────────────────

def get_k8s_version(cluster_name: str, gateway: ClusterGateway) -> str:
    """Return the Kubernetes server version string for a cluster."""
    try:
        api_client = gateway._get_api_client(cluster_name)
        version_api = client.VersionApi(api_client)
        info = version_api.get_code()

        major = str(getattr(info, "major", "")).strip()
        minor_raw = str(getattr(info, "minor", "")).strip()
        minor_digits = "".join(ch for ch in minor_raw if ch.isdigit())
        if major.isdigit() and minor_digits:
            return f"{int(major)}.{int(minor_digits)}"

        git_version = str(getattr(info, "git_version", "")).strip()
        match = re.search(r"(\d+)\.(\d+)", git_version)
        if match:
            return f"{int(match.group(1))}.{int(match.group(2))}"

        raise ValueError("Version API returned unparseable version fields")
    except Exception as exc:
        logger.warning("Primary version lookup failed for %s: %s", cluster_name, exc)

    # Fallback: infer cluster version from node kubelet versions.
    try:
        core = gateway.get_core_client(cluster_name)
        node_list = core.list_node()
        versions = []
        for node in node_list.items:
            kubelet_version = ""
            if node.status and node.status.node_info and node.status.node_info.kubelet_version:
                kubelet_version = str(node.status.node_info.kubelet_version)
            match = re.search(r"(\d+)\.(\d+)", kubelet_version)
            if match:
                versions.append((int(match.group(1)), int(match.group(2))))

        if versions:
            major, minor = max(versions)
            return f"{major}.{minor}"
    except Exception as exc:
        logger.warning("Fallback node-based version lookup failed for %s: %s", cluster_name, exc)

    logger.warning("get_k8s_version failed for %s: returning unknown", cluster_name)
    return "unknown"
