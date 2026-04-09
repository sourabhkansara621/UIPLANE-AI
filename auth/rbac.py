"""
auth/rbac.py
------------
Role-Based Access Control for the K8S-AI platform.

Core concept
------------
Every user belongs to one or more applications.
Before ANY Kubernetes query is executed, the RBAC layer checks:
  1. Is the user authenticated?
  2. Does the user own (or have access to) the requested application?
  3. Does the operation type (read vs mutate) match their permission?

infra-admin role bypasses app-level checks (wildcard access).

Functions
---------
get_current_user(token, db)                  -> User
check_app_access(user, app_name, db)         -> bool
require_app_access(user, app_name, db)       -> None  (raises 403)
get_user_allowed_apps(user, db)              -> List[str]
is_infra_admin(user)                         -> bool
check_mutation_permission(user, app, db)     -> bool
"""

from typing import List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from models.database import User, AppOwnership, get_db
from auth.jwt_handler import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ── Current user extraction ───────────────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency.
    Decodes JWT, loads User from DB.
    Raises 401 if token is invalid or user not found.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if user is None:
        raise credentials_exc
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that also enforces is_active."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


# ── Role helpers ──────────────────────────────────────────────────────────────

def is_infra_admin(user: User) -> bool:
    """Return True if the user has wildcard cluster access."""
    return user.role == "infra-admin"


def is_team_lead_or_above(user: User) -> bool:
    """Return True if user can grant app access to others."""
    return user.role in ("team-lead", "infra-admin")


# ── App-level permission checks ───────────────────────────────────────────────

def check_app_access(user: User, app_name: str, db: Session) -> bool:
    """
    Return True if the user may READ the given application.
    infra-admin always returns True.
    """
    if is_infra_admin(user):
        return True

    ownership = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == user.id,
            AppOwnership.app_name == app_name,
            AppOwnership.can_read == True,
        )
        .first()
    )
    return ownership is not None


def require_app_access(user: User, app_name: str, db: Session) -> None:
    """
    Raise HTTP 403 if the user does not have read access to app_name.
    Call this before every Kubernetes read operation.
    """
    if not check_app_access(user, app_name, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. You are not authorized to access '{app_name}'. "
                f"Your role: {user.role}. "
                "Contact your team lead or infra-admin to request access."
            ),
        )


def check_mutation_permission(user: User, app_name: str, db: Session) -> bool:
    """
    Return True if the user may MUTATE (write) the given application.
    infra-admin always returns True.
    """
    if is_infra_admin(user):
        return True

    ownership = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == user.id,
            AppOwnership.app_name == app_name,
            AppOwnership.can_mutate == True,
        )
        .first()
    )
    return ownership is not None


def require_mutation_permission(user: User, app_name: str, db: Session) -> None:
    """Raise HTTP 403 if the user cannot mutate app_name."""
    if not check_mutation_permission(user, app_name, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Mutation denied. You do not have write access to '{app_name}'."
            ),
        )


# ── Bulk helpers ──────────────────────────────────────────────────────────────

def get_user_allowed_apps(user: User, db: Session) -> List[str]:
    """
    Return the list of app names the user can read.
    infra-admin returns ['*'] to signal wildcard.
    """
    if is_infra_admin(user):
        return ["*"]

    ownerships = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == user.id,
            AppOwnership.can_read == True,
        )
        .all()
    )
    return [o.app_name for o in ownerships]


def get_user_mutable_apps(user: User, db: Session) -> List[str]:
    """Return app names the user can mutate."""
    if is_infra_admin(user):
        return ["*"]

    ownerships = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == user.id,
            AppOwnership.can_mutate == True,
        )
        .all()
    )
    return [o.app_name for o in ownerships]
