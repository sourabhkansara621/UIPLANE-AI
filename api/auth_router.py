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
    is_infra_admin, is_team_lead_or_above, oauth2_scheme,
)
from auth.jwt_handler import blacklist_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate with username and password to receive a JWT access token.
    
    This endpoint accepts JSON with username/password and returns a JWT token
    valid for the configured expiry period (default: 60 minutes).
    
    Args:
        request: LoginRequest with username and password
        db: Database session (auto-injected)
        
    Returns:
        TokenResponse containing:
        - access_token: JWT bearer token for subsequent API calls
        - token_type: Always "bearer"
        - expires_in: Seconds until token expires
        - user: User profile with role and allowed applications
        
    Raises:
        HTTPException 401: If credentials are invalid
        
    Example Request:
        POST /api/auth/login
        {
            "username": "john.doe",
            "password": "SecurePass123"
        }
        
    Example Response:
        {
            "access_token": "eyJhbGciOiJIUzI1NiIs...",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "username": "john.doe",
                "role": "developer",
                "allowed_apps": ["payments-api", "auth-service"]
            }
        }
    """
    return login_user(request, db)


@router.post("/login/form", response_model=TokenResponse, include_in_schema=False)
def login_form(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    OAuth2 form-based login endpoint for Swagger UI compatibility.
    
    This endpoint accepts form data (application/x-www-form-urlencoded)
    instead of JSON, allowing the Swagger UI "Authorize" button to work.
    
    Args:
        form: OAuth2 form data with username and password fields
        db: Database session
        
    Returns:
        Same TokenResponse as the /login endpoint
        
    Note:
        Hidden from API docs (include_in_schema=False) since it's just
        a Swagger UI convenience - clients should use POST /login instead.
    """
    return login_user(LoginRequest(username=form.username, password=form.password), db)


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new platform user account.
    
    This endpoint is restricted to infra-admin users only.
    Regular users and team-leads cannot create new accounts.
    
    Args:
        data: UserCreate schema with:
            - username: Unique username (required)
            - email: Unique email address (required)
            - password: Plain-text password (will be hashed)
            - full_name: Display name
            - role: One of 'developer', 'team-lead', 'infra-admin'
        db: Database session
        current_user: Must be infra-admin (enforced)
        
    Returns:
        UserOut with created user profile (password not included)
        
    Raises:
        HTTPException 403: If current user is not infra-admin
        HTTPException 400: If username or email already exists
        
    Note:
        New users start with zero app permissions. Use the
        /api/auth/grant-access endpoint to assign applications.
    """
    # Enforce infra-admin only
    if not is_infra_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only infra-admin can create users.",
        )
    
    # Create the user
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
    """
    Get the currently authenticated user's profile and permissions.
    
    This endpoint returns detailed information about the logged-in user,
    including their role and list of accessible applications.
    
    Args:
        db: Database session
        current_user: Authenticated user from JWT token
        
    Returns:
        UserOut containing:
        - id: User UUID
        - username: Username
        - email: Email address
        - full_name: Display name
        - role: User role (developer/team-lead/infra-admin)
        - is_active: Account status
        - allowed_apps: List of apps user can access (or ["*"] for infra-admin)
        
    Usage:
        Frontend applications should call this on initial load to:
        1. Verify the JWT token is still valid
        2. Get the user's display name for the UI
        3. Load the list of accessible applications
    """
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


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_active_user)
):
    """
    Logout endpoint that invalidates the current JWT token.
    
    This adds the token to a blacklist, preventing it from being used
    for future requests even though it hasn't expired yet.
    
    Args:
        token: JWT token to blacklist (from Authorization header)
        current_user: Authenticated user
        
    Returns:
        Success message with username
        
    Process:
        1. Token is added to in-memory blacklist
        2. Future requests with this token will receive 401 Unauthorized
        3. Token remains blacklisted until its natural expiration
        
    Production Note:
        The in-memory blacklist should be replaced with Redis for
        multi-instance deployments. Without Redis, logging out from
        one server won't invalidate the token on other servers.
        
    Example Response:
        {
            "message": "Logged out successfully",
            "username": "john.doe"
        }
    """
    # Add token to blacklist
    blacklist_token(token)
    return {"message": "Logged out successfully", "username": current_user.username}


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
    Grant a user read and/or write access to an application.
    
    This endpoint allows team-leads and infra-admins to assign application
    permissions to other users. Creates or updates an AppOwnership record.
    
    Args:
        data: AppOwnershipCreate with:
            - user_id: UUID of user to grant access to
            - app_name: Application name (e.g., 'payments-api')
            - can_read: Grant read permission (default: true)
            - can_mutate: Grant write permission (default: false)
        db: Database session
        current_user: Must be team-lead or infra-admin
        
    Returns:
        AppOwnershipOut with the created/updated permission record
        
    Raises:
        HTTPException 403: If current user is not team-lead or infra-admin
        HTTPException 404: If target user does not exist
        
    Behavior:
        - If permission record exists: Updates it with new values
        - If permission record doesn't exist: Creates new record
        - Records who granted the permission in granted_by field
        
    Example Request:
        POST /api/auth/grant-access
        {
            "user_id": "123e4567-e89b-12d3-a456-426614174000",
            "app_name": "payments-api",
            "can_read": true,
            "can_mutate": true
        }
    """
    # Enforce team-lead or infra-admin role
    if not is_team_lead_or_above(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only team-lead or infra-admin can grant access.",
        )

    # Verify target user exists
    target = db.query(User).filter(User.id == data.user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found.")

    # Upsert ownership record (update if exists, create if not)
    existing = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == data.user_id,
            AppOwnership.app_name == data.app_name,
        )
        .first()
    )
    if existing:
        # Update existing permission
        existing.can_read = data.can_read
        existing.can_mutate = data.can_mutate
        existing.granted_by = current_user.username
        db.commit()
        db.refresh(existing)
        return existing

    # Create new permission record
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
    """
    Remove a user's access to an application entirely.
    
    This deletes the AppOwnership record, revoking both read and write permissions.
    
    Args:
        user_id: UUID of user to revoke access from
        app_name: Application name
        db: Database session
        current_user: Must be team-lead or infra-admin
        
    Returns:
        HTTP 204 No Content on success
        
    Raises:
        HTTPException 403: If current user lacks permission
        HTTPException 404: If ownership record not found
        
    Note:
        This takes effect immediately. Any active JWT tokens still contain
        the old permissions in their payload, but the database check in
        require_app_access() will block the user.
    """
    # Enforce team-lead or infra-admin role
    if not is_team_lead_or_above(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    # Find and delete the ownership record
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
    """
    List all active platform users with their permissions.
    
    This admin-only endpoint returns all users and their application access.
    Useful for user management interfaces.
    
    Args:
        db: Database session
        current_user: Must be infra-admin
        
    Returns:
        List of UserOut objects, each containing:
        - id, username, email, full_name, role, is_active
        - allowed_apps: List of applications the user can access
        
    Raises:
        HTTPException 403: If current user is not infra-admin
        
    Note:
        Only returns active users (is_active=True).
        Deactivated users are hidden from this list.
    """
    # Enforce infra-admin only
    if not is_infra_admin(current_user):
        raise HTTPException(status_code=403, detail="infra-admin only.")

    # Get all active users
    users = db.query(User).filter(User.is_active == True).all()
    result = []
    
    # Build response with permissions for each user
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
