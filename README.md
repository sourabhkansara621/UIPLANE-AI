# UNIPLANE AI Platform - Phase 1

UNIPLANE AI Platform is an AI-powered Kubernetes operations platform for multi-cluster environments.

It provides:
- AI-assisted read operations (`k8-info`)
- Controlled write operations (`k8-agent`)
- Datadog-driven remediation workflows (`k8-autofix`)
- RBAC + audit-first governance
- MCP-based discovery across AKS, EKS, GKE, and Datadog

## What Is Included In Phase 1

| Capability | Status |
|---|---|
| JWT authentication and session profile APIs | Done |
| Role-based access control (viewer/editor/admin/infra-admin) | Done |
| App-level ownership checks before K8s operations | Done |
| Multi-cluster gateway (AKS, EKS, GKE, Rancher/on-prem) | Done |
| Cluster registry (app -> cluster -> namespace mapping) | Done |
| Chat-driven read and write workflows | Done |
| Datadog namespace issue ingestion | Done |
| Autofix actions (restart/scale/resource patch/config patch) | Done |
| MCP catalog and health endpoints | Done |
| Audit logging and export | Done |
| Admin DB browser (read-only SQL) | Done |
| Day/Dark UI theme toggle | Done |

## High-Level Architecture

- `main.py` starts FastAPI, initializes DB, loads cluster gateway, and mounts routers.
- `gateway/cluster_gateway.py` manages Kubernetes clients per cluster.
- `agents/read_agent.py` orchestrates prompt handling, context, and K8s intent resolution.
- `api/*_router.py` exposes auth, chat, k8s, registry, audit, MCP, and admin DB routes.
- `mcp/server.py` aggregates data from MCP clients:
	- `mcp/clients/gke_client.py`
	- `mcp/clients/eks_client.py`
	- `mcp/clients/aks_client.py`
	- `mcp/clients/datadog_client.py`
- `models/database.py` defines ORM models for users, app ownership, cluster registry, and audit logs.

## Repository Structure

```text
k8s_ai_platform/
	main.py
	requirements.txt
	.env.example
	config/
		settings.py
	models/
		database.py
		schemas.py
	auth/
		auth_service.py
		jwt_handler.py
		rbac.py
	gateway/
		cluster_gateway.py
	capabilities/
		k8s_reader.py
		k8s_writer.py
	agents/
		read_agent.py
	api/
		auth_router.py
		chat_router.py
		k8s_router.py
		registry_router.py
		audit_router.py
		mcp_router.py
		admin_db_router.py
	mcp/
		server.py
		schemas.py
		clients/
	ui/
		templates/
			index.html
			admin_db.html
		static/
			css/
			js/
	scripts/
		seed_db.py
	tests/
```

## Quick Start

### 1) Create virtual environment and install dependencies

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Configure environment

```powershell
Copy-Item .env.example .env
```

Set at minimum:
- `JWT_SECRET_KEY`
- `DATABASE_URL`
- `K8S_KUBECONFIG_PATHS`
- Optional AI provider credentials:
	- `ANTHROPIC_API_KEY`
	- `GITHUB_MODELS_TOKEN`

Optional MCP/Datadog settings:
- `MCP_ENABLED`
- `MCP_CLUSTER_ENDPOINTS`
- `MCP_GKE_KUBECONFIG_PATHS`
- `MCP_EKS_KUBECONFIG_PATHS`
- `MCP_AKS_KUBECONFIG_PATHS`
- `DATADOG_API_KEY`
- `DATADOG_APP_KEY`
- `DATADOG_SITE`

### 3) Start Postgres (if using local Docker)

```powershell
docker run -d --name k8sai-postgres -e POSTGRES_USER=k8sai -e POSTGRES_PASSWORD=password -e POSTGRES_DB=k8sai_db -p 5432:5432 postgres:15
```

### 4) Seed demo data

```powershell
python scripts/seed_db.py
```

Demo users (password: `demo1234`):
- `priya` (developer)
- `james` (infra-admin)
- `aisha` (developer)
- `bob` (developer)

### 5) Run the app

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open:
- UI: `http://localhost:8000/`
- Admin DB UI: `http://localhost:8000/admin/db`
- Swagger docs: `http://localhost:8000/docs`

## Core API Overview

### System
- `GET /health`

### Auth (`/api/auth`)
- `POST /login`
- `POST /login/form`
- `POST /register`
- `GET /me`
- `POST /logout`
- `POST /grant-access`
- `DELETE /revoke-access/{user_id}/{app_name}`
- `GET /users`

### Chat (`/api/chat`)
- `POST /query`
- `GET /llm-status`
- `GET /history`
- `GET /suggestions`
- `GET /namespaces`
- `POST /namespace/clear`
- `POST /save-deployment`

### Kubernetes (`/api/k8s`)
- `GET /upgrade/{cluster_name}/versions`
- `POST /upgrade/{cluster_name}`
- `GET /{app_name}/pods`
- `GET /{app_name}/namespaces`
- `GET /{app_name}/quota`
- `GET /{app_name}/deployments`
- `GET /{app_name}/hpa`
- `GET /{app_name}/ingress`
- `GET /{app_name}/pods/{pod_name}/logs`
- `GET /{app_name}/pods/{pod_name}/describe`
- `GET /{app_name}/version`

### Registry (`/api/registry`)
- `GET /clusters`
- `POST /clusters`
- `GET /clusters/{app_name}`
- `GET /where/{app_name}`
- `DELETE /clusters/{entry_id}`
- `GET /health`

### Audit (`/api/audit`)
- `GET /logs`
- `GET /logs/app/{app_name}`
- `GET /denied`
- `GET /export`
- `POST /log` (debug-only route in development)

### MCP (`/api/mcp`)
- `GET /catalog` (infra-admin)
- `GET /health` (infra-admin)
- `GET /datadog/issues`
- `POST /autofix/apply`

### Admin DB (`/api/admin/db`)
- `GET /tables`
- `POST /query`
- `GET /ui`

## MCP And Autofix Notes

- MCP server aggregates discovery from cloud clients and observability clients.
- MCP health returns `ok` or `degraded` based on client errors.
- Datadog issues are namespace-focused and can be filtered by cluster and time range.
- Autofix endpoint supports actions:
	- `restart`
	- `scale_up`
	- `increase_memory`
	- `increase_cpu`
	- `patch_config`
- Mutation actions enforce RBAC write checks before applying changes.

## Security Model

- JWT bearer authentication on protected routes.
- App-level ownership authorization before K8s operations.
- `infra-admin` role has wildcard cross-app access.
- All mutation flows are auditable.
- Admin DB API enforces read-only query policy.

## Testing

Run tests:

```powershell
pytest tests -v
```

## Troubleshooting

- If no clusters appear, verify `K8S_KUBECONFIG_PATHS` and file permissions.
- If AI responses fallback to plain summaries, verify provider keys (`ANTHROPIC_API_KEY` or `GITHUB_MODELS_TOKEN`).
- If Datadog issues are empty, verify `DATADOG_API_KEY`, `DATADOG_APP_KEY`, and `DATADOG_SITE`.
- If auth fails, check `JWT_SECRET_KEY` consistency and token expiry settings.

## Roadmap Snapshot

- Phase 2: richer live workflows and integration enhancements
- Phase 3: deeper automation with stronger guardrails
- Phase 4+: advanced remediation, policy, and platform extensions
