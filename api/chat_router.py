"""
api/chat_router.py
------------------
FastAPI router for the AI chat / query interface.

Routes
------
POST /api/chat/query          Send a natural-language K8s query
GET  /api/chat/history        Get recent chat history for current user
GET  /api/chat/suggestions    Get query suggestions based on user's apps
"""

import uuid
import yaml
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from models.database import get_db, User, AuditLog, ClusterRegistry
from models.schemas import ChatQueryRequest, ChatQueryResponse, AuditLogOut, SaveDeploymentRequest
from auth.rbac import get_current_active_user, get_user_allowed_apps, require_mutation_permission, check_mutation_permission
from agents.read_agent import ReadAgent, SESSION_CONTEXT
from gateway.cluster_gateway import ClusterGateway, get_gateway
from capabilities.k8s_reader import list_namespaces, get_deployment_manifest
from capabilities.k8s_writer import update_deployment

router = APIRouter(prefix="/api/chat", tags=["AI Chat"])


# ── Dependency: ReadAgent ────────────────────────────────────────────────────

def get_read_agent(gateway: ClusterGateway = Depends(get_gateway)) -> ReadAgent:
    """
    FastAPI dependency that provides a ReadAgent instance.
    
    The ReadAgent is the AI brain that:
    - Parses natural language queries
    - Translates them to Kubernetes operations
    - Fetches live cluster data
    - Generates human-readable summaries
    
    Args:
        gateway: ClusterGateway for accessing Kubernetes clusters
        
    Returns:
        ReadAgent instance configured with the cluster gateway
        
    Note:
        This is a dependency function - FastAPI will call it automatically
        when endpoints declare agent: ReadAgent = Depends(get_read_agent)
    """
    return ReadAgent(gateway=gateway)


# ── Query endpoint ────────────────────────────────────────────────────────────

@router.post("/query", response_model=ChatQueryResponse)
def query(
    request: ChatQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    agent: ReadAgent = Depends(get_read_agent),
):
    """
    Main AI-powered Kubernetes query endpoint.
    
    Send natural language questions about your Kubernetes resources and receive
    intelligent, context-aware responses with live cluster data.
    
    Args:
        request: ChatQueryRequest containing:
            - query: Natural language question (e.g., "Show pods in payments-api")
            - session_id: Optional session ID for conversation context
            - chat_mode: One of 'k8-info', 'k8-agent', 'k8-autofix'
        db: Database session
        current_user: Authenticated user (determines app access)
        agent: ReadAgent AI instance
        
    Returns:
        ChatQueryResponse with:
        - response: Human-readable answer
        - data: Structured Kubernetes resource data (if applicable)
        - session_id: Session ID for follow-up queries
        - intent: Detected intent type
        
    Processing Flow:
        1. AI parses the query to detect intent (list pods, get logs, etc.)
        2. Extracts app name, namespace, and other parameters
        3. Checks user has permission to access the app (RBAC)
        4. Fetches live data from the appropriate Kubernetes cluster
        5. Generates human-readable summary with AI (if available)
        6. Logs the query in audit trail
        
    Example Queries:
        - "List namespaces for sandbox"
        - "Show pods in namespace default for sandbox"
        - "What is the resource quota in namespace default for sandbox?"
        - "Get HPA settings in namespace default for sandbox"
        - "Show ingress config in namespace default for sandbox"
        - "Get logs for pod payments-api-7d9f8-xk2p"
        - "Describe pod auth-svc-6b8d9-qw3e"
        
    Chat Modes:
        - k8-info: Read-only queries (default, safest)
        - k8-agent: Allows write operations with confirmation
        - k8-autofix: AI can suggest and apply fixes automatically
    """
    # Generate or use existing session ID for conversation context
    session_id = request.session_id or str(uuid.uuid4())

    # Process the query through the AI agent
    response = agent.process_query(
        query=request.query,
        user=current_user,
        db=db,
        session_id=session_id,
        chat_mode=request.chat_mode,
    )
    return response


@router.get("/llm-status")
def get_llm_status(
    current_user: User = Depends(get_current_active_user),
    agent: ReadAgent = Depends(get_read_agent),
):
    """
    Get current LLM (AI model) provider and status for diagnostics.
    
    This endpoint returns information about which AI backend is currently
    being used and its availability status.
    
    Args:
        current_user: Authenticated user
        agent: ReadAgent instance
        
    Returns:
        Dictionary containing:
        - provider: AI provider name (e.g., "anthropic", "openai", "fallback")
        - model: Model name (e.g., "claude-3-sonnet", "gpt-4")
        - status: Availability status
        - user: Current username
        
    Usage:
        Call this to verify AI functionality or diagnose issues with
        natural language processing.
    """
    status = agent.get_llm_status()
    status["user"] = current_user.username
    return status


# ── Chat history ──────────────────────────────────────────────────────────────

@router.get("/history", response_model=List[AuditLogOut])
def get_chat_history(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get the current user's recent query history from the audit log.
    
    Returns past queries made by this user to help with:
    - Tracking what operations were performed
    - Repeating previous queries
    - Understanding user's recent activity
    
    Args:
        limit: Maximum number of history items to return (capped at 100)
        db: Database session
        current_user: Authenticated user
        
    Returns:
        List of AuditLogOut objects containing:
        - id: Log entry ID
        - user_id: User who made the query
        - action: Action type (always "READ" for this endpoint)
        - resource: Resource queried (app name, pod name, etc.)
        - timestamp: When the query was made
        - details: Additional query details
        
    Note:
        Only returns READ actions, not mutations or login events.
        Ordered by timestamp descending (most recent first).
    """
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id == current_user.id,
            AuditLog.action == "READ",
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(min(limit, 100))  # Cap at 100 to prevent abuse
        .all()
    )
    return logs


# ── Query suggestions ─────────────────────────────────────────────────────────

@router.get("/suggestions")
def get_suggestions(
    session_id: Optional[str] = None,
    chat_mode: Optional[str] = "k8-info",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return contextual query suggestions based on the user's allowed apps.
    Helps new users discover what they can ask.
    """
    allowed_apps = get_user_allowed_apps(current_user, db)

    if not allowed_apps or allowed_apps == ["*"]:
        apps_sample = [
            row[0]
            for row in db.query(ClusterRegistry.app_name)
            .filter(ClusterRegistry.is_active == True)
            .distinct()
            .all()
        ]
    else:
        apps_sample = allowed_apps[:2]

    if not apps_sample:
        apps_sample = ["sandbox"]

    context = SESSION_CONTEXT.get(session_id or "", {}) if session_id else {}
    selected_app = context.get("app_name")
    selected_namespace = context.get("namespace")
    selected_pod = context.get("pod_name")
    pending_mutation = context.get("pending_mutation")
    mode = (chat_mode or "k8-info").strip().lower()
    if mode not in {"k8-info", "k8-agent", "k8-autofix"}:
        mode = "k8-info"

    if selected_app and selected_app in apps_sample:
        apps_sample = [selected_app] + [a for a in apps_sample if a != selected_app]

    def _default_namespace_for_app(app_name: str) -> str:
        ns_row = (
            db.query(ClusterRegistry.namespace)
            .filter(
                ClusterRegistry.app_name == app_name,
                ClusterRegistry.is_active == True,
            )
            .order_by(ClusterRegistry.namespace.asc())
            .first()
        )
        return ns_row[0] if ns_row else "default"

    suggestions = []
    for app in apps_sample:
        ns = selected_namespace if selected_namespace and selected_app == app else _default_namespace_for_app(app)
        suggestions.extend([
            f"List namespaces for {app}",
            f"Show all pods in namespace {ns} for {app}",
            f"Show all deployments in namespace {ns} for {app}",
            f"Show deployment file in namespace {ns} for {app}",
            f"Show all services in namespace {ns} for {app}",
            f"Show all secrets in namespace {ns} for {app}",
            f"What is the resource quota in namespace {ns} for {app}?",
            f"Get HPA settings in namespace {ns} for {app}",
            f"Show all ingresses in namespace {ns} for {app}",
            f"Show node status for {app}",
            f"Are there any pod restarts in namespace {ns} for {app}?",
        ])

        if mode in {"k8-agent", "k8-autofix"}:
            suggestions.extend([
                f"Scale deployment <name> to 2 in namespace {ns} for {app}",
                f"Update deployment <name> image <image:tag> in namespace {ns} for {app}",
                f"Update service <name> type LoadBalancer in namespace {ns} for {app}",
                f"Update ingress <name> host <new-host> in namespace {ns} for {app}",
                f"Update secret <name> key <KEY> value <VALUE> in namespace {ns} for {app}",
                f"Update resourcequota <name> cpu 4 memory 8Gi pods 40 in namespace {ns} for {app}",
            ])

    if selected_pod:
        suggestions.insert(0, "describe")
        suggestions.insert(1, f"describe {selected_pod}")
        suggestions.insert(2, "log")
        suggestions.insert(3, f"log {selected_pod}")
        suggestions.insert(4, f"log {selected_pod} 200 lines")

    if pending_mutation:
        suggestions.insert(0, "confirm apply")
        suggestions.insert(1, "cancel apply")

    suggestions.append("What applications do I have access to?")
    suggestions.append("Show me all clusters in the registry")
    suggestions.append("main menu")

    return {
        "suggestions": suggestions[:12],
        "allowed_apps": allowed_apps,
        "user": current_user.username,
        "selected_namespace": selected_namespace,
        "selected_app": selected_app,
        "selected_pod": selected_pod,
    }


@router.get("/namespaces")
def get_namespaces(
    session_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """Return namespaces for sidebar picker based on allowed apps and active session context."""
    allowed_apps = get_user_allowed_apps(current_user, db)
    context = SESSION_CONTEXT.get(session_id or "", {}) if session_id else {}
    selected_app = context.get("app_name")
    selected_namespace = context.get("namespace")

    q = db.query(ClusterRegistry.app_name, ClusterRegistry.namespace, ClusterRegistry.cluster_name).filter(
        ClusterRegistry.is_active == True,
    )
    if allowed_apps != ["*"]:
        if not allowed_apps:
            return {
                "namespaces": [],
                "selected_namespace": selected_namespace,
                "selected_app": selected_app,
            }
        q = q.filter(ClusterRegistry.app_name.in_(allowed_apps))

    rows = q.distinct().all()
    if selected_app:
        rows = [r for r in rows if r[0] == selected_app]

    # Start with DB namespaces as fallback.
    namespaces = {ns for _, ns, _ in rows}

    # Enrich with live namespaces from connected clusters.
    connected_clusters = set(gateway.list_clusters())
    for app_name, _, cluster_name in rows:
        if cluster_name not in connected_clusters:
            continue
        try:
            live_namespaces = list_namespaces(cluster_name, gateway)
            namespaces.update(ns.name for ns in live_namespaces)
        except Exception:
            # Keep fallback namespaces from registry if live read fails.
            continue

    namespaces = sorted(namespaces)

    return {
        "namespaces": namespaces,
        "selected_namespace": selected_namespace,
        "selected_app": selected_app,
    }


@router.post("/namespace/clear")
def clear_selected_namespace(
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
):
    """Clear the selected namespace from the current chat session context."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    context = SESSION_CONTEXT.setdefault(session_id, {})
    context.pop("namespace", None)
    context.pop("pod_name", None)

    return {
        "session_id": session_id,
        "selected_namespace": None,
        "selected_app": context.get("app_name"),
        "user": current_user.username,
    }


@router.post("/save-deployment")
def save_deployment(
    request: SaveDeploymentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    gateway: ClusterGateway = Depends(get_gateway),
):
    """
    Save edited deployment YAML.
    Receives edited YAML content from modal editor and applies it to the cluster.
    """
    context = SESSION_CONTEXT.get(request.session_id, {})
    app_name = request.app_name or context.get("app_name")
    namespace = request.namespace or context.get("namespace")
    
    if not app_name or not namespace:
        raise HTTPException(status_code=400, detail="app_name and namespace are required")

    # RBAC check: verify user has mutation permission on this app
    if not check_mutation_permission(current_user, app_name, db):
        raise HTTPException(
            status_code=403,
            detail=f"403: Mutation denied. You do not have write access to '{app_name}'."
        )

    # Parse YAML and validate manifest shape/kind.
    try:
        deployment_spec = yaml.safe_load(request.yaml_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}")

    if not isinstance(deployment_spec, dict):
        raise HTTPException(status_code=400, detail="Invalid YAML: manifest must be a single Kubernetes object")

    kind_from_yaml = (deployment_spec.get("kind") or "").strip()
    kind_from_request = (request.resource_kind or "").strip()
    supported_kinds = {"deployment", "service", "ingress", "secret", "resourcequota"}

    effective_kind = (kind_from_request or kind_from_yaml).lower()
    if not effective_kind:
        raise HTTPException(status_code=400, detail="Invalid YAML: resource kind is required in YAML kind or request")
    if effective_kind not in supported_kinds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid YAML: unsupported Kubernetes manifest kind '{kind_from_request or kind_from_yaml}'. "
                "Supported kinds: Deployment, Service, Ingress, Secret, ResourceQuota"
            ),
        )

    # If both request and YAML specify kind, they must agree.
    if kind_from_request and kind_from_yaml and kind_from_request.lower() != kind_from_yaml.lower():
        raise HTTPException(
            status_code=400,
            detail=f"Invalid YAML: kind mismatch (request='{kind_from_request}', yaml='{kind_from_yaml}')",
        )

    # Resolve namespace against active registry entries for this app.
    # If client sends a bad namespace (for example deployment name by mistake),
    # auto-correct when a single valid namespace exists.
    active_rows = db.query(ClusterRegistry).filter(
        ClusterRegistry.app_name == app_name,
        ClusterRegistry.is_active == True,
    ).all()

    if not active_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No active cluster found for app '{app_name}'."
        )

    valid_namespaces = sorted({row.namespace for row in active_rows if row.namespace})
    if namespace not in valid_namespaces:
        if len(valid_namespaces) == 1:
            namespace = valid_namespaces[0]
        else:
            context_ns = context.get("namespace")
            if context_ns in valid_namespaces:
                namespace = context_ns
            else:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid namespace '{namespace}' for app '{app_name}'. "
                        f"Valid namespaces: {', '.join(valid_namespaces)}"
                    ),
                )

    # Get cluster info from registry
    cluster_row = db.query(ClusterRegistry).filter(
        ClusterRegistry.app_name == app_name,
        ClusterRegistry.namespace == namespace,
        ClusterRegistry.is_active == True,
    ).first()

    if not cluster_row:
        raise HTTPException(
            status_code=404,
            detail=f"No active cluster found for app '{app_name}' in namespace '{namespace}'"
        )

    cluster_name = cluster_row.cluster_name
    kind = (request.resource_kind or deployment_spec.get("kind") or "").strip()
    manifest_name = ((deployment_spec.get("metadata") or {}).get("name") or "").strip()
    # Prefer YAML metadata.name from editor content; UI fields can be stale across modal transitions.
    resource_name = manifest_name or (request.resource_name or request.deployment_name or "").strip()
    if not resource_name:
        raise HTTPException(status_code=400, detail="Resource name is required in request or metadata.name")
    if not kind:
        raise HTTPException(status_code=400, detail="Resource kind is required in request or YAML kind")

    # Apply the deployment updates reliably (replicas/image first-class + spec patch fallback)
    try:
        from kubernetes import client
        from kubernetes.client import ApiClient
        from kubernetes.client.rest import ApiException

        apps_api = gateway.get_apps_client(cluster_name)
        api_client = ApiClient()

        spec = deployment_spec.get("spec") or {}
        replicas = spec.get("replicas")
        image = None
        containers = (((spec.get("template") or {}).get("spec") or {}).get("containers") or [])
        if containers and isinstance(containers[0], dict):
            image = containers[0].get("image")

        # Ensure we target the actual namespace where the deployment exists.
        if kind.lower() == "deployment":
            try:
                apps_api.read_namespaced_deployment(name=resource_name, namespace=namespace)
            except ApiException as read_exc:
                if int(getattr(read_exc, "status", 0) or 0) == 404:
                    try:
                        all_matches = apps_api.list_deployment_for_all_namespaces(
                            field_selector=f"metadata.name={resource_name}"
                        )
                        matched_items = list(getattr(all_matches, "items", []) or [])
                        if len(matched_items) == 1 and getattr(matched_items[0], "metadata", None):
                            found_ns = (matched_items[0].metadata.namespace or "").strip()
                            if found_ns:
                                namespace = found_ns
                    except Exception:
                        pass
                else:
                    raise

        kind_l = kind.lower()
        core_api = gateway.get_core_client(cluster_name)
        net_api = gateway.get_networking_client(cluster_name)

        def _apply_patch(target_namespace: str):
            local_updated_info = None
            if kind_l == "deployment":
                if replicas is not None or image is not None:
                    parsed_replicas = int(replicas) if replicas is not None else None
                    local_updated_info = update_deployment(
                        cluster_name=cluster_name,
                        namespace=target_namespace,
                        deployment_name=resource_name,
                        gateway=gateway,
                        image=image,
                        replicas=parsed_replicas,
                    )
                    local_updated = apps_api.read_namespaced_deployment(
                        name=resource_name,
                        namespace=target_namespace,
                    )
                else:
                    patch_body = {
                        "apiVersion": deployment_spec.get("apiVersion", "apps/v1"),
                        "kind": "Deployment",
                        "metadata": {
                            "name": resource_name,
                            "namespace": target_namespace,
                            "labels": (deployment_spec.get("metadata") or {}).get("labels") or {},
                        },
                        "spec": spec,
                    }
                    local_updated = apps_api.patch_namespaced_deployment(
                        name=resource_name,
                        namespace=target_namespace,
                        body=patch_body,
                    )
            elif kind_l == "service":
                local_updated = core_api.patch_namespaced_service(name=resource_name, namespace=target_namespace, body=deployment_spec)
            elif kind_l == "ingress":
                local_updated = net_api.patch_namespaced_ingress(name=resource_name, namespace=target_namespace, body=deployment_spec)
            elif kind_l == "secret":
                local_updated = core_api.patch_namespaced_secret(name=resource_name, namespace=target_namespace, body=deployment_spec)
            elif kind_l == "resourcequota":
                local_updated = core_api.patch_namespaced_resource_quota(name=resource_name, namespace=target_namespace, body=deployment_spec)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported manifest kind '{kind}' for editor save")
            return local_updated, local_updated_info

        def _discover_namespace_by_name() -> str | None:
            try:
                if kind_l == "deployment":
                    result = apps_api.list_deployment_for_all_namespaces(field_selector=f"metadata.name={resource_name}")
                elif kind_l == "service":
                    result = core_api.list_service_for_all_namespaces(field_selector=f"metadata.name={resource_name}")
                elif kind_l == "ingress":
                    result = net_api.list_ingress_for_all_namespaces(field_selector=f"metadata.name={resource_name}")
                elif kind_l == "secret":
                    result = core_api.list_secret_for_all_namespaces(field_selector=f"metadata.name={resource_name}")
                elif kind_l == "resourcequota":
                    result = core_api.list_resource_quota_for_all_namespaces(field_selector=f"metadata.name={resource_name}")
                else:
                    return None
                items = list(getattr(result, "items", []) or [])
                if len(items) == 1 and getattr(items[0], "metadata", None):
                    return (items[0].metadata.namespace or "").strip() or None
            except Exception:
                return None
            return None

        try:
            updated, updated_info = _apply_patch(namespace)
        except ApiException as first_err:
            if int(getattr(first_err, "status", 0) or 0) != 404:
                raise
            discovered_ns = _discover_namespace_by_name()
            if discovered_ns and discovered_ns != namespace:
                namespace = discovered_ns
                updated, updated_info = _apply_patch(namespace)
            else:
                raise

        # Audit log
        from models.database import AuditLog
        audit = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            action="MUTATE",
            resource_type=kind_l,
            resource_name=resource_name,
            app_name=app_name,
            cluster_name=cluster_name,
            namespace=namespace,
            query_text=f"save {kind_l} {resource_name}",
            result_summary=f"Updated {kind_l} via editor for {app_name}/{namespace}",
            extra={"source": "editor-modal"},
            success=True,
        )
        db.add(audit)
        db.commit()

        return {
            "status": "success",
            "message": f"{kind} '{resource_name}' saved successfully",
            "deployment_name": resource_name,
            "namespace": namespace,
            "app_name": app_name,
            "session_id": request.session_id,
            "applied_replicas": (updated_info or {}).get("replicas") if updated_info else getattr(getattr(updated, "spec", None), "replicas", None),
            "ready_replicas": (updated_info or {}).get("ready_replicas") if updated_info else getattr(getattr(updated, "status", None), "ready_replicas", None),
            "applied_image": (updated_info or {}).get("image") if updated_info else None,
            "updated_manifest": api_client.sanitize_for_serialization(updated)
        }

    except ApiException as api_err:
        if int(getattr(api_err, "status", 0) or 0) == 404:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Resource '{resource_name}' ({kind}) was not found in namespace '{namespace}' "
                    f"on cluster '{cluster_name}'."
                ),
            )
        raise HTTPException(
            status_code=500,
            detail=f"Kubernetes API error: {api_err.reason}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save resource: {str(e)}"
        )
