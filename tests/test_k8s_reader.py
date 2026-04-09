"""
tests/test_k8s_reader.py
------------------------
Unit tests for Kubernetes reader functions.
Uses mock K8s API objects — no real cluster needed.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from capabilities.k8s_reader import (
    _age, _parse_resource, _pod_to_info, _deployment_to_info,
    list_pods, get_resource_quota,
)
from models.schemas import PodInfo


# ── Helper tests ──────────────────────────────────────────────────────────────

def test_age_recent():
    from datetime import timedelta
    ts = MagicMock()
    ts.replace.return_value = datetime.now(timezone.utc) - timedelta(minutes=30)
    result = _age(ts)
    assert "m" in result or "h" in result


def test_age_none():
    assert _age(None) == "unknown"


def test_parse_resource_millicores():
    assert _parse_resource("500m") == pytest.approx(0.5)


def test_parse_resource_gigabytes():
    val = _parse_resource("2Gi")
    assert val == pytest.approx(2 * 1024 ** 3)


def test_parse_resource_plain():
    assert _parse_resource("4") == pytest.approx(4.0)


def test_parse_resource_none():
    assert _parse_resource(None) is None


# ── Pod parsing tests ─────────────────────────────────────────────────────────

def make_mock_pod(name="test-pod", namespace="default", phase="Running",
                  restarts=0, ready=True, image="nginx:latest", node="node-1"):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.creation_timestamp = MagicMock()
    pod.metadata.creation_timestamp.replace.return_value = datetime.now(timezone.utc)
    pod.status.phase = phase
    pod.spec.node_name = node
    pod.spec.containers = [MagicMock()]
    pod.spec.containers[0].image = image
    pod.spec.containers[0].resources.requests = {"cpu": "100m", "memory": "128Mi"}

    cs = MagicMock()
    cs.restart_count = restarts
    cs.ready = ready
    pod.status.container_statuses = [cs]
    return pod


def test_pod_to_info_running():
    pod = make_mock_pod(name="payments-api-abc", phase="Running", restarts=0)
    info = _pod_to_info(pod, "gke-prod-us-east")
    assert isinstance(info, PodInfo)
    assert info.name == "payments-api-abc"
    assert info.status == "Running"
    assert info.restarts == 0
    assert info.cpu_request == "100m"


def test_pod_to_info_crash():
    pod = make_mock_pod(name="auth-svc-crash", phase="CrashLoopBackOff", restarts=14)
    info = _pod_to_info(pod, "gke-prod-us-east")
    assert info.status == "CrashLoopBackOff"
    assert info.restarts == 14


def test_pod_to_info_no_containers():
    pod = MagicMock()
    pod.metadata.name = "empty-pod"
    pod.metadata.namespace = "default"
    pod.metadata.creation_timestamp = None
    pod.status.phase = "Pending"
    pod.spec.node_name = None
    pod.spec.containers = []
    pod.status.container_statuses = None
    info = _pod_to_info(pod, "cluster-1")
    assert info.image == "unknown"
    assert info.restarts == 0


# ── Deployment parsing tests ──────────────────────────────────────────────────

def make_mock_deployment(name="payments-api", replicas=3, ready=3, image="payments:v2"):
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = "payments-prod"
    dep.metadata.creation_timestamp = MagicMock()
    dep.metadata.creation_timestamp.replace.return_value = datetime.now(timezone.utc)
    dep.spec.replicas = replicas
    dep.status.ready_replicas = ready
    dep.spec.template.spec.containers = [MagicMock()]
    dep.spec.template.spec.containers[0].image = image
    dep.spec.strategy.type = "RollingUpdate"
    return dep


def test_deployment_to_info():
    dep = make_mock_deployment(replicas=3, ready=3)
    info = _deployment_to_info(dep)
    assert info.replicas == 3
    assert info.ready_replicas == 3
    assert info.strategy == "RollingUpdate"


def test_deployment_to_info_partial_ready():
    dep = make_mock_deployment(replicas=4, ready=2)
    info = _deployment_to_info(dep)
    assert info.replicas == 4
    assert info.ready_replicas == 2


# ── Integration-style tests (mocked gateway) ─────────────────────────────────

def test_list_pods_returns_empty_on_api_error():
    from kubernetes.client import ApiException
    gateway = MagicMock()
    core = MagicMock()
    gateway.get_core_client.return_value = core
    core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")

    result = list_pods("fake-cluster", "default", gateway)
    assert result == []


def test_get_resource_quota_no_quota():
    gateway = MagicMock()
    core = MagicMock()
    gateway.get_core_client.return_value = core
    core.list_namespaced_resource_quota.return_value = MagicMock(items=[])

    result = get_resource_quota("fake-cluster", "default", gateway)
    assert result is None
