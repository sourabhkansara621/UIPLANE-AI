# K8S-AI Platform — Phase 1

AI-powered Kubernetes management platform.
**Phase 1**: Multi-cloud read-only queries with RBAC auth.

---

## What's in Phase 1

| Capability | Status |
|---|---|
| JWT authentication (login / register) | ✅ |
| App-ownership RBAC (user → app → namespace) | ✅ |
| Multi-cluster gateway (GKE, EKS, AKS, Rancher) | ✅ |
| Cluster registry (app → cluster → namespace mapping) | ✅ |
| Natural-language query via Claude AI | ✅ |
| Pods, namespaces, quota, deployments, HPA, ingress | ✅ |
| Pod logs + describe | ✅ |
| NetworkPolicy / firewall check | ✅ |
| Immutable audit log | ✅ |
| Chat UI (dark terminal theme) | ✅ |
| REST API with Swagger docs | ✅ |
| Unit tests | ✅ |

---

## Project Structure

```
k8s_ai_platform/
├── main.py                     # FastAPI app entry point
├── requirements.txt
├── .env.example
│
├── config/
│   └── settings.py             # Pydantic settings from .env
│
├── models/
│   ├── database.py             # SQLAlchemy ORM models
│   └── schemas.py              # Pydantic request/response schemas
│
├── auth/
│   ├── jwt_handler.py          # Token creation, decoding, password hashing
│   ├── rbac.py                 # App-ownership access checks
│   └── auth_service.py         # Login, register, session logic
│
├── gateway/
│   └── cluster_gateway.py      # Multi-cluster K8s client manager
│
├── capabilities/
│   └── k8s_reader.py           # All K8s read operations
│
├── agents/
│   └── read_agent.py           # AI intent parser + query orchestrator
│
├── api/
│   ├── auth_router.py          # /api/auth/* endpoints
│   ├── registry_router.py      # /api/registry/* endpoints
│   ├── chat_router.py          # /api/chat/* endpoints
│   ├── k8s_router.py           # /api/k8s/* endpoints
│   └── audit_router.py         # /api/audit/* endpoints
│
├── utils/
│   └── audit.py                # Audit log query helpers
│
├── ui/
│   └── templates/
│       └── index.html          # Chat UI
│
├── scripts/
│   └── seed_db.py              # Demo data seeder
│
└── tests/
    ├── conftest.py
    ├── test_auth.py
    └── test_k8s_reader.py
```

---

## Quick Start

### 1. Clone & install dependencies

```bash
cd k8s_ai_platform
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set:
#   AI_PROVIDER=auto|anthropic|github_models
#   ANTHROPIC_API_KEY=sk-ant-...
#   GITHUB_MODELS_TOKEN=github_pat_...
#   GITHUB_MODELS_MODEL=openai/gpt-4.1-mini
#   DATABASE_URL=postgresql://user:pass@localhost/k8sai_db
#   K8S_KUBECONFIG_PATHS=/path/to/gke.yaml,/path/to/eks.yaml
#   MCP_ENABLED=true
#   MCP_CLUSTER_ENDPOINTS=https://mcp-gateway.internal/api/clusters
#   MCP_TIMEOUT_SECONDS=10
#   MCP_GKE_KUBECONFIG_PATHS=/secure/gke-dev.yaml,/secure/gke-prod.yaml
#   MCP_EKS_KUBECONFIG_PATHS=/secure/eks-us-east-1.yaml
#   MCP_AKS_KUBECONFIG_PATHS=/secure/aks-shared.yaml
#   MCP_DATADOG_TARGETS=cluster:gke-dev,cluster:eks-us-east-1
```

MCP cluster endpoint response format:

```json
{
  "clusters": [
    {
      "context": "eks-prod-us-east-1",
      "kubeconfig_path": "/secure/path/eks.yaml"
    },
    {
      "context": "aks-shared-eu",
      "kubeconfig": "apiVersion: v1\nkind: Config\n..."
    }
  ]
}
```

When `MCP_ENABLED=true`, the gateway loads contexts from both `K8S_KUBECONFIG_PATHS` and `MCP_CLUSTER_ENDPOINTS`.

Built-in MCP package endpoints:

- `GET /api/mcp/catalog` returns aggregated cluster + observability catalog from clients: GKE, EKS, AKS, Datadog.
- `GET /api/mcp/health` returns MCP status and counts.

Note: MCP endpoints are restricted to `infra-admin` users.

### 3. Start Postgres (Docker)

```bash
docker run -d \
  --name k8sai-postgres \
  -e POSTGRES_USER=k8sai \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=k8sai_db \
  -p 5432:5432 \
  postgres:15
```

### 4. Seed demo data

```bash
python scripts/seed_db.py
```

This creates 4 demo users (password: `demo1234`):
- `priya` — developer — payments-api, billing-service
- `james` — infra-admin — all apps
- `aisha` — developer — auth-service, user-mgmt
- `bob` — developer — web-frontend

### 5. Run the server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 for the chat UI.
Open http://localhost:8000/docs for the Swagger API explorer.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## API Endpoints

### Auth
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/login` | Login, get JWT |
| GET | `/api/auth/me` | Current user profile |
| POST | `/api/auth/register` | Create user (admin only) |
| POST | `/api/auth/grant-access` | Grant app access |
| DELETE | `/api/auth/revoke-access/{user_id}/{app}` | Revoke app access |

### Chat (AI)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/chat/query` | Natural-language K8s query |
| GET | `/api/chat/history` | User's query history |
| GET | `/api/chat/suggestions` | Contextual query suggestions |

### K8s Resources
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/k8s/{app}/pods` | List pods |
| GET | `/api/k8s/{app}/quota` | Resource quota |
| GET | `/api/k8s/{app}/hpa` | HPA config |
| GET | `/api/k8s/{app}/ingress` | Ingress + network policies |
| GET | `/api/k8s/{app}/deployments` | Deployments |
| GET | `/api/k8s/{app}/pods/{pod}/logs` | Pod logs |
| GET | `/api/k8s/{app}/pods/{pod}/describe` | Pod describe |
| GET | `/api/k8s/{app}/version` | K8s version |

### Registry
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/registry/clusters` | All registry entries |
| POST | `/api/registry/clusters` | Register new entry |
| GET | `/api/registry/where/{app}` | Where is app deployed? |
| GET | `/api/registry/health` | Cluster connectivity test |

### Audit
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/audit/logs` | Recent audit logs |
| GET | `/api/audit/denied` | Recent access denials |
| GET | `/api/audit/export` | Export CSV |

---

## Next Phases

- **Phase 2**: Redis session cache, streaming AI responses, VS Code extension
- **Phase 3**: Mutation agent (memory/CPU patching, GitOps, PROD approval gate)
- **Phase 4**: Fix agent (root-cause analysis, auto-remediation)
- **Phase 5**: Datadog webhook integration, auto-remediation loop
