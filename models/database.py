"""
models/database.py
------------------
SQLAlchemy ORM models for:
  - User            : platform users with roles
  - AppOwnership    : maps user → application (RBAC source of truth)
  - ClusterRegistry : maps application → cluster/env/namespace
  - AuditLog        : immutable append-only change log
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer,
    ForeignKey, Text, JSON, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from config.settings import get_settings

Base = declarative_base()
settings = get_settings()

if settings.database_url.startswith("sqlite"):
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=settings.debug,
    )
else:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.debug,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── User ──────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)          # UUID
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(200), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200))
    role = Column(String(50), default="developer")     # developer | team-lead | infra-admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    # relationships
    app_ownerships = relationship("AppOwnership", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")

    def __repr__(self):
        return f"<User {self.username} role={self.role}>"


# ── AppOwnership (RBAC) ───────────────────────────────────────────────────────

class AppOwnership(Base):
    """
    Source of truth for authorization.
    One row = one user is allowed to access one application.
    infra-admin users bypass this table (wildcard access).
    """
    __tablename__ = "app_ownerships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    app_name = Column(String(100), nullable=False)     # e.g. "payments-api"
    can_read = Column(Boolean, default=True)
    can_mutate = Column(Boolean, default=False)        # Phase 3: write access
    granted_by = Column(String(100))                   # username of grantor
    granted_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="app_ownerships")

    def __repr__(self):
        return f"<AppOwnership user={self.user_id} app={self.app_name}>"


# ── ClusterRegistry ───────────────────────────────────────────────────────────

class ClusterRegistry(Base):
    """
    Maps every application to its cluster(s), environment, and namespace.
    This is how the AI knows "payments-api lives in gke-prod-us-east, namespace payments-prod".
    """
    __tablename__ = "cluster_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_name = Column(String(100), nullable=False)
    cluster_name = Column(String(150), nullable=False)
    cloud_provider = Column(String(20))                # GKE | EKS | AKS | Rancher
    environment = Column(String(20))                   # prod | staging | dev | nonprod
    region = Column(String(50))
    namespace = Column(String(100), nullable=False)
    k8s_version = Column(String(20))
    kubeconfig_secret = Column(String(200))            # Vault path to kubeconfig
    is_active = Column(Boolean, default=True)
    registered_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<ClusterRegistry app={self.app_name} "
            f"cluster={self.cluster_name} env={self.environment}>"
        )


# ── AuditLog ──────────────────────────────────────────────────────────────────

class AuditLog(Base):
    """
    Immutable append-only log of every action taken.
    Never updated, never deleted.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"))
    username = Column(String(100))                     # denormalised for safety
    action = Column(String(50))                        # READ | MUTATE | LOGIN | DENIED
    resource_type = Column(String(50))                 # pod | namespace | quota | hpa ...
    resource_name = Column(String(200))
    app_name = Column(String(100))
    cluster_name = Column(String(150))
    namespace = Column(String(100))
    query_text = Column(Text)                          # original natural-language query
    result_summary = Column(Text)
    extra = Column(JSON, default=dict)                 # arbitrary metadata
    timestamp = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=True)

    user = relationship("User", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.username} on {self.resource_name}>"


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db():
    """FastAPI dependency — yields a DB session then closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables. Called once at startup."""
    Base.metadata.create_all(bind=engine)
