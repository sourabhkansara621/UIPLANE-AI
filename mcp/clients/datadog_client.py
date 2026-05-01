"""Datadog observability MCP client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from mcp.clients.base import MCPClient
from mcp.schemas import MCPClientResult, MCPObservabilityTarget
from config.settings import get_settings


class DatadogObservabilityClient(MCPClient):
    name = "datadog"

    def _base_url(self) -> str:
        settings = get_settings()
        site = (settings.datadog_site or "datadoghq.com").strip().lower()
        if site.startswith("http://") or site.startswith("https://"):
            return site.rstrip("/")
        if site.startswith("api."):
            return f"https://{site}"
        return f"https://api.{site}"

    @staticmethod
    def _nested_value(payload: Dict[str, Any], candidates: List[List[str]]) -> Optional[str]:
        for path in candidates:
            current: Any = payload
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    current = None
                    break
                current = current[key]
            if isinstance(current, str) and current.strip():
                return current.strip()
        return None

    @staticmethod
    def _guess_severity(status: str, message: str) -> str:
        s = (status or "").lower()
        m = (message or "").lower()
        if s in {"critical", "emergency", "alert", "fatal"}:
            return "critical"
        if s in {"error", "err"}:
            return "error"
        if s in {"warn", "warning"}:
            return "warning"
        keywords = ["oom", "crash", "error", "failed", "backoff", "exception", "timeout", "5xx"]
        if any(k in m for k in keywords):
            return "error"
        return "info"

    def fetch_namespace_issues(
        self,
        namespace: str,
        cluster_name: Optional[str] = None,
        range_hours: int = 6,
        limit: int = 100,
    ) -> Dict[str, Any]:
        settings = get_settings()

        if not settings.datadog_api_key or not settings.datadog_app_key:
            return {
                "configured": False,
                "detail": "Datadog is not configured. Set DATADOG_API_KEY and DATADOG_APP_KEY.",
                "issues": [],
                "total_hits": 0,
            }

        safe_hours = max(1, min(int(range_hours), 168))
        safe_limit = max(10, min(int(limit), 500))
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=safe_hours)

        query_parts = [f"(@kubernetes.namespace_name:{namespace} OR kube_namespace:{namespace})"]
        if cluster_name:
            query_parts.append(
                f"(@kube_cluster_name:{cluster_name} OR cluster_name:{cluster_name} OR @kubernetes.cluster_name:{cluster_name})"
            )
        query = " AND ".join(query_parts)

        payload = {
            "filter": {
                "query": query,
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": now.isoformat().replace("+00:00", "Z"),
            },
            "sort": "timestamp",
            "page": {"limit": safe_limit},
        }

        headers = {
            "DD-API-KEY": settings.datadog_api_key,
            "DD-APPLICATION-KEY": settings.datadog_app_key,
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"{self._base_url()}/api/v2/logs/events/search",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            return {
                "configured": True,
                "detail": f"Failed to fetch Datadog logs: {exc}",
                "issues": [],
                "total_hits": 0,
            }

        rows = body.get("data") or []
        grouped: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            attrs = row.get("attributes") or {}
            nested = attrs.get("attributes") or {}

            message = attrs.get("message") or nested.get("message") or "No message"
            status = attrs.get("status") or nested.get("status") or ""
            timestamp = attrs.get("timestamp") or ""

            pod_name = self._nested_value(
                nested,
                [
                    ["kubernetes", "pod_name"],
                    ["pod_name"],
                    ["kube_pod_name"],
                ],
            ) or "unknown-pod"

            severity = self._guess_severity(status, message)
            if severity == "info":
                continue

            key = f"{pod_name}:{severity}"
            if key not in grouped:
                grouped[key] = {
                    "pod_name": pod_name,
                    "severity": severity,
                    "issue": message[:220],
                    "last_seen": timestamp,
                    "occurrences": 0,
                }

            grouped[key]["occurrences"] += 1
            if timestamp and timestamp > (grouped[key].get("last_seen") or ""):
                grouped[key]["last_seen"] = timestamp

        issues = sorted(
            grouped.values(),
            key=lambda item: (
                0 if item["severity"] == "critical" else 1 if item["severity"] == "error" else 2,
                -(item.get("occurrences") or 0),
                item.get("last_seen") or "",
            ),
        )

        return {
            "configured": True,
            "namespace": namespace,
            "cluster_name": cluster_name,
            "range_hours": safe_hours,
            "window_start": start.isoformat().replace("+00:00", "Z"),
            "window_end": now.isoformat().replace("+00:00", "Z"),
            "total_hits": len(rows),
            "issues": issues,
        }

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
