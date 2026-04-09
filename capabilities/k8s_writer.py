"""capabilities/k8s_writer.py
---------------------------
Controlled write operations for Kubernetes workloads.
"""

import logging
from typing import Dict, Any, Optional

from kubernetes.client import ApiException

from gateway.cluster_gateway import ClusterGateway

logger = logging.getLogger(__name__)


def _service_ports_to_text(ports: Any) -> Any:
    out = []
    for p in ports or []:
        port = getattr(p, "port", None)
        target = getattr(p, "target_port", None)
        protocol = getattr(p, "protocol", "TCP")
        out.append(f"{port}->{target}/{protocol}")
    return out


def update_deployment(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    gateway: ClusterGateway,
    image: Optional[str] = None,
    replicas: Optional[int] = None,
) -> Dict[str, Any]:
    """Update deployment image and/or replicas, then push patch to cluster."""
    if image is None and replicas is None:
        raise ValueError("At least one field must be provided: image or replicas")

    apps = gateway.get_apps_client(cluster_name)
    dep = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)

    patch: Dict[str, Any] = {"spec": {}}

    if replicas is not None:
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        patch["spec"]["replicas"] = replicas

    if image is not None:
        containers = dep.spec.template.spec.containers or []
        if not containers:
            raise ValueError(f"Deployment '{deployment_name}' has no containers")

        current_container_name = containers[0].name
        patch["spec"].setdefault("template", {}).setdefault("spec", {})["containers"] = [
            {
                "name": current_container_name,
                "image": image,
            }
        ]

    try:
        apps.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch,
        )
    except ApiException as exc:
        logger.error("update_deployment failed on %s/%s in %s: %s", namespace, deployment_name, cluster_name, exc)
        raise

    updated = apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    updated_containers = updated.spec.template.spec.containers or []

    return {
        "name": deployment_name,
        "namespace": namespace,
        "cluster": cluster_name,
        "replicas": updated.spec.replicas,
        "ready_replicas": updated.status.ready_replicas or 0,
        "image": updated_containers[0].image if updated_containers else None,
    }


def update_service(
    cluster_name: str,
    namespace: str,
    service_name: str,
    gateway: ClusterGateway,
    service_type: Optional[str] = None,
    port: Optional[int] = None,
    target_port: Optional[int] = None,
) -> Dict[str, Any]:
    if service_type is None and port is None and target_port is None:
        raise ValueError("At least one field must be provided: service_type, port, target_port")

    core = gateway.get_core_client(cluster_name)
    svc = core.read_namespaced_service(name=service_name, namespace=namespace)

    patch: Dict[str, Any] = {"spec": {}}
    if service_type is not None:
        allowed = {"ClusterIP", "NodePort", "LoadBalancer", "ExternalName"}
        if service_type not in allowed:
            raise ValueError(f"service_type must be one of {sorted(allowed)}")
        patch["spec"]["type"] = service_type

    if port is not None or target_port is not None:
        if port is not None and port <= 0:
            raise ValueError("port must be > 0")
        if target_port is not None and target_port <= 0:
            raise ValueError("target_port must be > 0")

        existing_ports = (svc.spec.ports or [])
        if not existing_ports:
            raise ValueError(f"Service '{service_name}' has no ports")

        first = existing_ports[0]
        patch["spec"]["ports"] = [{
            "port": port if port is not None else first.port,
            "targetPort": target_port if target_port is not None else first.target_port,
            "protocol": first.protocol or "TCP",
            "name": first.name,
        }]

    try:
        core.patch_namespaced_service(name=service_name, namespace=namespace, body=patch)
    except ApiException as exc:
        logger.error("update_service failed on %s/%s in %s: %s", namespace, service_name, cluster_name, exc)
        raise

    updated = core.read_namespaced_service(name=service_name, namespace=namespace)
    return {
        "name": service_name,
        "namespace": namespace,
        "cluster": cluster_name,
        "type": updated.spec.type,
        "ports": _service_ports_to_text(updated.spec.ports),
        "cluster_ip": updated.spec.cluster_ip,
        "external": (updated.status.load_balancer.ingress[0].ip if updated.status and updated.status.load_balancer and updated.status.load_balancer.ingress else None),
    }


def update_ingress_host(
    cluster_name: str,
    namespace: str,
    ingress_name: str,
    gateway: ClusterGateway,
    host: str,
) -> Dict[str, Any]:
    if not host:
        raise ValueError("host is required")

    net = gateway.get_networking_client(cluster_name)
    ing = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
    rules = ing.spec.rules or []
    if not rules:
        raise ValueError(f"Ingress '{ingress_name}' has no rules to update")

    patch = {
        "spec": {
            "rules": [{
                "host": host,
                "http": rules[0].http.to_dict() if rules[0].http else None,
            }]
        }
    }

    try:
        net.patch_namespaced_ingress(name=ingress_name, namespace=namespace, body=patch)
    except ApiException as exc:
        logger.error("update_ingress_host failed on %s/%s in %s: %s", namespace, ingress_name, cluster_name, exc)
        raise

    updated = net.read_namespaced_ingress(name=ingress_name, namespace=namespace)
    updated_rules = updated.spec.rules or []
    return {
        "name": ingress_name,
        "namespace": namespace,
        "cluster": cluster_name,
        "host": updated_rules[0].host if updated_rules else None,
        "ingress_class_name": updated.spec.ingress_class_name,
        "tls_count": len(updated.spec.tls or []),
    }


def update_secret_key(
    cluster_name: str,
    namespace: str,
    secret_name: str,
    gateway: ClusterGateway,
    key: str,
    value: str,
) -> Dict[str, Any]:
    if not key:
        raise ValueError("key is required")
    if value is None:
        raise ValueError("value is required")

    core = gateway.get_core_client(cluster_name)
    patch = {"stringData": {key: value}}

    try:
        core.patch_namespaced_secret(name=secret_name, namespace=namespace, body=patch)
    except ApiException as exc:
        logger.error("update_secret_key failed on %s/%s in %s: %s", namespace, secret_name, cluster_name, exc)
        raise

    updated = core.read_namespaced_secret(name=secret_name, namespace=namespace)
    data_keys = sorted(list((updated.data or {}).keys()))
    return {
        "name": secret_name,
        "namespace": namespace,
        "cluster": cluster_name,
        "type": updated.type,
        "updated_key": key,
        "data_key_count": len(data_keys),
        "data_keys": data_keys,
    }


def update_resource_quota(
    cluster_name: str,
    namespace: str,
    quota_name: str,
    gateway: ClusterGateway,
    hard: Dict[str, str],
) -> Dict[str, Any]:
    if not hard:
        raise ValueError("At least one hard limit must be provided")

    core = gateway.get_core_client(cluster_name)
    patch = {"spec": {"hard": hard}}

    try:
        core.patch_namespaced_resource_quota(name=quota_name, namespace=namespace, body=patch)
    except ApiException as exc:
        logger.error("update_resource_quota failed on %s/%s in %s: %s", namespace, quota_name, cluster_name, exc)
        raise

    updated = core.read_namespaced_resource_quota(name=quota_name, namespace=namespace)
    return {
        "name": quota_name,
        "namespace": namespace,
        "cluster": cluster_name,
        "hard": dict(updated.spec.hard or {}),
    }
