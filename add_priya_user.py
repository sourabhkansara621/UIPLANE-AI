#!/usr/bin/env python3
"""
Quick script to add user 'priya' with password 'demo1234' to the database.
Run this once to enable priya login.
"""

import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from models.database import SessionLocal, create_tables, User, AppOwnership
from auth.jwt_handler import hash_password

PRIYA_APPS = [
    "sandbox",
    "EKS",
    "GKE",
    "AKS",
    "on-prem Rancher",
]

def create_tables_if_needed():
    """Create tables if they don't exist."""
    try:
        create_tables()
        print("✓ Database tables created/verified")
    except Exception as e:
        print(f"✗ Error creating tables: {e}")

def add_priya():
    """Add user 'priya' to database."""
    create_tables_if_needed()
    
    db = SessionLocal()
    try:
        # Check if priya already exists
        existing = db.query(User).filter(User.username == "priya").first()
        if existing:
            print(f"✓ User 'priya' already exists (ID: {existing.id})")
            # Make sure is_active is True
            if not existing.is_active:
                existing.is_active = True
                db.commit()
                print("  ✓ Activated user 'priya'")

            # Ensure required app access exists.
            for app_name in PRIYA_APPS:
                existing_ownership = db.query(AppOwnership).filter(
                    AppOwnership.user_id == existing.id,
                    AppOwnership.app_name == app_name,
                ).first()
                if existing_ownership:
                    continue
                ownership = AppOwnership(
                    user_id=existing.id,
                    app_name=app_name,
                    can_read=True,
                    can_mutate=False,
                    granted_by="script",
                )
                db.add(ownership)
                print(f"  ✓ Granted app access: {app_name} (read-only)")

            db.commit()
            return existing.id
        
        # Create new user
        user_id = str(uuid.uuid4())
        user = User(
            id=user_id,
            username="priya",
            email="priya@company.com",
            hashed_password=hash_password("demo1234"),
            full_name="Priya S.",
            role="developer",
            is_active=True,
        )
        db.add(user)
        db.flush()
        print(f"✓ Created user 'priya' (ID: {user_id})")
        
        # Grant default app access list.
        for app_name in PRIYA_APPS:
            ownership = AppOwnership(
                user_id=user_id,
                app_name=app_name,
                can_read=True,
                can_mutate=False,
                granted_by="script",
            )
            db.add(ownership)
        db.commit()
        print("✓ Granted app access (read-only):")
        for app_name in PRIYA_APPS:
            print(f"  - {app_name}")
        print("\n--- LOGIN INFO ---")
        print("Username: priya")
        print("Password: demo1234")
        print(f"Apps: {', '.join(PRIYA_APPS)}")
        
        return user_id
        
    except Exception as e:
        db.rollback()
        print(f"✗ Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    add_priya()
