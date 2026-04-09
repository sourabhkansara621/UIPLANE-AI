"""
main.py
-------
FastAPI application entry point for K8S-AI Platform — Phase 1.

Startup sequence
----------------
1. Load settings from .env
2. Create DB tables (if not exist)
3. Load all Kubernetes cluster kubeconfigs
4. Mount all API routers
5. Serve the chat UI at /

Usage
-----
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from models.database import create_tables
from gateway.cluster_gateway import get_gateway
from models.schemas import HealthResponse
from api import (
    auth_router,
    registry_router,
    chat_router,
    k8s_router,
    audit_router,
    mcp_router,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("k8s_ai")
settings = get_settings()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler.
    Runs on startup: initialise DB and cluster gateway.
    Runs on shutdown: clean up connections.
    """
    logger.info("=== K8S-AI Platform starting (Phase 1) ===")

    # 1. Create database tables
    try:
        create_tables()
        logger.info("Database tables ready")
    except Exception as exc:
        logger.error("DB initialisation failed: %s", exc)

    # 2. Load Kubernetes cluster configs
    try:
        gateway = get_gateway()
        count = gateway.get_connected_count()
        logger.info("Cluster gateway ready — %d cluster(s) loaded", count)
    except Exception as exc:
        logger.warning("Cluster gateway init warning: %s", exc)

    logger.info("=== Startup complete. Listening on port 8000 ===")
    yield

    logger.info("=== K8S-AI Platform shutting down ===")


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="K8S-AI Platform",
    description=(
        "AI-powered Kubernetes management platform. "
        "Phase 1: Multi-cluster read-only queries with RBAC auth."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else ["https://yourdomain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files ──────────────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "ui" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── API Routers ───────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(registry_router)
app.include_router(chat_router)
app.include_router(k8s_router)
app.include_router(audit_router)
app.include_router(mcp_router)


# ── UI route ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_ui():
    """Serve the chat UI."""
    template_path = Path(__file__).parent / "ui" / "templates" / "index.html"
    if template_path.exists():
        return HTMLResponse(content=template_path.read_text())
    return HTMLResponse("<h1>K8S-AI Platform</h1><p>UI not found. See /docs</p>")


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """
    System health endpoint.
    Returns connectivity status for DB, Redis, and K8s clusters.
    """
    gateway = get_gateway()
    clusters_connected = gateway.get_connected_count()

    db_ok = False
    try:
        from models.database import SessionLocal
        with SessionLocal() as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        clusters_connected=clusters_connected,
        db_connected=db_ok,
        redis_connected=False,  # Phase 2: wire Redis
    )


# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s: %s", request.url, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check logs."},
    )
