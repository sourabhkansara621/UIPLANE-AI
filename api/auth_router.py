"""
api/auth_router.py
------------------
FastAPI router for all authentication endpoints.

Routes
------
POST /api/auth/login        Login and receive JWT
POST /api/auth/register     Create a new user
GET  /api/auth/me           Get current user profile
POST /api/auth/logout       Invalidate session (Redis blacklist)
POST /api/auth/grant-access Grant app access to a user (team-lead+)
GET  /api/auth/users        List all users (infra-admin only)
"""

import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from models.database import get_db, User, AppOwnership
from models.schemas import (
    LoginRequest, TokenResponse, UserOut, UserCreate,
    AppOwnershipCreate, AppOwnershipOut,
)
from auth.auth_service import login_user, create_user
from auth.rbac import (
    get_current_active_user, get_user_allowed_apps,
    is_infra_admin, is_team_lead_or_above,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate with username + password.
    Returns a JWT access token valid for the configured expiry period.
    """
    return login_user(request, db)


@router.post("/login/form", response_model=TokenResponse, include_in_schema=False)
def login_form(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 form-based login (for Swagger UI 'Authorize' button)."""
    return login_user(LoginRequest(username=form.username, password=form.password), db)


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new platform user.
    Only infra-admin can register new users.
    """
    if not is_infra_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only infra-admin can create users.",
        )
    user = create_user(data, db)
    allowed = get_user_allowed_apps(user, db)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        allowed_apps=allowed,
    )


# ── Current user ──────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return the currently authenticated user's profile and allowed apps."""
    allowed = get_user_allowed_apps(current_user, db)
    return UserOut(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        allowed_apps=allowed,
    )


# ── Grant app access ──────────────────────────────────────────────────────────

@router.post(
    "/grant-access",
    response_model=AppOwnershipOut,
    status_code=status.HTTP_201_CREATED,
)
def grant_app_access(
    data: AppOwnershipCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Grant a user read (and optionally mutate) access to an application.
    Requires team-lead or infra-admin role.
    """
    if not is_team_lead_or_above(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only team-lead or infra-admin can grant access.",
        )

    # Check target user exists
    target = db.query(User).filter(User.id == data.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found.")

    # Upsert ownership record
    existing = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == data.user_id,
            AppOwnership.app_name == data.app_name,
        )
        .first()
    )
    if existing:
        existing.can_read = data.can_read
        existing.can_mutate = data.can_mutate
        existing.granted_by = current_user.username
        db.commit()
        db.refresh(existing)
        return existing

    ownership = AppOwnership(
        user_id=data.user_id,
        app_name=data.app_name,
        can_read=data.can_read,
        can_mutate=data.can_mutate,
        granted_by=current_user.username,
    )
    db.add(ownership)
    db.commit()
    db.refresh(ownership)
    return ownership


# ── Revoke app access ─────────────────────────────────────────────────────────

@router.delete("/revoke-access/{user_id}/{app_name}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_app_access(
    user_id: str,
    app_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Remove a user's access to an application."""
    if not is_team_lead_or_above(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    ownership = (
        db.query(AppOwnership)
        .filter(AppOwnership.user_id == user_id, AppOwnership.app_name == app_name)
        .first()
    )
    if not ownership:
        raise HTTPException(status_code=404, detail="Ownership record not found.")

    db.delete(ownership)
    db.commit()


# ── List users (admin) ────────────────────────────────────────────────────────

@router.get("/users", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List all platform users. Requires infra-admin role."""
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    users = db.query(User).filter(User.is_active == True).all()
    result = []
    for u in users:
        allowed = get_user_allowed_apps(u, db)
        result.append(
            UserOut(
                id=u.id,
                username=u.username,
                email=u.email,
                full_name=u.full_name,
                role=u.role,
                is_active=u.is_active,
                allowed_apps=allowed,
            )
        )
    return result
