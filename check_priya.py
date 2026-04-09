#!/usr/bin/env python3
"""
Check priya user status and permissions
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models.database import SessionLocal, User, AppOwnership

def check_priya():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "priya").first()
        if not user:
            print("✗ User 'priya' NOT FOUND in database")
            return
        
        print("\n=== USER INFO ===")
        print(f"Username: {user.username}")
        print(f"Email: {user.email}")
        print(f"Full Name: {user.full_name}")
        print(f"Role: {user.role}")
        print(f"Active: {user.is_active}")
        print(f"Created: {user.created_at}")
        print(f"Last Login: {user.last_login}")
        
        print("\n=== APP ACCESS ===")
        ownerships = db.query(AppOwnership).filter(AppOwnership.user_id == user.id).all()
        if not ownerships:
            print("  ✗ No app access granted")
        else:
            for own in ownerships:
                read_str = "✓ read" if own.can_read else "✗ read"
                mutate_str = "✓ mutate" if own.can_mutate else "✗ mutate"
                print(f"  {own.app_name}: {read_str}, {mutate_str}")
        
        print("\n=== LOGIN TEST ===")
        from auth.auth_service import authenticate_user
        success = authenticate_user("priya", "demo1234", db)
        if success:
            print("✓ Password 'demo1234' is CORRECT")
        else:
            print("✗ Password 'demo1234' is WRONG - try resetting")
        
    finally:
        db.close()

if __name__ == "__main__":
    check_priya()
