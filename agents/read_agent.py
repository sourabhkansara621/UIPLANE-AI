"""
agents/read_agent.py
--------------------
Phase 1 Read Agent.
- Keyword-based intent parser (no AI credits needed)
- Mock K8s data fallback when no cluster is connected
- Plain-text summary fallback when Anthropic credits are unavailable
- Uses real Claude AI when credits are available
"""

import json
import logging
import re
import yaml
from difflib import get_close_matches
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import uuid
import httpx

from sqlalchemy.orm import Session

from config.settings import get_settings
from models.database import User, ClusterRegistry, AuditLog
from models.schemas import ChatQueryResponse
from auth.rbac import require_app_access, get_user_allowed_apps, require_mutation_permission
from gateway.cluster_gateway import ClusterGateway
from capabilities.k8s_reader import (
    list_pods, get_resource_quota, list_deployments,
    get_hpa, list_ingresses, get_pod_logs, describe_pod,
    check_network_policy, get_k8s_version, list_namespaces, list_nodes,
    list_services, list_secrets, get_deployment_manifest,
    get_service_manifest, get_ingress_manifest, get_secret_manifest, get_resourcequota_manifest,
    describe_deployment, describe_ingress, describe_service, describe_secret_metadata,
)
from capabilities.k8s_writer import update_deployment
from capabilities.k8s_writer import (
    update_service,
    update_ingress_host,
    update_secret_key,
    update_resource_quota,
)

logger = logging.getLogger(__name__)
settings = get_settings()
SESSION_CONTEXT: Dict[str, Dict[str, Optional[str]]] = {}
MUTATION_INTENTS = {
    "deployment_update",
    "service_update",
    "ingress_update",
    "secret_update",
    "resourcequota_update",
}


def _to_yaml(payload: Any) -> str:
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


# ── Intent dataclass ──────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    intent_type: str
    app_name: Optional[str] = None
    pod_name: Optional[str] = None
    tail_lines: Optional[int] = None
    environment: Optional[str] = None
    namespace: Optional[str] = None
    raw_query: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_K8S_DATA = {
    "payments-api": {
        "pods": [
            {"name": "payments-api-7d9f8-xk2p", "namespace": "payments-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "120m", "memory_request": "256Mi",
             "image": "gcr.io/mycompany/payments-api:v2.14.1", "node": "node-1", "age": "5d"},
            {"name": "payments-api-7d9f8-lm4q", "namespace": "payments-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "135m", "memory_request": "241Mi",
             "image": "gcr.io/mycompany/payments-api:v2.14.1", "node": "node-2", "age": "5d"},
            {"name": "payments-api-7d9f8-np8r", "namespace": "payments-prod",
             "status": "Running", "ready": "1/1", "restarts": 2,
             "cpu_request": "98m", "memory_request": "512Mi",
             "image": "gcr.io/mycompany/payments-api:v2.14.1", "node": "node-1", "age": "3d"},
        ],
        "quota": {"namespace": "payments-prod", "cpu_limit": "4", "cpu_used": "1.2",
                  "cpu_percent": 30.0, "memory_limit": "4Gi", "memory_used": "1.1Gi",
                  "memory_percent": 27.5, "pods_limit": "20", "pods_used": "3"},
        "hpas": [{"name": "payments-api-hpa", "namespace": "payments-prod",
                  "min_replicas": 2, "max_replicas": 10, "current_replicas": 3,
                  "desired_replicas": 3, "target_cpu_percent": 70, "current_cpu_percent": 52}],
        "ingresses": [{"name": "payments-ingress", "namespace": "payments-prod",
                       "host": "api.payments.mycompany.com", "tls_enabled": True,
                       "backend_service": "payments-api", "backend_port": "8080",
                       "address": "34.102.140.239"}],
        "deployments": [{"name": "payments-api", "namespace": "payments-prod",
                         "replicas": 3, "ready_replicas": 3,
                         "image": "gcr.io/mycompany/payments-api:v2.14.1",
                         "strategy": "RollingUpdate", "age": "5d"}],
        "where": {"cluster": "gke-prod-us-east", "environment": "prod",
                  "namespace": "payments-prod", "cloud_provider": "GKE",
                  "region": "us-east1", "k8s_version": "1.28"},
    },
    "auth-service": {
        "pods": [
            {"name": "auth-svc-6b8d9-qw3e", "namespace": "auth-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "80m", "memory_request": "180Mi",
             "image": "gcr.io/mycompany/auth-service:v1.9.3", "node": "node-3", "age": "2d"},
            {"name": "auth-svc-6b8d9-rt5y", "namespace": "auth-prod",
             "status": "CrashLoopBackOff", "ready": "0/1", "restarts": 14,
             "cpu_request": "0m", "memory_request": "0Mi",
             "image": "gcr.io/mycompany/auth-service:v1.9.3", "node": "node-2", "age": "2d"},
        ],
        "quota": {"namespace": "auth-prod", "cpu_limit": "2", "cpu_used": "0.8",
                  "cpu_percent": 40.0, "memory_limit": "2Gi", "memory_used": "0.18Gi",
                  "memory_percent": 9.0, "pods_limit": "10", "pods_used": "2"},
        "hpas": [{"name": "auth-svc-hpa", "namespace": "auth-prod",
                  "min_replicas": 2, "max_replicas": 6, "current_replicas": 2,
                  "desired_replicas": 2, "target_cpu_percent": 60, "current_cpu_percent": 40}],
        "ingresses": [{"name": "auth-ingress", "namespace": "auth-prod",
                       "host": "auth.mycompany.com", "tls_enabled": True,
                       "backend_service": "auth-svc", "backend_port": "3000",
                       "address": "34.102.140.100"}],
        "deployments": [{"name": "auth-service", "namespace": "auth-prod",
                         "replicas": 2, "ready_replicas": 1,
                         "image": "gcr.io/mycompany/auth-service:v1.9.3",
                         "strategy": "RollingUpdate", "age": "2d"}],
        "where": {"cluster": "gke-prod-us-east", "environment": "prod",
                  "namespace": "auth-prod", "cloud_provider": "GKE",
                  "region": "us-east1", "k8s_version": "1.28"},
    },
    "billing-service": {
        "pods": [
            {"name": "billing-svc-9e2a-vb4n", "namespace": "billing-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "200m", "memory_request": "512Mi",
             "image": "gcr.io/mycompany/billing-service:v4.1.0", "node": "node-1", "age": "7d"},
            {"name": "billing-svc-9e2a-cx7m", "namespace": "billing-prod",
             "status": "Running", "ready": "1/1", "restarts": 1,
             "cpu_request": "188m", "memory_request": "490Mi",
             "image": "gcr.io/mycompany/billing-service:v4.1.0", "node": "node-3", "age": "7d"},
        ],
        "quota": {"namespace": "billing-prod", "cpu_limit": "3", "cpu_used": "1.5",
                  "cpu_percent": 50.0, "memory_limit": "3Gi", "memory_used": "1Gi",
                  "memory_percent": 33.3, "pods_limit": "12", "pods_used": "2"},
        "hpas": [{"name": "billing-hpa", "namespace": "billing-prod",
                  "min_replicas": 2, "max_replicas": 8, "current_replicas": 2,
                  "desired_replicas": 2, "target_cpu_percent": 65, "current_cpu_percent": 62}],
        "ingresses": [{"name": "billing-ingress", "namespace": "billing-prod",
                       "host": "billing.mycompany.com", "tls_enabled": True,
                       "backend_service": "billing-svc", "backend_port": "9090",
                       "address": "34.102.140.50"}],
        "deployments": [{"name": "billing-service", "namespace": "billing-prod",
                         "replicas": 2, "ready_replicas": 2,
                         "image": "gcr.io/mycompany/billing-service:v4.1.0",
                         "strategy": "RollingUpdate", "age": "7d"}],
        "where": {"cluster": "gke-prod-us-east", "environment": "prod",
                  "namespace": "billing-prod", "cloud_provider": "GKE",
                  "region": "us-east1", "k8s_version": "1.28"},
    },
    "web-frontend": {
        "pods": [
            {"name": "web-fe-d4c7-mn2k", "namespace": "frontend-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "30m", "memory_request": "64Mi",
             "image": "gcr.io/mycompany/web-frontend:v3.2.0", "node": "node-2", "age": "1d"},
            {"name": "web-fe-d4c7-op9l", "namespace": "frontend-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "28m", "memory_request": "71Mi",
             "image": "gcr.io/mycompany/web-frontend:v3.2.0", "node": "node-1", "age": "1d"},
            {"name": "web-fe-d4c7-qr6s", "namespace": "frontend-prod",
             "status": "Pending", "ready": "0/1", "restarts": 0,
             "cpu_request": "0m", "memory_request": "0Mi",
             "image": "gcr.io/mycompany/web-frontend:v3.2.0", "node": None, "age": "10m"},
        ],
        "quota": {"namespace": "frontend-prod", "cpu_limit": "1", "cpu_used": "0.3",
                  "cpu_percent": 30.0, "memory_limit": "512Mi", "memory_used": "135Mi",
                  "memory_percent": 26.4, "pods_limit": "8", "pods_used": "3"},
        "hpas": [{"name": "web-fe-hpa", "namespace": "frontend-prod",
                  "min_replicas": 2, "max_replicas": 8, "current_replicas": 3,
                  "desired_replicas": 3, "target_cpu_percent": 50, "current_cpu_percent": 28}],
        "ingresses": [{"name": "frontend-ingress", "namespace": "frontend-prod",
                       "host": "app.mycompany.com", "tls_enabled": True,
                       "backend_service": "web-fe", "backend_port": "80",
                       "address": "34.102.140.10"}],
        "deployments": [{"name": "web-frontend", "namespace": "frontend-prod",
                         "replicas": 3, "ready_replicas": 2,
                         "image": "gcr.io/mycompany/web-frontend:v3.2.0",
                         "strategy": "RollingUpdate", "age": "1d"}],
        "where": {"cluster": "eks-prod-eu-west", "environment": "prod",
                  "namespace": "frontend-prod", "cloud_provider": "EKS",
                  "region": "eu-west-1", "k8s_version": "1.27"},
    },
    "user-mgmt": {
        "pods": [
            {"name": "usermgmt-3f1b-yz8w", "namespace": "usermgmt-prod",
             "status": "Running", "ready": "1/1", "restarts": 0,
             "cpu_request": "60m", "memory_request": "200Mi",
             "image": "gcr.io/mycompany/user-mgmt:v2.0.5", "node": "node-3", "age": "10d"},
        ],
        "quota": {"namespace": "usermgmt-prod", "cpu_limit": "2", "cpu_used": "0.4",
                  "cpu_percent": 20.0, "memory_limit": "2Gi", "memory_used": "0.2Gi",
                  "memory_percent": 10.0, "pods_limit": "8", "pods_used": "1"},
        "hpas": [{"name": "usermgmt-hpa", "namespace": "usermgmt-prod",
                  "min_replicas": 1, "max_replicas": 4, "current_replicas": 1,
                  "desired_replicas": 1, "target_cpu_percent": 60, "current_cpu_percent": 30}],
        "ingresses": [{"name": "usermgmt-ingress", "namespace": "usermgmt-prod",
                       "host": "users.mycompany.com", "tls_enabled": True,
                       "backend_service": "usermgmt", "backend_port": "8000",
                       "address": "34.102.140.20"}],
        "deployments": [{"name": "user-mgmt", "namespace": "usermgmt-prod",
                         "replicas": 1, "ready_replicas": 1,
                         "image": "gcr.io/mycompany/user-mgmt:v2.0.5",
                         "strategy": "RollingUpdate", "age": "10d"}],
        "where": {"cluster": "eks-prod-eu-west", "environment": "prod",
                  "namespace": "usermgmt-prod", "cloud_provider": "EKS",
                  "region": "eu-west-1", "k8s_version": "1.27"},
    },
}


# ── Read Agent ────────────────────────────────────────────────────────────────

class ReadAgent:

    def __init__(self, gateway: ClusterGateway):
        self.gateway = gateway
        self._ai_provider = self._resolve_ai_provider()
        self._ai_available = self._ai_provider in {"anthropic", "github_models"}

    def _resolve_ai_provider(self) -> str:
        provider = (settings.ai_provider or "auto").strip().lower()
        if provider == "anthropic" and settings.anthropic_api_key:
            return "anthropic"
        if provider == "github_models" and settings.github_models_token:
            return "github_models"
        if provider == "auto":
            if settings.anthropic_api_key:
                return "anthropic"
            if settings.github_models_token:
                return "github_models"
        return "none"

    def get_llm_status(self) -> Dict[str, Any]:
        if self._ai_provider == "anthropic":
            return {
                "enabled": True,
                "provider": "anthropic",
                "model": settings.anthropic_model,
                "configured": {
                    "anthropic_api_key": bool(settings.anthropic_api_key),
                    "github_models_token": bool(settings.github_models_token),
                },
            }

        if self._ai_provider == "github_models":
            return {
                "enabled": True,
                "provider": "github_models",
                "model": settings.github_models_model,
                "endpoint": settings.github_models_endpoint,
                "configured": {
                    "anthropic_api_key": bool(settings.anthropic_api_key),
                    "github_models_token": bool(settings.github_models_token),
                },
            }

        return {
            "enabled": False,
            "provider": "none",
            "model": None,
            "configured": {
                "anthropic_api_key": bool(settings.anthropic_api_key),
                "github_models_token": bool(settings.github_models_token),
            },
            "message": "No LLM provider configured. Set AI_PROVIDER and credentials in .env.",
        }

    # ── Main entrypoint ───────────────────────────────────────────────────────

    def process_query(
        self,
        query: str,
        user: User,
        db: Session,
        session_id: Optional[str] = None,
        chat_mode: str = "k8-info",
    ) -> ChatQueryResponse:

        session_id = session_id or str(uuid.uuid4())
        chat_mode = (chat_mode or "k8-info").strip().lower()
        if chat_mode not in {"k8-info", "k8-agent", "k8-autofix"}:
            chat_mode = "k8-info"
        context = SESSION_CONTEXT.setdefault(session_id, {})
        intent = self._parse_intent(query)
        intent.raw_query = query
        mutation_confirmed = False

        if not intent.app_name and context.get("app_name"):
            intent.app_name = context.get("app_name")
        if not intent.namespace and context.get("namespace"):
            intent.namespace = context.get("namespace")
        if not intent.pod_name and context.get("pod_name"):
            intent.pod_name = context.get("pod_name")

        pending_mutation = context.get("pending_mutation")
        if intent.intent_type == "mutation_cancel":
            if pending_mutation:
                context.pop("pending_mutation", None)
                return ChatQueryResponse(
                    answer="Pending resource update discarded.",
                    session_id=session_id,
                )
            return ChatQueryResponse(
                answer="No pending resource update to cancel.",
                session_id=session_id,
            )

        if intent.intent_type == "mutation_confirm":
            if not pending_mutation:
                return ChatQueryResponse(
                    answer=(
                        "No pending resource update to apply.\n"
                        "Run an update command first, then confirm it."
                    ),
                    session_id=session_id,
                )
            confirm_count = int(pending_mutation.get("confirm_count") or 0)
            if confirm_count < 1:
                pending_mutation["confirm_count"] = 1
                context["pending_mutation"] = pending_mutation
                return ChatQueryResponse(
                    answer=(
                        "Second confirmation required for safety.\n"
                        "Review the preview once more, then type 'confirm apply' again to execute."
                    ),
                    session_id=session_id,
                )
            intent = IntentResult(
                intent_type=pending_mutation.get("intent_type") or "deployment_update",
                app_name=pending_mutation.get("app_name"),
                namespace=pending_mutation.get("namespace"),
                environment=pending_mutation.get("environment"),
                raw_query=query,
                extra=pending_mutation.get("extra", {}),
            )
            mutation_confirmed = True

        if intent.intent_type == "describe_pod" and not intent.pod_name:
            return ChatQueryResponse(
                answer=(
                    "No pod is selected yet.\n"
                    "Run 'show pods' first, then you can type just 'describe'.\n"
                    "You can also use: 'describe POD_NAME'."
                ),
                session_id=session_id,
            )

        if intent.intent_type == "deployment_describe" and not (intent.extra or {}).get("deployment_name"):
            inferred = self._infer_single_resource_name(intent, db, "deployment")
            if inferred:
                intent.extra["deployment_name"] = inferred
            else:
                return ChatQueryResponse(
                    answer=(
                        "Please provide deployment name.\n"
                        "Example: describe deployment <deployment-name> in namespace <ns> for <app>"
                    ),
                    session_id=session_id,
                )

        if intent.intent_type == "service_describe" and not (intent.extra or {}).get("service_name"):
            inferred = self._infer_single_resource_name(intent, db, "service")
            if inferred:
                intent.extra["service_name"] = inferred
            else:
                return ChatQueryResponse(
                    answer=(
                        "Please provide service name.\n"
                        "Example: describe service <service-name> in namespace <ns> for <app>"
                    ),
                    session_id=session_id,
                )

        if intent.intent_type == "ingress_describe" and not (intent.extra or {}).get("ingress_name"):
            inferred = self._infer_single_resource_name(intent, db, "ingress")
            if inferred:
                intent.extra["ingress_name"] = inferred
            else:
                return ChatQueryResponse(
                    answer=(
                        "Please provide ingress name.\n"
                        "Example: describe ingress <ingress-name> in namespace <ns> for <app>"
                    ),
                    session_id=session_id,
                )

        if intent.intent_type == "secret_describe" and not (intent.extra or {}).get("secret_name"):
            inferred = self._infer_single_resource_name(intent, db, "secret")
            if inferred:
                intent.extra["secret_name"] = inferred
            else:
                return ChatQueryResponse(
                    answer=(
                        "Please provide secret name.\n"
                        "Example: describe secret <secret-name> in namespace <ns> for <app>"
                    ),
                    session_id=session_id,
                )

        if intent.intent_type in MUTATION_INTENTS and chat_mode == "k8-info":
            return ChatQueryResponse(
                answer=(
                    "Resource edits are disabled in k8-info mode.\n"
                    "Switch mode to k8-agent (or k8-autofix) to edit resources.\n"
                    "In k8-info mode, use read-only commands such as:\n"
                    "  - show all deployments\n"
                    "  - show all services\n"
                    "  - show all secrets\n"
                    "  - show all ingresses\n"
                    "  - show quota"
                ),
                session_id=session_id,
            )

        if intent.intent_type == "logs" and not intent.pod_name:
            return ChatQueryResponse(
                answer=(
                    "No pod is selected yet.\n"
                    "Run 'show pods' first, then select a pod, then you can type just 'log'.\n"
                    "You can also use: 'log POD_NAME' or 'log POD_NAME 200 lines'."
                ),
                session_id=session_id,
            )

        if intent.intent_type == "namespace_select" and intent.namespace:
            if not intent.app_name:
                intent.app_name = self._infer_app_from_namespace(intent.namespace, user, db) or context.get("app_name")
            context["namespace"] = intent.namespace
            if intent.app_name:
                context["app_name"] = intent.app_name
            app_hint = intent.app_name or "your app"
            base_menu = (
                "Now ask:\n"
                "  - show all deployments\n"
                "  - show all services\n"
                "  - show all secrets\n"
                "  - show quota\n"
                "  - show all ingresses"
            ) if chat_mode in {"k8-agent", "k8-autofix"} else (
                "Now ask:\n"
                "  - show all pods\n"
                "  - show all deployments\n"
                "  - show all services\n"
                "  - show all secrets\n"
                "  - show quota\n"
                "  - get hpa\n"
                "  - show all ingresses\n"
                "  - show node status"
            )
            return ChatQueryResponse(
                answer=(
                    f"Selected namespace: {intent.namespace}\n"
                    f"App context: {app_hint}\n\n"
                    f"{base_menu}"
                ),
                session_id=session_id,
            )

        if intent.intent_type == "main_menu":
            ns = intent.namespace or context.get("namespace")
            app = intent.app_name or context.get("app_name")
            if ns:
                context["namespace"] = ns
            if app:
                context["app_name"] = app
            app_hint = app or "your app"
            ns_hint = ns or "(not selected)"
            base_menu = (
                "Now ask:\n"
                "  - show all deployments\n"
                "  - show all services\n"
                "  - show all secrets\n"
                "  - show quota\n"
                "  - show all ingresses"
            ) if chat_mode in {"k8-agent", "k8-autofix"} else (
                "Now ask:\n"
                "  - show all pods\n"
                "  - show all deployments\n"
                "  - show all services\n"
                "  - show all secrets\n"
                "  - show quota\n"
                "  - get hpa\n"
                "  - show all ingresses\n"
                "  - show node status"
            )
            return ChatQueryResponse(
                answer=(
                    "Main menu\n"
                    f"Selected namespace: {ns_hint}\n"
                    f"App context: {app_hint}\n\n"
                    f"{base_menu}"
                ),
                session_id=session_id,
            )

        # List apps — no cluster needed
        if intent.intent_type == "list_apps":
            allowed = get_user_allowed_apps(user, db)
            if allowed == ["*"]:
                apps = [
                    row[0]
                    for row in db.query(ClusterRegistry.app_name)
                    .filter(ClusterRegistry.is_active == True)
                    .distinct()
                    .all()
                ]
            else:
                apps = allowed
            answer = f"You have access to {len(apps)} application(s):\n"
            answer += "\n".join(f"  - {a}" for a in apps)
            return ChatQueryResponse(answer=answer, session_id=session_id)

        # General chat flow (non-K8s questions typed in the same textbox).
        if intent.intent_type == "general_chat":
            answer = self._general_chat_response(query=query, user=user, context=context)
            self._write_audit(
                user=user,
                db=db,
                action="READ",
                resource_type="general_chat",
                app_name=context.get("app_name") or "",
                query_text=query,
                result_summary=answer[:500],
                success=True,
            )
            return ChatQueryResponse(answer=answer, session_id=session_id)

        # Infer app name if not detected
        if not intent.app_name:
            allowed = get_user_allowed_apps(user, db)
            intent.app_name = self._infer_app(query, allowed)

        # Namespace can imply app when not explicitly provided
        if intent.namespace and not intent.app_name:
            intent.app_name = self._infer_app_from_namespace(intent.namespace, user, db)

        # Normalize typos in app names against user's allowed apps before RBAC.
        intent.app_name = self._normalize_app_name(intent.app_name, user, db)

        # Namespace-first workflow for operational reads
        namespace_required_intents = {
            "pods", "quota", "hpa", "ingress", "deployments", "deployment_manifest", "deployment_edit", "service_edit", "ingress_edit", "secret_edit", "resourcequota_edit", "services", "secrets", "deployment_update", "service_update", "ingress_update", "secret_update", "resourcequota_update", "logs", "where", "version", "describe_pod", "deployment_describe", "service_describe", "ingress_describe", "secret_describe", "pod_select", "image_pull_help", "k8s_issue_help"
        }
        if intent.intent_type in namespace_required_intents and not intent.namespace:
            ns_options = self._get_user_namespaces(user, db, intent.app_name)
            hint = "\n".join(f"  - {n}" for n in ns_options[:12]) if ns_options else "  - default"
            return ChatQueryResponse(
                answer=(
                    "Please provide a namespace first.\n"
                    "Example: 'show pods in namespace default for sandbox'\n\n"
                    "Namespaces you can use:\n"
                    f"{hint}"
                ),
                session_id=session_id,
            )

        if intent.app_name:
            context["app_name"] = intent.app_name
        if intent.namespace:
            context["namespace"] = intent.namespace

        # Namespace listing flow (no app required)
        if intent.intent_type == "namespaces" and not intent.app_name:
            ns_options = self._get_user_namespaces(user, db)
            if not ns_options:
                return ChatQueryResponse(
                    answer="No namespaces found in your accessible registry entries.",
                    session_id=session_id,
                )
            answer = "Namespaces you can access:\n" + "\n".join(f"  - {n}" for n in ns_options)
            answer += "\n\nNow ask with namespace, e.g. 'show pods in namespace default for sandbox'."
            return ChatQueryResponse(answer=answer, session_id=session_id)

        # RBAC check
        if intent.app_name and intent.app_name != "*":
            try:
                require_app_access(user, intent.app_name, db)
            except Exception as exc:
                self._write_audit(user=user, db=db, action="DENIED",
                                  resource_type=intent.intent_type,
                                  app_name=intent.app_name or "",
                                  query_text=query, result_summary=str(exc), success=False)
                return ChatQueryResponse(answer=str(exc), session_id=session_id)

        if intent.intent_type in MUTATION_INTENTS and intent.app_name and intent.app_name != "*":
            try:
                require_mutation_permission(user, intent.app_name, db)
            except Exception as exc:
                self._write_audit(user=user, db=db, action="DENIED",
                                  resource_type=intent.intent_type,
                                  app_name=intent.app_name or "",
                                  query_text=query, result_summary=str(exc), success=False)
                return ChatQueryResponse(answer=str(exc), session_id=session_id)

        # Get registry entries
        registry_entries = self._get_registry_entries(intent, db)

        if intent.intent_type in {"describe_pod", "logs"} and registry_entries:
            resolved_pod = None
            resolved_entries: List[ClusterRegistry] = []
            candidate_names: List[str] = []
            for reg in registry_entries:
                if reg.cluster_name not in self.gateway.list_clusters():
                    continue
                pods = list_pods(reg.cluster_name, intent.namespace or reg.namespace, self.gateway)
                pod_names = [p.name for p in pods]
                candidate_names.extend(pod_names)
                match = self._resolve_pod_name(intent.pod_name or "", pod_names)
                if match:
                    resolved_pod = match
                    resolved_entries = [reg]
                    break

            if resolved_pod:
                intent.pod_name = resolved_pod
                registry_entries = resolved_entries
            elif candidate_names:
                sample = "\n".join(f"  - {name}" for name in sorted(set(candidate_names))[:20])
                return ChatQueryResponse(
                    answer=(
                        f"Pod '{intent.pod_name}' was not found in namespace '{intent.namespace}'.\n"
                        "Use one of these pod names:\n"
                        f"{sample}\n\n"
                        f"Then run: {'describe' if intent.intent_type == 'describe_pod' else 'log'} POD_NAME"
                    ),
                    session_id=session_id,
                )

        if intent.intent_type == "pod_select" and registry_entries:
            selected_pod = None
            for reg in registry_entries:
                if reg.cluster_name not in self.gateway.list_clusters():
                    continue
                pods = list_pods(reg.cluster_name, intent.namespace or reg.namespace, self.gateway)
                pod_names = [p.name for p in pods]
                match = self._resolve_pod_name(intent.pod_name or "", pod_names)
                if match:
                    selected_pod = match
                    break

            if not selected_pod:
                return ChatQueryResponse(
                    answer=(
                        f"Pod '{intent.pod_name}' was not found in namespace '{intent.namespace}'.\n"
                        "Use: show pods (to list available pod names), then select one with 'select pod POD_NAME'."
                    ),
                    session_id=session_id,
                )

            context["pod_name"] = selected_pod
            return ChatQueryResponse(
                answer=(
                    f"Selected pod: {selected_pod}\n"
                    f"Namespace: {intent.namespace}\n\n"
                    "Now ask:\n"
                    "  - describe\n"
                    "  - log\n"
                    "  - log 200 lines"
                ),
                session_id=session_id,
            )

        # Fetch data
        raw_data: Dict[str, Any] = {}
        clusters_accessed: List[str] = []

        if registry_entries:
            if intent.intent_type in MUTATION_INTENTS and len(registry_entries) > 1 and not intent.environment:
                return ChatQueryResponse(
                    answer=(
                        "Multiple cluster targets match this app.\n"
                        "Add environment in your command to avoid broad updates, e.g. 'in prod' or 'in nonprod'."
                    ),
                    session_id=session_id,
                )

            if intent.intent_type in MUTATION_INTENTS and not mutation_confirmed:
                op = intent.extra or {}
                name_by_intent = {
                    "deployment_update": op.get("deployment_name"),
                    "service_update": op.get("service_name"),
                    "ingress_update": op.get("ingress_name"),
                    "secret_update": op.get("secret_name"),
                    "resourcequota_update": op.get("resourcequota_name"),
                }
                resource_name = name_by_intent.get(intent.intent_type) or "unknown"
                targets = ", ".join(sorted({reg.cluster_name for reg in registry_entries}))
                context["pending_mutation"] = {
                    "intent_type": intent.intent_type,
                    "app_name": intent.app_name,
                    "namespace": intent.namespace,
                    "environment": intent.environment,
                    "extra": op,
                    "confirm_count": 0,
                }
                title_by_intent = {
                    "deployment_update": "Deployment update preview",
                    "service_update": "Service update preview",
                    "ingress_update": "Ingress update preview",
                    "secret_update": "Secret update preview",
                    "resourcequota_update": "ResourceQuota update preview",
                }
                resource_label_by_intent = {
                    "deployment_update": "deployment",
                    "service_update": "service",
                    "ingress_update": "ingress",
                    "secret_update": "secret",
                    "resourcequota_update": "resourcequota",
                }
                change_lines = []
                for k, v in op.items():
                    if k.endswith("_name"):
                        continue
                    if k == "hard" and isinstance(v, dict):
                        for hk, hv in v.items():
                            change_lines.append(f"  - {hk}: {hv}")
                        continue
                    if k == "value":
                        change_lines.append("  - value: ***hidden***")
                        continue
                    change_lines.append(f"  - {k}: {v}")
                changes = "\n".join(change_lines) if change_lines else "  - no changes detected"
                return ChatQueryResponse(
                    answer=(
                        f"{title_by_intent.get(intent.intent_type, 'Update preview')}:\n"
                        f"  - app: {intent.app_name}\n"
                        f"  - {resource_label_by_intent.get(intent.intent_type, 'resource')}: {resource_name}\n"
                        f"  - namespace: {intent.namespace or 'default'}\n"
                        f"  - targets: {targets}\n"
                        "Requested changes:\n"
                        f"{changes}\n\n"
                        "Type 'confirm apply' twice to execute, or 'cancel apply' to discard."
                    ),
                    session_id=session_id,
                )

            for reg in registry_entries:
                is_connected = reg.cluster_name in self.gateway.list_clusters()
                if is_connected:
                    try:
                        if intent.intent_type in MUTATION_INTENTS:
                            raw_data[reg.cluster_name] = self._execute_k8s_mutation(intent, reg)
                        else:
                            raw_data[reg.cluster_name] = self._execute_k8s_read(intent, reg)
                        clusters_accessed.append(reg.cluster_name)
                    except Exception as exc:
                        logger.error("K8s read failed on %s: %s", reg.cluster_name, exc)
                        raw_data[reg.cluster_name] = {"error": str(exc)}
                        clusters_accessed.append(reg.cluster_name)
                else:
                    raw_data[reg.cluster_name] = {
                        "error": f"Cluster '{reg.cluster_name}' is not connected. Check kubeconfig context loading."
                    }
                    clusters_accessed.append(reg.cluster_name)
        elif intent.app_name:
            return ChatQueryResponse(
                answer=(
                    f"No active cluster registry entry found for app '{intent.app_name}'. "
                    "Please verify your registry/config for this app."
                ),
                session_id=session_id,
            )
        else:
            return ChatQueryResponse(
                answer="Please specify an application name. Example: 'show pods for payments-api'",
                session_id=session_id,
            )

        # Generate answer
        # Keep selected pod in session context so user can type just 'describe'.
        if intent.intent_type == "describe_pod" and intent.pod_name:
            context["pod_name"] = intent.pod_name
        elif intent.intent_type == "logs" and intent.pod_name:
            context["pod_name"] = intent.pod_name
        elif intent.intent_type in MUTATION_INTENTS:
            context.pop("pending_mutation", None)

        answer = self._generate_summary(query, raw_data, intent, user, chat_mode)

        # Flag for UI: open deployment editor modal
        if intent.intent_type in {"deployment_edit", "service_edit", "ingress_edit", "secret_edit", "resourcequota_edit"}:
            raw_data["open_editor_modal"] = True

        # Audit
        audit_action = "MUTATE" if intent.intent_type in MUTATION_INTENTS else "READ"
        self._write_audit(user=user, db=db, action=audit_action,
                          resource_type=intent.intent_type,
                          app_name=intent.app_name or "",
                          cluster_name=", ".join(clusters_accessed),
                          query_text=query, result_summary=answer[:500], success=True)

        return ChatQueryResponse(answer=answer, data=raw_data,
                                 clusters_accessed=clusters_accessed, session_id=session_id)

    # ── Intent parser (keyword, no AI) ────────────────────────────────────────

    def _parse_intent(self, query: str) -> IntentResult:
        q = query.lower()
        q_stripped = q.strip()
        intent_type = "summary"
        generic_describe_name: Optional[str] = None
        generic_describe_key: Optional[str] = None
        force_list_intent: Optional[str] = None
        list_guards = [
            (r"\b(show|list|get)\s+(all\s+)?pods\b", "pods"),
            (r"\b(show|list|get)\s+(all\s+)?deployments\b", "deployments"),
            (r"\b(show|list|get)\s+(all\s+)?services\b", "services"),
            (r"\b(show|list|get)\s+(all\s+)?secrets\b", "secrets"),
            (r"\b(show|list|get)\s+(all\s+)?ingresses\b", "ingress"),
        ]
        for pattern, forced in list_guards:
            if re.search(pattern, q):
                force_list_intent = forced
                break

        if q_stripped in {"confirm", "confirm apply", "apply now", "yes apply", "confirm deployment"}:
            intent_type = "mutation_confirm"
        elif q_stripped in {"cancel", "cancel apply", "discard", "abort"}:
            intent_type = "mutation_cancel"
        elif q_stripped in {"main menu", "menu", "start over", "starting point", "home"}:
            intent_type = "main_menu"
        # Note: describe deployment/service/ingress/secret are extracted via resource_names, not here
        elif q_stripped in {"describe", "describe pod", "pod describe"}:
            intent_type = "describe_pod"
        elif q_stripped in {"logs", "log"}:
            intent_type = "logs"
        elif re.match(r"^edit\s+deployment\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "deployment_edit"
        elif re.match(r"^edit\s+service\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "service_edit"
        elif re.match(r"^edit\s+ingress\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "ingress_edit"
        elif re.match(r"^edit\s+secret\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "secret_edit"
        elif re.match(r"^edit\s+resourcequota\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "resourcequota_edit"
        elif re.match(r"^(?:log|logs)\s+[a-z0-9-]+(?:\s+\d+\s*lines?)?$", q_stripped):
            intent_type = "logs"
        elif re.match(r"^(?:select\s+)?pod\s+[a-z0-9][a-z0-9._-]*$", q_stripped) or re.match(r"^select\s+[a-z0-9][a-z0-9._-]*$", q_stripped):
            intent_type = "pod_select"
        elif any(phrase in q for phrase in [
            "image pull", "imagepullbackoff", "errimagepull", "pull backoff", "pull off",
            "can't pull image", "cannot pull image", "failed to pull image",
        ]):
            intent_type = "image_pull_help"

        k8s_error_keywords = [
            "crashloopbackoff", "oomkilled", "failedmount", "failedscheduling", "evicted",
            "liveness probe", "readiness probe", "startup probe", "back-off", "backoff",
            "forbidden", "unauthorized", "x509", "tls", "i/o timeout", "no such host",
            "connection refused", "insufficient cpu", "insufficient memory", "errimagepull",
            "imagepullbackoff", "createcontainerconfigerror", "createcontainererror",
        ]
        if intent_type == "summary" and any(k in q for k in k8s_error_keywords):
            intent_type = "k8s_issue_help"

        if intent_type == "summary":
            # Explicit resource type: describe service NAME, describe ingress NAME, etc.
            m_explicit_deployment = re.match(r"^describe\s+deployment\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            m_explicit_service = re.match(r"^describe\s+service\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            m_explicit_ingress = re.match(r"^describe\s+ingress\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            m_explicit_secret = re.match(r"^describe\s+secret\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            
            if m_explicit_deployment:
                intent_type = "deployment_describe"
                generic_describe_key = "deployment_name"
                generic_describe_name = m_explicit_deployment.group(1)
            elif m_explicit_service:
                intent_type = "service_describe"
                generic_describe_key = "service_name"
                generic_describe_name = m_explicit_service.group(1)
            elif m_explicit_ingress:
                intent_type = "ingress_describe"
                generic_describe_key = "ingress_name"
                generic_describe_name = m_explicit_ingress.group(1)
            elif m_explicit_secret:
                intent_type = "secret_describe"
                generic_describe_key = "secret_name"
                generic_describe_name = m_explicit_secret.group(1)
            else:
                # Fallback: guess from NAME pattern
                m = re.match(r"^describe\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
                if m:
                    generic_describe_name = m.group(1)
                    if "secret" in generic_describe_name:
                        intent_type = "secret_describe"
                        generic_describe_key = "secret_name"
                    elif "ingress" in generic_describe_name:
                        intent_type = "ingress_describe"
                        generic_describe_key = "ingress_name"
                    elif "service" in generic_describe_name or generic_describe_name.endswith("-svc"):
                        intent_type = "service_describe"
                        generic_describe_key = "service_name"
                    else:
                        intent_type = "deployment_describe"
                        generic_describe_key = "deployment_name"

        # Resource intents must take precedence over namespace keywords,
        # so queries like "show pods in namespace wildfly-test" map to pods.
        if intent_type == "summary":
            if any(w in q for w in ["pod", "container", "running", "crash", "restart"]):
                intent_type = "pods"
            elif any(w in q for w in ["node", "nodes", "worker node", "cluster node"]):
                intent_type = "nodes"
            elif any(w in q for w in ["services", "show service", "list service", "svc"]):
                intent_type = "services"
            elif any(w in q for w in ["secrets", "show secret", "list secret"]):
                intent_type = "secrets"
            elif any(w in q for w in ["quota", "resource", "limit", "usage", "memory usage", "cpu usage"]):
                intent_type = "quota"
            elif any(w in q for w in ["hpa", "autoscal", "scale", "replica"]):
                intent_type = "hpa"
            elif any(w in q for w in ["ingress", "imgress", "host", "url", "tls", "firewall", "network"]):
                intent_type = "ingress"
            elif any(w in q for w in ["log", "stdout", "stderr", "output"]):
                intent_type = "logs"
            elif any(w in q for w in ["where", "which cluster", "deployed", "location", "cluster is"]):
                intent_type = "where"
            elif any(phrase in q for phrase in [
                "deployment yaml", "deployment file", "full deployment",
            ]):
                intent_type = "deployment_manifest"
            elif any(w in q for w in ["deploy", "rollout", "image version"]):
                intent_type = "deployments"
            elif any(w in q for w in ["k8s version", "kubernetes version", "server version"]):
                intent_type = "version"
            elif any(w in q for w in ["my app", "list app", "what app", "which app", "what can", "access to"]):
                intent_type = "list_apps"
            elif any(
                phrase in q for phrase in [
                    "list namespaces", "show namespaces", "get namespaces", "all namespaces",
                    "list namespace", "show namespace", "get namespace",
                ]
            ):
                intent_type = "namespaces"
            elif "describe" in q and "namespace" not in q:
                intent_type = "describe_pod"

        app_name = None
        app_match = re.search(r"(?:for|of)\s+([a-z0-9-]+)", q)
        if app_match:
            app_name = app_match.group(1)

        pod_name = self._extract_pod_name(q)
        tail_lines = self._extract_tail_lines(q)
        update_data = self._extract_deployment_update(q)
        if update_data:
            intent_type = "deployment_update"
        else:
            update_data = self._extract_service_update(q)
            if update_data:
                intent_type = "service_update"
            else:
                update_data = self._extract_ingress_update(q)
                if update_data:
                    intent_type = "ingress_update"
                else:
                    update_data = self._extract_secret_update(query)
                    if update_data:
                        intent_type = "secret_update"
                    else:
                        update_data = self._extract_resourcequota_update(q)
                        if update_data:
                            intent_type = "resourcequota_update"

        if generic_describe_name and generic_describe_key:
            update_data = update_data or {}
            update_data.setdefault(generic_describe_key, generic_describe_name)

        resource_names = self._extract_resource_names(q)
        if resource_names:
            update_data = {**(update_data or {}), **resource_names}

        if intent_type == "deployment_edit":
            m = re.match(r"^edit\s+deployment\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            if m:
                update_data = update_data or {}
                update_data["deployment_name"] = m.group(1)
        elif intent_type == "service_edit":
            m = re.match(r"^edit\s+service\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            if m:
                update_data = update_data or {}
                update_data["service_name"] = m.group(1)
        elif intent_type == "ingress_edit":
            m = re.match(r"^edit\s+ingress\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            if m:
                update_data = update_data or {}
                update_data["ingress_name"] = m.group(1)
        elif intent_type == "secret_edit":
            m = re.match(r"^edit\s+secret\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            if m:
                update_data = update_data or {}
                update_data["secret_name"] = m.group(1)
        elif intent_type == "resourcequota_edit":
            m = re.match(r"^edit\s+resourcequota\s+([a-z0-9][a-z0-9._-]*)$", q_stripped)
            if m:
                update_data = update_data or {}
                update_data["resourcequota_name"] = m.group(1)

        deployment_name = self._extract_deployment_name(q)
        explicit_manifest = bool(re.search(r"\bshow\s+deployment\s+[a-z0-9][a-z0-9._-]*\b", q))
        if deployment_name:
            if explicit_manifest or intent_type == "deployment_manifest":
                intent_type = "deployment_manifest"
            update_data = update_data or {}
            update_data.setdefault("deployment_name", deployment_name)

        if force_list_intent:
            intent_type = force_list_intent

        namespace = self._extract_namespace(q)
        if not namespace and intent_type not in {"describe_pod", "logs", "pod_select", "general_chat"}:
            token_match = re.fullmatch(r"[a-z0-9][a-z0-9._-]*", q.strip())
            if token_match:
                namespace = q.strip()
                intent_type = "namespace_select"

        if intent_type == "summary" and not app_name and not namespace:
            k8s_resolution_hints = [
                "k8s", "kubernetes", "cluster", "namespace", "pod", "service", "deployment",
                "hpa", "quota", "ingress", "imgress", "issue", "issues", "error", "errors",
                "failed", "not working", "resolution", "resolutions", "troubleshoot", "check",
            ]
            if any(h in q for h in k8s_resolution_hints):
                intent_type = "k8s_issue_help"
            elif not re.fullmatch(r"[a-z0-9-]+", q_stripped):
                intent_type = "general_chat"

        environment = None
        # Match explicit environment intent only; avoid inferring env from namespace names like wildfly-test.
        if re.search(r"\bprod\b", q) and not re.search(r"\bnon[- ]?prod\b", q):
            environment = "prod"
        elif re.search(r"\b(non[- ]?prod|dev|staging)\b", q):
            environment = "nonprod"

        return IntentResult(intent_type=intent_type, app_name=app_name,
                    pod_name=pod_name, tail_lines=tail_lines, environment=environment,
                    namespace=namespace, raw_query=query, extra=update_data or {})

    def _extract_deployment_update(self, query_lower: str) -> Optional[Dict[str, Any]]:
        scale_patterns = [
            r"scale\s+deployment\s+([a-z0-9-]+)\s+to\s+(\d+)",
            r"set\s+replicas\s+(\d+)\s+for\s+deployment\s+([a-z0-9-]+)",
        ]
        for pattern in scale_patterns:
            m = re.search(pattern, query_lower)
            if m:
                if pattern.startswith("scale"):
                    name, reps = m.group(1), m.group(2)
                else:
                    reps, name = m.group(1), m.group(2)
                return {"deployment_name": name, "replicas": int(reps)}

        image_patterns = [
            r"update\s+deployment\s+([a-z0-9-]+)\s+image\s+([a-z0-9./:_-]+)",
            r"set\s+image\s+([a-z0-9./:_-]+)\s+for\s+deployment\s+([a-z0-9-]+)",
        ]
        for pattern in image_patterns:
            m = re.search(pattern, query_lower)
            if m:
                if pattern.startswith("update"):
                    name, image = m.group(1), m.group(2)
                else:
                    image, name = m.group(1), m.group(2)
                return {"deployment_name": name, "image": image}

        return None

    def _extract_service_update(self, query_lower: str) -> Optional[Dict[str, Any]]:
        type_patterns = [
            r"update\s+service\s+([a-z0-9._-]+)\s+type\s+(clusterip|nodeport|loadbalancer|externalname)",
            r"set\s+service\s+([a-z0-9._-]+)\s+type\s+(clusterip|nodeport|loadbalancer|externalname)",
        ]
        for pattern in type_patterns:
            m = re.search(pattern, query_lower)
            if m:
                type_map = {
                    "clusterip": "ClusterIP",
                    "nodeport": "NodePort",
                    "loadbalancer": "LoadBalancer",
                    "externalname": "ExternalName",
                }
                return {"service_name": m.group(1), "service_type": type_map[m.group(2)]}

        port_patterns = [
            r"update\s+service\s+([a-z0-9._-]+)\s+port\s+(\d+)(?:\s+target\s+(\d+))?",
            r"set\s+service\s+([a-z0-9._-]+)\s+port\s+(\d+)(?:\s+target\s+(\d+))?",
        ]
        for pattern in port_patterns:
            m = re.search(pattern, query_lower)
            if m:
                out: Dict[str, Any] = {"service_name": m.group(1), "port": int(m.group(2))}
                if m.group(3):
                    out["target_port"] = int(m.group(3))
                return out
        return None

    def _extract_ingress_update(self, query_lower: str) -> Optional[Dict[str, Any]]:
        patterns = [
            r"update\s+ingress\s+([a-z0-9._-]+)\s+host\s+([a-z0-9.-]+)",
            r"set\s+ingress\s+([a-z0-9._-]+)\s+host\s+([a-z0-9.-]+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, query_lower)
            if m:
                return {"ingress_name": m.group(1), "host": m.group(2)}
        return None

    def _extract_secret_update(self, query_raw: str) -> Optional[Dict[str, Any]]:
        pattern = r"^\s*update\s+secret\s+([a-z0-9._-]+)\s+key\s+([a-zA-Z0-9_./-]+)\s+value\s+(.+?)\s*$"
        m = re.match(pattern, query_raw, re.IGNORECASE)
        if not m:
            return None
        value = m.group(3).strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        return {
            "secret_name": m.group(1).lower(),
            "key": m.group(2),
            "value": value,
        }

    def _extract_resourcequota_update(self, query_lower: str) -> Optional[Dict[str, Any]]:
        name_match = re.search(r"(?:update\s+resourcequota|set\s+resourcequota|set\s+quota)\s+([a-z0-9._-]+)", query_lower)
        if not name_match:
            return None

        hard: Dict[str, str] = {}
        cpu_m = re.search(r"\bcpu\s+([0-9]+m?|[0-9]+(?:\.[0-9]+)?)", query_lower)
        mem_m = re.search(r"\bmemory\s+([0-9]+(?:mi|gi|m|g|ki|ti|pi|ei)?)", query_lower)
        pods_m = re.search(r"\bpods\s+(\d+)", query_lower)

        if cpu_m:
            hard["limits.cpu"] = cpu_m.group(1)
        if mem_m:
            hard["limits.memory"] = mem_m.group(1)
        if pods_m:
            hard["pods"] = pods_m.group(1)

        if not hard:
            return None

        return {
            "resourcequota_name": name_match.group(1),
            "hard": hard,
        }

    def _extract_deployment_name(self, query_lower: str) -> Optional[str]:
        patterns = [
            r"deployment\s+([a-z0-9][a-z0-9._-]*)",
            r"show\s+deployments?\s+([a-z0-9][a-z0-9._-]*)",
        ]
        for pattern in patterns:
            m = re.search(pattern, query_lower)
            if m:
                candidate = m.group(1)
                if candidate not in {"yaml", "file", "manifest", "in", "for", "all"}:
                    return candidate
        return None

    def _extract_resource_names(self, query_lower: str) -> Dict[str, str]:
        out: Dict[str, str] = {}

        patterns = {
            "deployment_name": [r"describe\s+deployment\s+([a-z0-9][a-z0-9._-]*)", r"show\s+deployment\s+([a-z0-9][a-z0-9._-]*)"],
            "service_name": [r"describe\s+service\s+([a-z0-9][a-z0-9._-]*)", r"show\s+service\s+([a-z0-9][a-z0-9._-]*)"],
            "ingress_name": [r"describe\s+ingress\s+([a-z0-9][a-z0-9._-]*)", r"show\s+ingress\s+([a-z0-9][a-z0-9._-]*)"],
            "secret_name": [r"describe\s+secret\s+([a-z0-9][a-z0-9._-]*)", r"show\s+secret\s+([a-z0-9][a-z0-9._-]*)"],
        }
        for key, regexes in patterns.items():
            for rgx in regexes:
                m = re.search(rgx, query_lower)
                if m:
                    out[key] = m.group(1)
                    break
        return out

    def _extract_namespace(self, query_lower: str) -> Optional[str]:
        patterns = [
            r"namespace\s+([a-z0-9-]+)",
            r"ns\s+([a-z0-9-]+)",
            r"in\s+([a-z0-9-]+)\s+namespace",
        ]
        for pattern in patterns:
            m = re.search(pattern, query_lower)
            if m:
                return m.group(1)
        return None

    def _extract_pod_name(self, query_lower: str) -> Optional[str]:
        patterns = [
            r"describe\s+(?:pod\s+)?([a-z0-9][a-z0-9._-]*)",
            r"pod\s+([a-z0-9][a-z0-9._-]*)",
            r"logs?\s+(?:for\s+)?(?:pod\s+)?([a-z0-9][a-z0-9._-]*)",
            r"select\s+(?:pod\s+)?([a-z0-9][a-z0-9._-]*)",
        ]
        for pattern in patterns:
            m = re.search(pattern, query_lower)
            if m:
                value = m.group(1)
                if value not in {"pod", "describe", "logs", "log"} and not value.isdigit():
                    return value
        return None

    def _extract_tail_lines(self, query_lower: str) -> Optional[int]:
        patterns = [
            r"(\d+)\s*lines?",
            r"last\s+(\d+)",
            r"tail\s+(\d+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, query_lower)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
        return None

    def _infer_app(self, query: str, allowed_apps: List[str]) -> Optional[str]:
        if allowed_apps == ["*"]:
            return None
        q = query.lower()
        for app in allowed_apps:
            if app.lower() in q or app.lower().replace("-", " ") in q:
                return app
        return allowed_apps[0] if len(allowed_apps) == 1 else None

    def _normalize_app_name(
        self,
        app_name: Optional[str],
        user: User,
        db: Session,
    ) -> Optional[str]:
        if not app_name:
            return app_name

        allowed_apps = get_user_allowed_apps(user, db)
        if allowed_apps == ["*"]:
            candidates = [
                row[0]
                for row in db.query(ClusterRegistry.app_name)
                .filter(ClusterRegistry.is_active == True)
                .distinct()
                .all()
            ]
        else:
            candidates = allowed_apps

        if app_name in candidates:
            return app_name

        matches = get_close_matches(app_name, candidates, n=1, cutoff=0.75)
        return matches[0] if matches else app_name

    def _infer_app_from_namespace(self, namespace: str, user: User, db: Session) -> Optional[str]:
        allowed = get_user_allowed_apps(user, db)
        rows = db.query(ClusterRegistry).filter(
            ClusterRegistry.namespace == namespace,
            ClusterRegistry.is_active == True,
        ).all()
        for row in rows:
            if allowed == ["*"] or row.app_name in allowed:
                return row.app_name
        return None

    def _get_user_namespaces(
        self,
        user: User,
        db: Session,
        app_name: Optional[str] = None,
    ) -> List[str]:
        allowed = get_user_allowed_apps(user, db)
        q = db.query(ClusterRegistry).filter(ClusterRegistry.is_active == True)
        if app_name:
            q = q.filter(ClusterRegistry.app_name == app_name)
        rows = q.all()
        namespaces = set()
        for row in rows:
            if allowed == ["*"] or row.app_name in allowed:
                namespaces.add(row.namespace)
        return sorted(namespaces)

    def _resolve_pod_name(self, requested: str, pod_names: List[str]) -> Optional[str]:
        if not requested or not pod_names:
            return None

        req = requested.lower().strip()
        lower_to_original = {name.lower(): name for name in pod_names}

        if req in lower_to_original:
            return lower_to_original[req]

        starts_with = [name for name in pod_names if name.lower().startswith(req)]
        if starts_with:
            return starts_with[0]

        contains = [name for name in pod_names if req in name.lower()]
        if contains:
            return contains[0]

        fuzzy = get_close_matches(req, list(lower_to_original.keys()), n=1, cutoff=0.6)
        if fuzzy:
            return lower_to_original[fuzzy[0]]

        return None

    def _infer_single_resource_name(
        self,
        intent: IntentResult,
        db: Session,
        resource_kind: str,
    ) -> Optional[str]:
        if not intent.app_name or not intent.namespace:
            return None

        registry_entries = self._get_registry_entries(intent, db)
        if not registry_entries:
            return None

        names: List[str] = []
        for reg in registry_entries:
            if reg.cluster_name not in self.gateway.list_clusters():
                continue
            ns = intent.namespace or reg.namespace
            if resource_kind == "deployment":
                names.extend([d.name for d in list_deployments(reg.cluster_name, ns, self.gateway)])
            elif resource_kind == "service":
                names.extend([s.get("name") for s in list_services(reg.cluster_name, ns, self.gateway) if s.get("name")])
            elif resource_kind == "ingress":
                names.extend([i.get("name") for i in list_ingresses(reg.cluster_name, ns, self.gateway) if i.get("name")])
            elif resource_kind == "secret":
                names.extend([s.get("name") for s in list_secrets(reg.cluster_name, ns, self.gateway) if s.get("name")])

        unique_names = sorted(set(names))
        return unique_names[0] if len(unique_names) == 1 else None

    # ── Registry ─────────────────────────────────────────────────────────────

    def _get_registry_entries(self, intent: IntentResult, db: Session) -> List[ClusterRegistry]:
        if not intent.app_name:
            return []
        q = db.query(ClusterRegistry).filter(
            ClusterRegistry.app_name == intent.app_name,
            ClusterRegistry.is_active == True,
        )
        if intent.environment:
            env_rows = q.filter(ClusterRegistry.environment == intent.environment).all()
            if env_rows:
                return env_rows
        return q.all()

    # ── Mock data ─────────────────────────────────────────────────────────────

    def _mock_data(self, intent: IntentResult, app_name: str) -> Dict[str, Any]:
        app = MOCK_K8S_DATA.get(app_name, MOCK_K8S_DATA["payments-api"])
        if intent.intent_type == "namespaces":
            return {
                "namespaces": [
                    {
                        "name": app["quota"]["namespace"],
                        "status": "Active",
                        "app_name": app_name,
                        "cluster_name": app["where"]["cluster"],
                        "environment": app["where"].get("environment", "unknown"),
                        "labels": {"app": app_name},
                    }
                ]
            }
        elif intent.intent_type == "pods":
            return {"pods": app["pods"]}
        elif intent.intent_type == "quota":
            return {"quota": app["quota"]}
        elif intent.intent_type == "hpa":
            return {"hpas": app["hpas"]}
        elif intent.intent_type == "ingress":
            return {"ingresses": app["ingresses"],
                    "network_policies": [{"name": f"{app_name}-netpol",
                                          "pod_selector": f"app={app_name}",
                                          "ingress_rules": 2, "egress_rules": 1}]}
        elif intent.intent_type == "deployments":
            return {"deployments": app["deployments"]}
        elif intent.intent_type == "deployment_manifest":
            dep = app["deployments"][0] if app.get("deployments") else {"name": f"{app_name}-deployment"}
            return {
                "deployment_manifest": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": dep.get("name"),
                        "namespace": dep.get("namespace"),
                        "labels": {"app": dep.get("name")},
                    },
                    "spec": {
                        "replicas": dep.get("replicas", 1),
                        "selector": {"matchLabels": {"app": dep.get("name")}},
                        "template": {
                            "metadata": {"labels": {"app": dep.get("name")}},
                            "spec": {
                                "containers": [
                                    {
                                        "name": dep.get("name"),
                                        "image": dep.get("image"),
                                        "ports": [{"containerPort": 8080}],
                                    }
                                ]
                            },
                        },
                        "strategy": {"type": dep.get("strategy", "RollingUpdate")},
                    },
                    "status": {
                        "replicas": dep.get("replicas", 1),
                        "readyReplicas": dep.get("ready_replicas", 1),
                    },
                },
                "deployment_name": dep.get("name"),
            }
        elif intent.intent_type == "deployment_describe":
            name = (intent.extra or {}).get("deployment_name")
            dep = app["deployments"][0] if app.get("deployments") else {"name": name or f"{app_name}-deployment"}
            return {
                "deployment_description": {
                    "name": name or dep.get("name"),
                    "namespace": dep.get("namespace"),
                    "image": dep.get("image"),
                    "replicas": dep.get("replicas", 1),
                    "ready_replicas": dep.get("ready_replicas", 1),
                    "strategy": dep.get("strategy", "RollingUpdate"),
                }
            }
        elif intent.intent_type == "service_describe":
            name = (intent.extra or {}).get("service_name") or f"{app_name}-svc"
            return {
                "service_description": {
                    "name": name,
                    "namespace": app["quota"]["namespace"],
                    "type": "ClusterIP",
                    "ports": ["80->8080/TCP"],
                }
            }
        elif intent.intent_type == "ingress_describe":
            ing = (app.get("ingresses") or [{}])[0]
            return {
                "ingress_description": {
                    "name": (intent.extra or {}).get("ingress_name") or ing.get("name"),
                    "namespace": ing.get("namespace"),
                    "host": ing.get("host"),
                    "tls_enabled": ing.get("tls_enabled"),
                    "backend_service": ing.get("backend_service"),
                    "backend_port": ing.get("backend_port"),
                }
            }
        elif intent.intent_type == "secret_describe":
            name = (intent.extra or {}).get("secret_name") or f"{app_name}-secret"
            return {
                "secret_description": {
                    "name": name,
                    "namespace": app["quota"]["namespace"],
                    "type": "Opaque",
                    "data_keys": ["username", "password"],
                    "data_key_count": 2,
                }
            }
        elif intent.intent_type == "services":
            return {
                "services": [
                    {
                        "name": f"{app_name}-svc",
                        "namespace": app["quota"]["namespace"],
                        "type": "ClusterIP",
                        "cluster_ip": "10.96.12.34",
                        "external": None,
                        "ports": ["80->8080/TCP"],
                        "age": "7d",
                    }
                ]
            }
        elif intent.intent_type == "secrets":
            return {
                "secrets": [
                    {
                        "name": f"{app_name}-secret",
                        "namespace": app["quota"]["namespace"],
                        "type": "Opaque",
                        "data_keys": ["username", "password"],
                        "data_key_count": 2,
                        "age": "7d",
                    }
                ]
            }
        elif intent.intent_type == "nodes":
            return {
                "nodes": [
                    {
                        "name": "sandbox-node-1",
                        "status": "Ready",
                        "roles": ["worker"],
                        "kubelet_version": "v1.29.2",
                        "os_image": "Ubuntu 22.04.4 LTS",
                        "container_runtime": "containerd://1.7.15",
                        "cpu_allocatable": "4",
                        "memory_allocatable": "15Gi",
                        "pods_allocatable": "110",
                        "age": "42d",
                    },
                    {
                        "name": "sandbox-node-2",
                        "status": "Ready",
                        "roles": ["worker"],
                        "kubelet_version": "v1.29.2",
                        "os_image": "Ubuntu 22.04.4 LTS",
                        "container_runtime": "containerd://1.7.15",
                        "cpu_allocatable": "4",
                        "memory_allocatable": "15Gi",
                        "pods_allocatable": "110",
                        "age": "41d",
                    },
                ]
            }
        elif intent.intent_type == "describe_pod":
            pod = None
            if app["pods"]:
                if intent.pod_name:
                    pod = next((p for p in app["pods"] if p.get("name") == intent.pod_name), None)
                pod = pod or app["pods"][0]
            if not pod:
                return {"pod_description": {"error": "No pods available"}}
            return {
                "pod_description": {
                    "name": pod.get("name"),
                    "namespace": pod.get("namespace"),
                    "node": pod.get("node"),
                    "status": pod.get("status"),
                    "pod_ip": "10.42.0.10",
                    "host_ip": "192.168.1.10",
                    "service_account": "default",
                    "qos_class": "Burstable",
                    "start_time": "n/a",
                    "labels": {"app": app_name},
                    "annotations": {},
                    "conditions": [{"type": "Ready", "status": "True", "reason": "ContainersReady"}],
                    "containers": [{
                        "name": pod.get("name", "container"),
                        "image": pod.get("image"),
                        "ready": str(pod.get("status") == "Running"),
                        "restart_count": str(pod.get("restarts", 0)),
                        "state": "Running" if pod.get("status") == "Running" else pod.get("status"),
                    }],
                    "events": [],
                }
            }
        elif intent.intent_type == "where":
            return app["where"]
        elif intent.intent_type == "version":
            return {"k8s_version": app["where"]["k8s_version"]}
        elif intent.intent_type == "logs":
            return {
                "logs": f"[INFO] {app_name} starting up\n[INFO] Connected to database\n[INFO] Listening on :8080\n[INFO] Health check OK",
                "pod": app["pods"][0]["name"],
                "tail_lines": max(100, intent.tail_lines or 100),
            }
        elif intent.intent_type == "image_pull_help":
            return {
                "image_pull_analysis": {
                    "namespace": app["quota"]["namespace"],
                    "pods_checked": [p["name"] for p in app["pods"]],
                    "suspected_pods": [p["name"] for p in app["pods"] if p.get("status") in {"Pending", "CrashLoopBackOff"}],
                    "recommendations": [
                        "Verify image name/tag and registry path.",
                        "Check imagePullSecrets and registry credentials.",
                        "Confirm node egress/network access to container registry.",
                        "If private registry, ensure secret exists in same namespace.",
                        "Review pod events for ErrImagePull/ImagePullBackOff reasons.",
                    ],
                }
            }
        elif intent.intent_type == "k8s_issue_help":
            return {
                "k8s_issue_analysis": {
                    "namespace": app["quota"]["namespace"],
                    "pods_checked": [p["name"] for p in app["pods"]],
                    "detected_issues": [
                        {
                            "type": "pod_health",
                            "pod": p["name"],
                            "status": p.get("status"),
                            "detail": "Non-running status detected" if p.get("status") != "Running" else "Healthy",
                        }
                        for p in app["pods"]
                        if p.get("status") != "Running"
                    ],
                    "recommendations": [
                        "Run describe on impacted pod: describe <pod-name>",
                        "Check logs: log <pod-name> 200 lines",
                        "Check image/tag and registry credentials if pull errors appear.",
                        "Check probe settings and startup timeouts for CrashLoop/Probe failures.",
                        "Check resource requests/limits for OOMKilled or scheduling failures.",
                    ],
                }
            }
        else:
            pods = app["pods"]
            return {"pods": pods, "quota": app["quota"],
                    "deployments": app["deployments"],
                    "cluster": app["where"]["cluster"],
                    "namespace": app["quota"]["namespace"]}

    # ── Real K8s ──────────────────────────────────────────────────────────────

    def _execute_k8s_read(self, intent: IntentResult, reg: ClusterRegistry) -> Dict[str, Any]:
        cluster = reg.cluster_name
        ns = intent.namespace or reg.namespace
        gw = self.gateway
        if intent.intent_type == "namespaces":
            return {"namespaces": [n.model_dump() for n in list_namespaces(cluster, gw)]}
        elif intent.intent_type == "pods":
            return {"pods": [p.model_dump() for p in list_pods(cluster, ns, gw)]}
        elif intent.intent_type == "quota":
            q = get_resource_quota(cluster, ns, gw)
            return {"quota": q.model_dump() if q else None}
        elif intent.intent_type == "hpa":
            return {"hpas": [h.model_dump() for h in get_hpa(cluster, ns, gw)]}
        elif intent.intent_type == "ingress":
            return {"ingresses": [i.model_dump() for i in list_ingresses(cluster, ns, gw)],
                    "network_policies": check_network_policy(cluster, ns, gw)}
        elif intent.intent_type == "deployments":
            return {"deployments": [d.model_dump() for d in list_deployments(cluster, ns, gw)]}
        elif intent.intent_type in {"deployment_manifest", "deployment_edit"}:
            requested_name = (intent.extra or {}).get("deployment_name")
            if requested_name:
                manifest = get_deployment_manifest(cluster, ns, requested_name, gw)
                return {
                    "deployment_manifest": manifest,
                    "deployment_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                    "deployment_name": requested_name,
                    "edit_mode": intent.intent_type == "deployment_edit",
                }

            deps = list_deployments(cluster, ns, gw)
            if not deps:
                return {"error": f"No deployments found in namespace '{ns}'"}

            if len(deps) > 1:
                cmd = "edit deployment" if intent.intent_type == "deployment_edit" else "show deployment"
                return {
                    "deployment_names": [d.name for d in deps],
                    "error": f"Multiple deployments found. Specify one by name: '{cmd} <name> in namespace <ns> for <app>'",
                }

            only_dep = deps[0]
            manifest = get_deployment_manifest(cluster, ns, only_dep.name, gw)
            return {
                "deployment_manifest": manifest,
                "deployment_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                "deployment_name": only_dep.name,
            }
        elif intent.intent_type == "service_edit":
            name = (intent.extra or {}).get("service_name")
            manifest = get_service_manifest(cluster, ns, name, gw)
            return {
                "service_manifest": manifest,
                "service_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                "service_name": name,
            }
        elif intent.intent_type == "ingress_edit":
            name = (intent.extra or {}).get("ingress_name")
            manifest = get_ingress_manifest(cluster, ns, name, gw)
            return {
                "ingress_manifest": manifest,
                "ingress_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                "ingress_name": name,
            }
        elif intent.intent_type == "secret_edit":
            name = (intent.extra or {}).get("secret_name")
            manifest = get_secret_manifest(cluster, ns, name, gw)
            return {
                "secret_manifest": manifest,
                "secret_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                "secret_name": name,
            }
        elif intent.intent_type == "resourcequota_edit":
            name = (intent.extra or {}).get("resourcequota_name")
            manifest = get_resourcequota_manifest(cluster, ns, name, gw)
            return {
                "resourcequota_manifest": manifest,
                "resourcequota_manifest_yaml": yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
                "resourcequota_name": name,
            }
        elif intent.intent_type == "deployment_describe":
            name = (intent.extra or {}).get("deployment_name")
            return {"deployment_description": describe_deployment(cluster, ns, name, gw)}
        elif intent.intent_type == "service_describe":
            name = (intent.extra or {}).get("service_name")
            return {"service_description": describe_service(cluster, ns, name, gw)}
        elif intent.intent_type == "ingress_describe":
            name = (intent.extra or {}).get("ingress_name")
            return {"ingress_description": describe_ingress(cluster, ns, name, gw)}
        elif intent.intent_type == "secret_describe":
            name = (intent.extra or {}).get("secret_name")
            return {"secret_description": describe_secret_metadata(cluster, ns, name, gw)}
        elif intent.intent_type == "services":
            return {"services": list_services(cluster, ns, gw)}
        elif intent.intent_type == "secrets":
            return {"secrets": list_secrets(cluster, ns, gw)}
        elif intent.intent_type == "nodes":
            return {"nodes": list_nodes(cluster, gw)}
        elif intent.intent_type == "logs":
            tail_lines = max(100, intent.tail_lines or 100)
            return {
                "logs": get_pod_logs(cluster, ns, intent.pod_name or "", gw, tail_lines=tail_lines),
                "pod": intent.pod_name or "",
                "tail_lines": tail_lines,
            }
        elif intent.intent_type == "image_pull_help":
            pods = list_pods(cluster, ns, gw)
            checked = [p.name for p in pods]
            suspected = []
            for p in pods[:20]:
                desc = describe_pod(cluster, ns, p.name, gw)
                containers = desc.get("containers") or []
                for c in containers:
                    reason = (c.get("state_reason") or "").lower()
                    state = (c.get("state") or "").lower()
                    if any(x in reason for x in ["imagepullbackoff", "errimagepull", "invalidimagename", "registry"]):
                        suspected.append({
                            "pod": p.name,
                            "container": c.get("name"),
                            "reason": c.get("state_reason"),
                            "message": c.get("state_message") or "",
                        })
                    elif "waiting(imagepullbackoff)" in state or "waiting(errimagepull)" in state:
                        suspected.append({
                            "pod": p.name,
                            "container": c.get("name"),
                            "reason": c.get("state"),
                            "message": c.get("state_message") or "",
                        })
            return {
                "image_pull_analysis": {
                    "namespace": ns,
                    "pods_checked": checked,
                    "suspected_pods": suspected,
                    "recommendations": [
                        "Check pod events: kubectl describe pod <pod> -n <namespace>",
                        "Verify image exists and tag is correct in deployment.",
                        "Validate imagePullSecrets and secret type kubernetes.io/dockerconfigjson.",
                        "Ensure service account has imagePullSecrets reference.",
                        "Confirm node can reach registry (DNS/TLS/Firewall/Proxy).",
                        "If using ACR/ECR/GCR, verify workload identity/role permissions.",
                    ],
                }
            }
        elif intent.intent_type == "k8s_issue_help":
            pods = list_pods(cluster, ns, gw)
            checked = [p.name for p in pods]
            detected_issues = []
            issue_types = set()

            for p in pods[:25]:
                desc = describe_pod(cluster, ns, p.name, gw)
                containers = desc.get("containers") or []
                events = desc.get("events") or []

                for c in containers:
                    reason = (c.get("state_reason") or "").lower()
                    state = (c.get("state") or "").lower()
                    message = (c.get("state_message") or "").lower()
                    last_state = (c.get("last_state") or "").lower()

                    def add_issue(issue_type: str, detail: str):
                        issue_types.add(issue_type)
                        detected_issues.append({
                            "type": issue_type,
                            "pod": p.name,
                            "container": c.get("name"),
                            "detail": detail,
                        })

                    if any(x in reason or x in state for x in ["imagepullbackoff", "errimagepull", "invalidimagename"]):
                        add_issue("image_pull", c.get("state") or c.get("state_reason") or "image pull error")
                    if "crashloopbackoff" in reason or "crashloopbackoff" in state:
                        add_issue("crash_loop", c.get("state") or "CrashLoopBackOff")
                    if "oomkilled" in last_state or "oomkilled" in reason:
                        add_issue("oom_killed", c.get("last_state") or c.get("state_reason") or "OOMKilled")
                    if any(x in message for x in ["readiness probe failed", "liveness probe failed", "startup probe failed"]):
                        add_issue("probe_failure", c.get("state_message") or "Probe failed")
                    if any(x in message for x in ["forbidden", "unauthorized", "permission denied"]):
                        add_issue("auth_rbac", c.get("state_message") or "Authorization/permission issue")
                    if any(x in message for x in ["x509", "tls", "no such host", "i/o timeout", "connection refused"]):
                        add_issue("network_dns_tls", c.get("state_message") or "Network/DNS/TLS issue")

                for e in events:
                    reason = (e.get("reason") or "").lower()
                    msg = (e.get("message") or "").lower()
                    if "failedscheduling" in reason or "insufficient" in msg:
                        issue_types.add("scheduling")
                        detected_issues.append({"type": "scheduling", "pod": p.name, "detail": e.get("message")})
                    if "failedmount" in reason or "unable to attach or mount" in msg:
                        issue_types.add("volume_mount")
                        detected_issues.append({"type": "volume_mount", "pod": p.name, "detail": e.get("message")})

                if p.status != "Running":
                    issue_types.add("pod_not_running")
                    detected_issues.append({"type": "pod_not_running", "pod": p.name, "detail": p.status})

            recs_map = {
                "image_pull": [
                    "Verify image name/tag exists in registry.",
                    "Check imagePullSecrets in namespace and service account bindings.",
                    "Confirm node can access registry endpoint (DNS/TLS/Firewall).",
                ],
                "crash_loop": [
                    "Run log <pod-name> 200 lines to inspect startup errors.",
                    "Validate env vars/config maps/secrets referenced by container.",
                    "Increase startupProbe/livenessProbe timeouts if app starts slowly.",
                ],
                "oom_killed": [
                    "Increase memory limits/requests and check memory leaks.",
                    "Review heap settings (JVM/Node/Python) and reduce startup spikes.",
                ],
                "probe_failure": [
                    "Validate probe path/port and initialDelaySeconds.",
                    "Check app health endpoint behavior under startup load.",
                ],
                "scheduling": [
                    "Check node capacity, taints/tolerations, and resource requests.",
                    "Reduce requests or add node capacity/autoscaler limits.",
                ],
                "volume_mount": [
                    "Verify PVC bound status and storage class availability.",
                    "Check volume/secret/configmap names and namespace scope.",
                ],
                "auth_rbac": [
                    "Check service account, role/clusterrole bindings, and token permissions.",
                    "Validate registry credentials for private images.",
                ],
                "network_dns_tls": [
                    "Check DNS resolution and outbound network policies.",
                    "Validate TLS cert chain/trust and proxy/firewall rules.",
                ],
                "pod_not_running": [
                    "Describe the pod and inspect recent events for exact root cause.",
                ],
            }

            recommendations = []
            for t in sorted(issue_types):
                recommendations.extend(recs_map.get(t, []))
            if not recommendations:
                recommendations = [
                    "Run describe <pod-name> for non-running pods.",
                    "Run log <pod-name> 200 lines for recent failures.",
                    "Check namespace events for scheduling, mount, and probe errors.",
                ]

            return {
                "k8s_issue_analysis": {
                    "namespace": ns,
                    "pods_checked": checked,
                    "detected_issues": detected_issues[:80],
                    "recommendations": recommendations,
                }
            }
        elif intent.intent_type == "describe_pod":
            pod_name = intent.pod_name or ""
            return {"pod_description": describe_pod(cluster, ns, pod_name, gw), "pod": pod_name}
        elif intent.intent_type == "where":
            return {"cluster": cluster, "environment": reg.environment,
                    "namespace": ns, "cloud_provider": reg.cloud_provider,
                    "region": reg.region, "k8s_version": reg.k8s_version}
        elif intent.intent_type == "version":
            return {"k8s_version": get_k8s_version(cluster, gw)}
        else:
            pods = list_pods(cluster, ns, gw)
            q = get_resource_quota(cluster, ns, gw)
            deps = list_deployments(cluster, ns, gw)
            return {"pods": [p.model_dump() for p in pods],
                    "quota": q.model_dump() if q else None,
                    "deployments": [d.model_dump() for d in deps],
                    "cluster": cluster, "namespace": ns}

    def _execute_k8s_mutation(self, intent: IntentResult, reg: ClusterRegistry) -> Dict[str, Any]:
        cluster = reg.cluster_name
        ns = intent.namespace or reg.namespace
        gw = self.gateway

        op = intent.extra or {}

        if intent.intent_type == "deployment_update":
            deployment_name = op.get("deployment_name")
            image = op.get("image")
            replicas = op.get("replicas")
            if not deployment_name:
                raise ValueError("Deployment name is required for update")

            updated = update_deployment(
                cluster_name=cluster,
                namespace=ns,
                deployment_name=deployment_name,
                gateway=gw,
                image=image,
                replicas=replicas,
            )
            return {
                "deployment_update": updated,
                "operation": {
                    "image": image,
                    "replicas": replicas,
                },
            }

        if intent.intent_type == "service_update":
            service_name = op.get("service_name")
            if not service_name:
                raise ValueError("Service name is required for update")
            updated = update_service(
                cluster_name=cluster,
                namespace=ns,
                service_name=service_name,
                gateway=gw,
                service_type=op.get("service_type"),
                port=op.get("port"),
                target_port=op.get("target_port"),
            )
            return {"service_update": updated, "operation": op}

        if intent.intent_type == "ingress_update":
            ingress_name = op.get("ingress_name")
            if not ingress_name:
                raise ValueError("Ingress name is required for update")
            host = op.get("host")
            updated = update_ingress_host(
                cluster_name=cluster,
                namespace=ns,
                ingress_name=ingress_name,
                gateway=gw,
                host=host,
            )
            return {"ingress_update": updated, "operation": {"host": host}}

        if intent.intent_type == "secret_update":
            secret_name = op.get("secret_name")
            key = op.get("key")
            value = op.get("value")
            if not secret_name:
                raise ValueError("Secret name is required for update")
            updated = update_secret_key(
                cluster_name=cluster,
                namespace=ns,
                secret_name=secret_name,
                gateway=gw,
                key=key,
                value=value,
            )
            return {"secret_update": updated, "operation": {"key": key, "value": "***hidden***"}}

        if intent.intent_type == "resourcequota_update":
            quota_name = op.get("resourcequota_name")
            hard = op.get("hard") or {}
            if not quota_name:
                raise ValueError("ResourceQuota name is required for update")
            updated = update_resource_quota(
                cluster_name=cluster,
                namespace=ns,
                quota_name=quota_name,
                gateway=gw,
                hard=hard,
            )
            return {"resourcequota_update": updated, "operation": {"hard": hard}}

        raise ValueError("Unsupported mutation intent")

    # ── Summary ───────────────────────────────────────────────────────────────

    def _generate_summary(self, query, raw_data, intent, user, chat_mode: str = "k8-info") -> str:
        # Detailed resource views should return full output without model summarization.
        if intent.intent_type in {
            "describe_pod", "deployment_manifest", "deployment_edit", "service_edit", "ingress_edit", "secret_edit", "resourcequota_edit", "deployment_describe",
            "service_describe", "ingress_describe", "secret_describe",
            "pods", "deployments", "services", "secrets", "ingress",
            "deployment_update", "service_update", "ingress_update", "secret_update", "resourcequota_update",
        }:
            return self._plain_summary(query, raw_data, intent, user, chat_mode)

        if self._ai_available:
            try:
                return self._ai_summary(query, raw_data, intent, user)
            except Exception as exc:
                logger.warning("AI summary failed, switching to plain text: %s", exc)
                self._ai_available = False
        return self._plain_summary(query, raw_data, intent, user, chat_mode)

    def _ai_summary(self, query, raw_data, intent, user) -> str:
        if self._ai_provider == "github_models":
            return self._github_models_summary(query, raw_data, user)
        return self._anthropic_summary(query, raw_data, user)

    def _general_chat_response(self, query: str, user: User, context: Dict[str, Any]) -> str:
        if self._ai_available:
            try:
                return self._ai_general_chat(query, user, context)
            except Exception as exc:
                logger.warning("General chat AI call failed, using fallback: %s", exc)

        app_ctx = context.get("app_name")
        ns_ctx = context.get("namespace")
        pod_ctx = context.get("pod_name")
        ctx = []
        if app_ctx:
            ctx.append(f"app={app_ctx}")
        if ns_ctx:
            ctx.append(f"namespace={ns_ctx}")
        if pod_ctx:
            ctx.append(f"pod={pod_ctx}")
        ctx_text = ", ".join(ctx) if ctx else "none"
        return (
            "I can help with general questions and Kubernetes operations. "
            f"Current context: {ctx_text}. "
            "If you want cluster details, ask things like 'show pods' or 'describe'."
        )

    def _ai_general_chat(self, query: str, user: User, context: Dict[str, Any]) -> str:
        if self._ai_provider == "github_models":
            return self._github_models_general_chat(query, user, context)
        return self._anthropic_general_chat(query, user, context)

    def _anthropic_summary(self, query, raw_data, user) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=500,
            system=(
                "You are K8S-AI, a Kubernetes operations assistant. "
                f"User: {user.username} ({user.role}). "
                "Summarise the data clearly. Flag CrashLoopBackOff, OOMKilled, "
                "high restarts, or quota above 80%. Under 250 words. Plain text."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Query: {query}\n\nData:\n{json.dumps(raw_data, indent=2, default=str)[:2500]}\n\nSummarise.",
                }
            ],
        )
        return response.content[0].text.strip()

    def _github_models_summary(self, query, raw_data, user) -> str:
        system_prompt = (
            "You are K8S-AI, a Kubernetes operations assistant. "
            f"User: {user.username} ({user.role}). "
            "Summarise the data clearly. Flag CrashLoopBackOff, OOMKilled, "
            "high restarts, or quota above 80%. Under 250 words. Plain text."
        )
        user_prompt = f"Query: {query}\n\nData:\n{json.dumps(raw_data, indent=2, default=str)[:2500]}\n\nSummarise."

        headers = {
            "Authorization": f"Bearer {settings.github_models_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.github_models_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(settings.github_models_endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("No response choices from GitHub model endpoint")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            joined = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
            return joined or "No response"
        if isinstance(content, str):
            return content.strip() or "No response"
        return "No response"

    def _anthropic_general_chat(self, query: str, user: User, context: Dict[str, Any]) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        context_text = json.dumps({
            "app_name": context.get("app_name"),
            "namespace": context.get("namespace"),
            "pod_name": context.get("pod_name"),
        }, default=str)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=500,
            system=(
                "You are K8S-AI assistant in a Kubernetes platform chat. "
                "Answer user questions clearly and concisely. "
                "If asked non-Kubernetes topics, still answer helpfully."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Context: {context_text}\n\nQuestion: {query}",
                }
            ],
        )
        return response.content[0].text.strip()

    def _github_models_general_chat(self, query: str, user: User, context: Dict[str, Any]) -> str:
        system_prompt = (
            "You are K8S-AI assistant in a Kubernetes platform chat. "
            "Answer user questions clearly and concisely. "
            "If asked non-Kubernetes topics, still answer helpfully."
        )
        user_prompt = (
            f"User: {user.username} ({user.role})\n"
            f"Context: {json.dumps({'app_name': context.get('app_name'), 'namespace': context.get('namespace'), 'pod_name': context.get('pod_name')}, default=str)}\n\n"
            f"Question: {query}"
        )

        headers = {
            "Authorization": f"Bearer {settings.github_models_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.github_models_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 500,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(settings.github_models_endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("No response choices from GitHub model endpoint")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            joined = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
            return joined or "No response"
        if isinstance(content, str):
            return content.strip() or "No response"
        return "No response"

    def _plain_summary(self, query, raw_data, intent, user, chat_mode: str = "k8-info") -> str:
        lines = []
        can_edit = chat_mode in {"k8-agent", "k8-autofix"}
        is_demo = any("demo" in c or "mock" in c for c in raw_data.keys())
        if is_demo:
            lines.append("[ DEMO MODE — no live cluster connected. Showing sample data. ]\n")

        for cluster, data in raw_data.items():
            label = cluster.replace(" (demo)", "").replace(" (mock)", "")

            # WHERE
            if intent.intent_type == "where" and "cluster" in data:
                lines.append(f"'{intent.app_name}' is deployed in:")
                lines.append(f"  Cluster       : {data.get('cluster', label)}")
                lines.append(f"  Cloud         : {data.get('cloud_provider', '?')}")
                lines.append(f"  Region        : {data.get('region', '?')}")
                lines.append(f"  Namespace     : {data.get('namespace', '?')}")
                lines.append(f"  Environment   : {data.get('environment', '?')}")
                lines.append(f"  K8s version   : {data.get('k8s_version', '?')}")
                continue

            # PODS
            if "pods" in data:
                pods = data["pods"]
                running = sum(1 for p in pods if p.get("status") == "Running")
                lines.append(f"Pods — '{intent.app_name}' — {label}")
                lines.append(f"  {running}/{len(pods)} pods running\n")
                for p in pods:
                    status = p.get("status", "?")
                    ready = p.get("ready", "?")
                    restarts = p.get("restarts", 0)
                    flag = "⚠ " if restarts > 0 else ""
                    lines.append(f"  - {flag}{p.get('name','?')}")
                    lines.append(f"    Age          : {p.get('age','?')}")
                    lines.append(f"    Status       : {status}")
                    lines.append(f"    Pods running : {ready}")
                crash = [p for p in pods if p.get("status") == "CrashLoopBackOff"]
                if crash:
                    lines.append(f"\n  ⚠ {len(crash)} pod(s) in CrashLoopBackOff")
                    lines.append("    Likely causes: app crash on startup, bad env var, or missing config.")
                    lines.append("    Action: check logs with 'logs <pod-name>'")
                pending = [p for p in pods if p.get("status") == "Pending"]
                if pending:
                    lines.append(f"\n  ⚠ {len(pending)} pod(s) Pending — may be waiting for node resources.")
                if pods:
                    lines.append("\n  Tip: select a pod first using 'select pod <pod-name>' to enable quick 'describe' and 'logs'.")

            # POD DESCRIBE
            if "pod_description" in data:
                pd = data.get("pod_description") or {}
                if pd.get("error"):
                    lines.append(f"\nPod Describe — {label}")
                    lines.append(f"  Error: {pd.get('error')}")
                else:
                    lines.append(f"\nName:           {pd.get('name', '?')}")
                    lines.append(f"Namespace:      {pd.get('namespace', '?')}")
                    lines.append(f"Cluster:        {label}")
                    lines.append(f"Node:           {pd.get('node', '?')}")
                    lines.append(f"Status:         {pd.get('status', '?')}")
                    lines.append(f"Reason:         {pd.get('reason') or '-'}")
                    lines.append(f"Message:        {pd.get('message') or '-'}")
                    lines.append(f"Pod IP:         {pd.get('pod_ip', '?')}")
                    lines.append(f"Host IP:        {pd.get('host_ip', '?')}")
                    lines.append(f"Service Account:{pd.get('service_account', '?')}")
                    lines.append(f"QoS Class:      {pd.get('qos_class', '?')}")
                    lines.append(f"Priority Class: {pd.get('priority_class_name') or '-'}")
                    lines.append(f"Restart Policy: {pd.get('restart_policy') or '-'}")
                    lines.append(f"DNS Policy:     {pd.get('dns_policy') or '-'}")
                    lines.append(f"Scheduler:      {pd.get('scheduler_name') or '-'}")
                    lines.append(f"Start Time:     {pd.get('start_time', '?')}")

                    owner_refs = pd.get("owner_references") or []
                    if owner_refs:
                        lines.append("Controlled By:")
                        for r in owner_refs:
                            lines.append(f"  {r.get('kind','?')}/{r.get('name','?')}")

                    labels = pd.get("labels") or {}
                    if labels:
                        lines.append("Labels:")
                        for k, v in labels.items():
                            lines.append(f"  {k}={v}")
                    annotations = pd.get("annotations") or {}
                    if annotations:
                        lines.append("Annotations:")
                        for k, v in annotations.items():
                            lines.append(f"  {k}: {v}")

                    containers = pd.get("containers") or []
                    if containers:
                        lines.append("Containers:")
                        for c in containers:
                            lines.append(f"  {c.get('name','?')}:")
                            lines.append(f"    Image:        {c.get('image','?')}")
                            lines.append(f"    Ready:        {c.get('ready','?')}")
                            lines.append(f"    Restart Count:{c.get('restart_count','0')}")
                            lines.append(f"    State:        {c.get('state','?')}")
                            if c.get("state_reason"):
                                lines.append(f"    State Reason: {c.get('state_reason')}")
                            if c.get("state_message"):
                                lines.append(f"    State Msg:    {c.get('state_message')}")
                            if c.get("last_state"):
                                lines.append(f"    Last State:   {c.get('last_state')}")
                            if c.get("container_id"):
                                lines.append(f"    Container ID: {c.get('container_id')}")
                            resources = c.get("resources") or {}
                            req = resources.get("requests") or {}
                            lim = resources.get("limits") or {}
                            if req:
                                lines.append("    Requests:")
                                for rk, rv in req.items():
                                    lines.append(f"      {rk}: {rv}")
                            if lim:
                                lines.append("    Limits:")
                                for lk, lv in lim.items():
                                    lines.append(f"      {lk}: {lv}")

                    init_containers = pd.get("init_containers") or []
                    if init_containers:
                        lines.append("Init Containers:")
                        for c in init_containers:
                            lines.append(
                                f"  {c.get('name','?')}: image={c.get('image','?')} "
                                f"ready={c.get('ready','?')} restarts={c.get('restart_count','0')} "
                                f"state={c.get('state','?')}"
                            )

                    conditions = pd.get("conditions") or []
                    if conditions:
                        lines.append("Conditions:")
                        for c in conditions:
                            lines.append(
                                f"  Type={c.get('type','?')}  Status={c.get('status','?')}  "
                                f"Reason={c.get('reason') or 'n/a'}"
                            )

                    events = pd.get("events") or []
                    if events:
                        lines.append("Events:")
                        for e in events:
                            ts = e.get("last_timestamp") or e.get("event_time") or e.get("first_timestamp") or ""
                            ts_prefix = f"[{ts}] " if ts else ""
                            lines.append(f"  {ts_prefix}{e.get('type','?')}  {e.get('reason','?')}  {e.get('message','')}")

                    volumes = pd.get("volumes") or []
                    if volumes:
                        lines.append("Volumes:")
                        for v in volumes:
                            lines.append(f"  {v.get('name', '?')}")
                            vkeys = [k for k in v.keys() if k != 'name' and v.get(k) is not None]
                            for k in vkeys:
                                lines.append(f"    {k}: {v.get(k)}")

                    tolerations = pd.get("tolerations") or []
                    if tolerations:
                        lines.append("Tolerations:")
                        for t in tolerations:
                            key = t.get('key') or ''
                            op = t.get('operator') or ''
                            val = t.get('value') or ''
                            eff = t.get('effect') or ''
                            sec = t.get('toleration_seconds')
                            sec_txt = f", {sec}s" if sec is not None else ""
                            lines.append(f"  {key} {op} {val} {eff}{sec_txt}".strip())

            # NAMESPACES
            if "namespaces" in data:
                namespaces = data["namespaces"]
                lines.append(f"Namespaces — {label}")
                lines.append(f"  Found {len(namespaces)} namespace(s)\n")
                for n in namespaces:
                    lines.append(f"  - {n.get('name', '?')} ({n.get('status', 'Unknown')})")

            # NODES
            if "nodes" in data:
                nodes = data["nodes"]
                ready = sum(1 for n in nodes if n.get("status") == "Ready")
                lines.append(f"\nCluster Nodes — {label}")
                lines.append(f"  Ready: {ready}/{len(nodes)}")
                for n in nodes:
                    roles = ",".join(n.get("roles") or ["worker"])
                    lines.append(
                        f"  - {n.get('name','?')}  {n.get('status','?')}  roles={roles}  "
                        f"cpu={n.get('cpu_allocatable','?')} mem={n.get('memory_allocatable','?')}  age={n.get('age','?')}"
                    )

            # QUOTA
            if "quota" in data and data["quota"]:
                q = data["quota"]
                lines.append(f"\nResource Quota — {label}")
                qname = q.get('name') or '-'
                lines.append(f"  Name   : {qname}")
                cpu_pct = q.get("cpu_percent", 0) or 0
                mem_pct = q.get("memory_percent", 0) or 0
                lines.append(f"  CPU    : {q.get('cpu_used','?')} / {q.get('cpu_limit','?')}  ({cpu_pct}%)")
                lines.append(f"  Memory : {q.get('memory_used','?')} / {q.get('memory_limit','?')}  ({mem_pct}%)")
                lines.append(f"  Pods   : {q.get('pods_used','?')} / {q.get('pods_limit','?')}")
                if qname != '-' and can_edit:
                    lines.append(f"  Edit   : edit resourcequota {qname}")
                if cpu_pct > 80:
                    lines.append("  ⚠ CPU usage above 80% — consider increasing limits or scaling out")
                if mem_pct > 80:
                    lines.append("  ⚠ Memory usage above 80% — risk of OOMKilled pods")

            # HPA
            if "hpas" in data:
                for h in data["hpas"]:
                    lines.append(f"\nHPA — {h.get('name','?')} — {label}")
                    lines.append(f"  Replicas : {h.get('current_replicas','?')} now  |  min {h.get('min_replicas','?')}  max {h.get('max_replicas','?')}")
                    lines.append(f"  CPU load : {h.get('current_cpu_percent','?')}% current  |  {h.get('target_cpu_percent','?')}% target")
                    if (h.get("current_cpu_percent") or 0) > (h.get("target_cpu_percent") or 100):
                        lines.append("  ⚠ CPU above target — HPA may be scaling up soon")

            # INGRESS
            if "ingresses" in data:
                lines.append(f"\nIngress & Network — {label}")
                for i in data["ingresses"]:
                    iname = i.get('name','?')
                    lines.append(f"  - {iname}")
                    lines.append(f"    Host    : {i.get('host','?')}")
                    lines.append(f"    TLS     : {'Enabled ✓' if i.get('tls_enabled') else 'Disabled ⚠'}")
                    lines.append(f"    Address : {i.get('address') or 'pending...'}")
                    if intent.intent_type == "ingress":
                        lines.append(f"    Try     : describe ingress {iname}")
                        if can_edit:
                            lines.append(f"    Edit    : edit ingress {iname}")
            if "network_policies" in data:
                lines.append(f"  Network policies: {len(data['network_policies'])} active")

            # DEPLOYMENTS
            if "deployments" in data and intent.intent_type == "deployments":
                lines.append(f"\nDeployments — {label}")
                for d in data["deployments"]:
                    ready = d.get("ready_replicas", 0)
                    total = d.get("replicas", 0)
                    flag = "⚠ " if ready < total else ""
                    dname = d.get('name','?')
                    lines.append(f"  - {flag}{dname}")
                    lines.append(f"    Age          : {d.get('age','?')}")
                    lines.append(f"    Pods running : {ready}/{total}")
                    lines.append(f"    Try          : describe deployment {dname}")
                    if can_edit:
                        lines.append(f"    Edit         : edit deployment {dname}")

            if "deployment_manifest" in data and intent.intent_type in {"deployment_manifest", "deployment_edit"}:
                if data.get("error"):
                    lines.append(f"\nDeployment Manifest — {label}")
                    lines.append(f"  Error: {data.get('error')}")
                elif data.get("deployment_names"):
                    lines.append(f"\nDeployment Manifest — {label}")
                    lines.append("  Multiple deployments found:")
                    for name in data.get("deployment_names", []):
                        lines.append(f"  - {name}")
                    lines.append("  Use: show deployment <name> in namespace <ns> for <app>")
                else:
                    title = "Deployment Edit — Config" if intent.intent_type == "deployment_edit" else "Deployment Manifest"
                    lines.append(f"\n{title} — {label}")
                    lines.append(f"Name: {data.get('deployment_name') or '?'}")
                    manifest = data.get("deployment_manifest") or {}
                    lines.append(_to_yaml(manifest))
                    if intent.intent_type == "deployment_edit":
                        lines.append("Edit workflow:")
                        lines.append("  1. Update using command examples below.")
                        lines.append("  2. Review preview.")
                        lines.append("  3. Type 'confirm apply' twice to push changes.")
                        dep_name = data.get('deployment_name') or '<name>'
                        lines.append(f"Examples:")
                        lines.append(f"  - scale deployment {dep_name} to 2")
                        lines.append(f"  - update deployment {dep_name} image <image:tag>")

            if "service_manifest" in data and intent.intent_type == "service_edit":
                lines.append(f"\nService Edit — Config — {label}")
                lines.append(f"Name: {data.get('service_name') or '?'}")
                lines.append(_to_yaml(data.get("service_manifest") or {}))

            if "ingress_manifest" in data and intent.intent_type == "ingress_edit":
                lines.append(f"\nIngress Edit — Config — {label}")
                lines.append(f"Name: {data.get('ingress_name') or '?'}")
                lines.append(_to_yaml(data.get("ingress_manifest") or {}))

            if "secret_manifest" in data and intent.intent_type == "secret_edit":
                lines.append(f"\nSecret Edit — Config — {label}")
                lines.append(f"Name: {data.get('secret_name') or '?'}")
                lines.append(_to_yaml(data.get("secret_manifest") or {}))

            if "resourcequota_manifest" in data and intent.intent_type == "resourcequota_edit":
                lines.append(f"\nResourceQuota Edit — Config — {label}")
                lines.append(f"Name: {data.get('resourcequota_name') or '?'}")
                lines.append(_to_yaml(data.get("resourcequota_manifest") or {}))

            if "services" in data and intent.intent_type == "services":
                lines.append(f"\nServices — {label}")
                for s in data["services"]:
                    sname = s.get('name','?')
                    lines.append(f"  - {sname}")
                    lines.append(f"    Age      : {s.get('age','?')}")
                    lines.append(f"    Type     : {s.get('type','?')}")
                    lines.append(f"    External : {s.get('external') or '-'}")
                    lines.append(f"    Try      : describe service {sname}")
                    if can_edit:
                        lines.append(f"    Edit     : edit service {sname}")

            if "secrets" in data and intent.intent_type == "secrets":
                lines.append(f"\nSecrets (metadata only) — {label}")
                for s in data["secrets"]:
                    sname = s.get('name','?')
                    lines.append(f"  - {sname} ({s.get('type','?')})")
                    lines.append(f"    Keys     : {s.get('data_key_count', 0)}")
                    keys = s.get("data_keys") or []
                    if keys:
                        lines.append(f"    Key names: {', '.join(keys[:12])}")
                    lines.append(f"    Try      : describe secret {sname}")
                    if can_edit:
                        lines.append(f"    Edit     : edit secret {sname}")

            if "deployment_description" in data and intent.intent_type == "deployment_describe":
                lines.append(f"\nDeployment Description — {label}")
                lines.append(_to_yaml(data.get("deployment_description") or {}))

            if "service_description" in data and intent.intent_type == "service_describe":
                lines.append(f"\nService Description — {label}")
                lines.append(_to_yaml(data.get("service_description") or {}))

            if "ingress_description" in data and intent.intent_type == "ingress_describe":
                lines.append(f"\nIngress Description — {label}")
                lines.append(_to_yaml(data.get("ingress_description") or {}))

            if "secret_description" in data and intent.intent_type == "secret_describe":
                lines.append(f"\nSecret Description (metadata only) — {label}")
                lines.append(_to_yaml(data.get("secret_description") or {}))

            if "deployment_update" in data:
                upd = data.get("deployment_update") or {}
                op = data.get("operation") or {}
                lines.append(f"\nDeployment Updated — {label}")
                lines.append(f"  Name       : {upd.get('name', '?')}")
                lines.append(f"  Namespace  : {upd.get('namespace', '?')}")
                if op.get("replicas") is not None:
                    lines.append(f"  Replicas   : {upd.get('ready_replicas', 0)}/{upd.get('replicas', '?')} ready")
                if op.get("image"):
                    lines.append(f"  Image      : {upd.get('image', '?')}")

            if "service_update" in data:
                upd = data.get("service_update") or {}
                lines.append(f"\nService Updated — {label}")
                lines.append(f"  Name       : {upd.get('name', '?')}")
                lines.append(f"  Namespace  : {upd.get('namespace', '?')}")
                lines.append(f"  Type       : {upd.get('type', '?')}")
                ports = upd.get("ports") or []
                if ports:
                    lines.append(f"  Ports      : {', '.join(ports)}")

            if "ingress_update" in data:
                upd = data.get("ingress_update") or {}
                lines.append(f"\nIngress Updated — {label}")
                lines.append(f"  Name       : {upd.get('name', '?')}")
                lines.append(f"  Namespace  : {upd.get('namespace', '?')}")
                lines.append(f"  Host       : {upd.get('host', '?')}")

            if "secret_update" in data:
                upd = data.get("secret_update") or {}
                lines.append(f"\nSecret Updated — {label}")
                lines.append(f"  Name       : {upd.get('name', '?')}")
                lines.append(f"  Namespace  : {upd.get('namespace', '?')}")
                lines.append(f"  Updated key: {upd.get('updated_key', '?')}")
                lines.append(f"  Keys total : {upd.get('data_key_count', 0)}")

            if "resourcequota_update" in data:
                upd = data.get("resourcequota_update") or {}
                lines.append(f"\nResourceQuota Updated — {label}")
                lines.append(f"  Name       : {upd.get('name', '?')}")
                lines.append(f"  Namespace  : {upd.get('namespace', '?')}")
                hard = upd.get("hard") or {}
                for k, v in hard.items():
                    lines.append(f"  {k:<10}: {v}")

            # LOGS
            if "logs" in data:
                lines.append(f"\nLogs (last {data.get('tail_lines', 100)} lines) — {data.get('pod','?')} — {label}")
                lines.append(data.get("logs", "No logs available"))

            # IMAGE PULL TROUBLESHOOTING
            if "image_pull_analysis" in data:
                ipa = data.get("image_pull_analysis") or {}
                lines.append(f"\nImage Pull Troubleshooting — {label}")
                lines.append(f"  Namespace: {ipa.get('namespace', '?')}")
                checked = ipa.get("pods_checked") or []
                lines.append(f"  Pods checked: {len(checked)}")
                suspected = ipa.get("suspected_pods") or []
                if suspected:
                    lines.append("  Suspected image pull failures:")
                    for s in suspected[:12]:
                        if isinstance(s, dict):
                            lines.append(
                                f"    - {s.get('pod','?')} [{s.get('container','?')}]: "
                                f"{s.get('reason','?')} {s.get('message','')[:180]}"
                            )
                        else:
                            lines.append(f"    - {s}")
                else:
                    lines.append("  No explicit ErrImagePull/ImagePullBackOff found in checked pod states.")

                recs = ipa.get("recommendations") or []
                if recs:
                    lines.append("\n  Recommended fixes:")
                    for r in recs:
                        lines.append(f"    - {r}")

            # GENERAL K8S ISSUE TROUBLESHOOTING
            if "k8s_issue_analysis" in data:
                kia = data.get("k8s_issue_analysis") or {}
                lines.append(f"\nKubernetes Troubleshooting — {label}")
                lines.append(f"  Namespace: {kia.get('namespace', '?')}")
                checked = kia.get("pods_checked") or []
                lines.append(f"  Pods checked: {len(checked)}")
                issues = kia.get("detected_issues") or []
                if issues:
                    lines.append("  Detected issues:")
                    for it in issues[:20]:
                        lines.append(
                            f"    - [{it.get('type','issue')}] {it.get('pod','?')}"
                            f"{f'/{it.get('container')}' if it.get('container') else ''}: {it.get('detail','')[:200]}"
                        )
                else:
                    lines.append("  No explicit pod-level issue signatures detected in current sample.")

                recs = kia.get("recommendations") or []
                if recs:
                    lines.append("\n  Recommended fixes:")
                    for r in recs:
                        lines.append(f"    - {r}")

            # VERSION
            if "k8s_version" in data and intent.intent_type == "version":
                lines.append(f"\nKubernetes server version — {label}: {data['k8s_version']}")

        if not lines:
            lines.append(f"No data found for: {query}")
            lines.append("Try: 'show pods for payments-api'  or  'quota for auth-service'")

        return "\n".join(lines)

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _write_audit(
        self,
        user: User,
        db: Session,
        action: str,
        resource_type: str,
        query_text: str,
        result_summary: str,
        app_name: str = "",
        cluster_name: str = "",
        namespace: str = "",
        success: bool = True,
    ) -> None:
        """Append an immutable audit record to the database."""
        try:
            log = AuditLog(
                user_id=user.id,
                username=user.username,
                action=action,
                resource_type=resource_type,
                app_name=app_name,
                cluster_name=cluster_name,
                namespace=namespace,
                query_text=query_text,
                result_summary=result_summary,
                success=success,
            )
            db.add(log)
            db.commit()
        except Exception as exc:
            logger.error("Audit write failed: %s", exc)
