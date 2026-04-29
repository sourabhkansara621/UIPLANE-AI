"""
scripts/seed_db.py
------------------
Seed the database with demo users, app ownerships, and cluster registry entries.
Run once after first startup:

    python scripts/seed_db.py

This creates the same data as the Phase 1 demo UI so you can test immediately.
"""

import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.database import SessionLocal, create_tables, User, AppOwnership, ClusterRegistry
from auth.jwt_handler import hash_password

# ── Demo users ────────────────────────────────────────────────────────────────

DEMO_USERS = [
    {
        "id": str(uuid.uuid4()),
        "username": "priya",
        "email": "priya@company.com",
        "password": "demo1234",
        "full_name": "Priya S.",
        "role": "developer",
        "apps": [
            ("sandbox", True, False),
            ("EKS", True, False),
            ("GKE", True, False),
            ("AKS", True, False),
            ("on-prem Rancher", True, False),
        ],
    },
    {
        "id": str(uuid.uuid4()),
        "username": "james",
        "email": "james@company.com",
        "password": "demo1234",
        "full_name": "James K.",
        "role": "infra-admin",
        "apps": [],  # infra-admin has wildcard access, no explicit rows needed
    },
    {
        "id": str(uuid.uuid4()),
        "username": "aisha",
        "email": "aisha@company.com",
        "password": "demo1234",
        "full_name": "Aisha M.",
        "role": "developer",
        "apps": [("auth-service", True, False), ("user-mgmt", True, False)],
    },
    {
        "id": str(uuid.uuid4()),
        "username": "bob",
        "email": "bob@company.com",
        "password": "demo1234",
        "full_name": "Bob T.",
        "role": "developer",
        "apps": [("web-frontend", True, False)],
    },
]

# ── Cluster registry ──────────────────────────────────────────────────────────

CLUSTER_ENTRIES = [
    # payments-api
    dict(app_name="payments-api", cluster_name="gke-prod-us-east",     cloud_provider="GKE",     environment="prod",     region="us-east1",    namespace="payments-prod",    k8s_version="1.28"),
    dict(app_name="payments-api", cluster_name="gke-dev-us-east",      cloud_provider="GKE",     environment="nonprod",  region="us-east1",    namespace="payments-dev",     k8s_version="1.28"),
    dict(app_name="payments-api", cluster_name="rancher-onprem-dc1",   cloud_provider="Rancher", environment="prod",     region="on-prem",     namespace="payments-prod",    k8s_version="1.26"),
    # billing-service
    dict(app_name="billing-service", cluster_name="gke-prod-us-east",  cloud_provider="GKE",     environment="prod",     region="us-east1",    namespace="billing-prod",     k8s_version="1.28"),
    dict(app_name="billing-service", cluster_name="aks-nonprod-weu",   cloud_provider="AKS",     environment="nonprod",  region="westeurope",  namespace="billing-qa",       k8s_version="1.29"),
    # auth-service
    dict(app_name="auth-service",  cluster_name="gke-prod-us-east",    cloud_provider="GKE",     environment="prod",     region="us-east1",    namespace="auth-prod",        k8s_version="1.28"),
    dict(app_name="auth-service",  cluster_name="rancher-onprem-dc1",  cloud_provider="Rancher", environment="prod",     region="on-prem",     namespace="auth-prod",        k8s_version="1.26"),
    # user-mgmt
    dict(app_name="user-mgmt",     cluster_name="eks-prod-eu-west",    cloud_provider="EKS",     environment="prod",     region="eu-west-1",   namespace="usermgmt-prod",    k8s_version="1.27"),
    dict(app_name="user-mgmt",     cluster_name="aks-nonprod-weu",     cloud_provider="AKS",     environment="nonprod",  region="westeurope",  namespace="usermgmt-dev",     k8s_version="1.29"),
    # web-frontend
    dict(app_name="web-frontend",  cluster_name="eks-prod-eu-west",    cloud_provider="EKS",     environment="prod",     region="eu-west-1",   namespace="frontend-prod",    k8s_version="1.27"),
    dict(app_name="web-frontend",  cluster_name="gke-dev-us-east",     cloud_provider="GKE",     environment="nonprod",  region="us-east1",    namespace="frontend-dev",     k8s_version="1.28"),
    dict(app_name="web-frontend",  cluster_name="aks-nonprod-weu",     cloud_provider="AKS",     environment="nonprod",  region="westeurope",  namespace="frontend-dev",     k8s_version="1.29"),
    # sandbox (your Rancher cluster)
    dict(app_name="sandbox",       cluster_name="sandbox-deux",        cloud_provider="Rancher", environment="dev",      region="intranet",   namespace="default",          k8s_version="1.28"),
]


def seed():
    create_tables()
    db = SessionLocal()

    try:
        print("Seeding demo users...")
        user_ids = {}

        for u_data in DEMO_USERS:
            existing = db.query(User).filter(User.username == u_data["username"]).first()
            if existing:
                print(f"  User '{u_data['username']}' already exists, checking app access.")
                user_ids[u_data["username"]] = existing.id

                for app_name, can_read, can_mutate in u_data["apps"]:
                    existing_ownership = db.query(AppOwnership).filter(
                        AppOwnership.user_id == existing.id,
                        AppOwnership.app_name == app_name,
                    ).first()
                    if existing_ownership:
                        continue

                    ownership = AppOwnership(
                        user_id=existing.id,
                        app_name=app_name,
                        can_read=can_read,
                        can_mutate=can_mutate,
                        granted_by="seed-script",
                    )
                    db.add(ownership)
                    print(f"    Added app access: {u_data['username']} -> {app_name}")
                continue

            user = User(
                id=u_data["id"],
                username=u_data["username"],
                email=u_data["email"],
                hashed_password=hash_password(u_data["password"]),
                full_name=u_data["full_name"],
                role=u_data["role"],
            )
            db.add(user)
            db.flush()
            user_ids[u_data["username"]] = u_data["id"]

            for app_name, can_read, can_mutate in u_data["apps"]:
                ownership = AppOwnership(
                    user_id=u_data["id"],
                    app_name=app_name,
                    can_read=can_read,
                    can_mutate=can_mutate,
                    granted_by="seed-script",
                )
                db.add(ownership)

            print(f"  Created user '{u_data['username']}' (role={u_data['role']})")

        print("\nSeeding cluster registry...")
        for entry_data in CLUSTER_ENTRIES:
            existing = db.query(ClusterRegistry).filter(
                ClusterRegistry.app_name == entry_data["app_name"],
                ClusterRegistry.cluster_name == entry_data["cluster_name"],
            ).first()
            if existing:
                continue

            entry = ClusterRegistry(**entry_data)
            db.add(entry)
            print(f"  Registered {entry_data['app_name']} -> {entry_data['cluster_name']} ({entry_data['environment']})")

        db.commit()
        print("\nSeed complete!")
        print("\nDemo credentials (password: demo1234):")
        print("  priya   — developer  — sandbox")
        print("  james   — infra-admin — all apps (wildcard)")
        print("  aisha   — developer  — auth-service, user-mgmt")
        print("  bob     — developer  — web-frontend")

    except Exception as exc:
        db.rollback()
        print(f"Seed failed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
