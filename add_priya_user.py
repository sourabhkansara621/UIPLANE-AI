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
        
        # Grant access to 'sandbox' app
        ownership = AppOwnership(
            user_id=user_id,
            app_name="sandbox",
            can_read=True,
            can_mutate=False,  # Set to False initially
            granted_by="script",
        )
        db.add(ownership)
        db.commit()
        print(f"✓ Granted app access: sandbox (read-only)")
        print("\n--- LOGIN INFO ---")
        print("Username: priya")
        print("Password: demo1234")
        print("App: sandbox")
        
        return user_id
        
    except Exception as e:
        db.rollback()
        print(f"✗ Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    add_priya()
