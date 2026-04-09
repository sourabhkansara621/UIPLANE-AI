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
from auth.jwt_handler import decode_access_token, is_token_blacklisted

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ── Current user extraction ───────────────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency to extract and validate the current authenticated user.
    
    This is the primary authentication gate for all protected endpoints.
    It performs the following checks:
    1. Verifies the token is not blacklisted (logged out)
    2. Decodes and validates the JWT token
    3. Extracts the user ID from the token payload
    4. Loads the user from the database
    5. Verifies the user is still active
    
    Args:
        token: JWT bearer token from the Authorization header
        db: Database session (auto-injected by FastAPI)
        
    Returns:
        User object from the database
        
    Raises:
        HTTPException 401: If token is invalid, blacklisted, expired, or user not found/inactive
        
    Usage:
        @router.get("/protected")
        def protected_endpoint(current_user: User = Depends(get_current_user)):
            return {"user": current_user.username}
    """
    # Prepare standard 401 exception for various failure cases
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Check if token is blacklisted (user has logged out)
    if is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        # Decode JWT and extract user_id from 'sub' claim
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        # Token is malformed, expired, or has invalid signature
        raise credentials_exc

    # Load user from database and verify they're active
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if user is None:
        raise credentials_exc
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    FastAPI dependency that enforces the user is active.
    
    This is a convenience wrapper around get_current_user that adds
    an additional is_active check (though get_current_user already filters for active users).
    
    Args:
        current_user: User object from get_current_user dependency
        
    Returns:
        The same User object if active
        
    Raises:
        HTTPException 400: If user account is inactive
        
    Note:
        This is somewhat redundant since get_current_user already filters
        for is_active=True, but kept for semantic clarity.
    """
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


# ── Role helpers ──────────────────────────────────────────────────────────────

def is_infra_admin(user: User) -> bool:
    """
    Check if user has infrastructure admin privileges (wildcard cluster access).
    
    Args:
        user: User object to check
        
    Returns:
        True if user has 'infra-admin' role, False otherwise
        
    Note:
        infra-admin users bypass all app-level access checks and can:
        - Access all applications across all clusters
        - Create and delete users
        - Grant/revoke app access to other users
        - Perform any read or write operation
    """
    return user.role == "infra-admin"


def is_team_lead_or_above(user: User) -> bool:
    """
    Check if user can grant app access to other users.
    
    Args:
        user: User object to check
        
    Returns:
        True if user is 'team-lead' or 'infra-admin', False otherwise
        
    Note:
        team-lead users can:
        - Grant read/write access to apps they own
        - Revoke access from other users
        But cannot create new users (that requires infra-admin)
    """
    return user.role in ("team-lead", "infra-admin")


# ── App-level permission checks ───────────────────────────────────────────────

def check_app_access(user: User, app_name: str, db: Session) -> bool:
    """
    Check if a user has READ permission for an application.
    
    This is the core authorization check before any Kubernetes read operation.
    
    Args:
        user: User object attempting access
        app_name: Application name (e.g., 'payments-api', 'auth-service')
        db: Database session for querying AppOwnership
        
    Returns:
        True if user can read the app, False otherwise
        
    Logic:
        - infra-admin: Always returns True (wildcard access)
        - Regular users: Returns True only if AppOwnership record exists
          with can_read=True for this user + app combination
          
    Note:
        This function does NOT raise exceptions - use require_app_access()
        for enforcement in request handlers.
    """
    # infra-admin bypasses all app-level checks
    if is_infra_admin(user):
        return True

    # Check if ownership record exists with read permission
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
    Enforce that a user has READ permission for an application.
    
    Call this at the start of every Kubernetes read operation endpoint.
    
    Args:
        user: User object attempting access
        app_name: Application name to check
        db: Database session
        
    Returns:
        None (silent success if user has access)
        
    Raises:
        HTTPException 403: If user does not have read access, with a
                          helpful message explaining how to request access
                          
    Usage:
        @router.get("/api/k8s/{app_name}/pods")
        def get_pods(app_name: str, user: User = Depends(...), db: Session = Depends(...)):
            require_app_access(user, app_name, db)  # Enforces permission
            # ... proceed with Kubernetes operation
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
    Check if a user has WRITE (mutate) permission for an application.
    
    This is the authorization check before any Kubernetes write operation
    (scaling deployments, updating images, modifying configs, etc.).
    
    Args:
        user: User object attempting the mutation
        app_name: Application name
        db: Database session
        
    Returns:
        True if user can mutate the app, False otherwise
        
    Logic:
        - infra-admin: Always returns True
        - Regular users: Returns True only if AppOwnership record exists
          with can_mutate=True
          
    Note:
        Write permission implies read permission, but they are checked separately.
        Use require_mutation_permission() to enforce this in request handlers.
    """
    # infra-admin bypasses all checks
    if is_infra_admin(user):
        return True

    # Check if ownership record exists with mutate permission
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
    """
    Enforce that a user has WRITE permission for an application.
    
    Call this before any Kubernetes mutation operation.
    
    Args:
        user: User object attempting the mutation
        app_name: Application name
        db: Database session
        
    Returns:
        None (silent success if user has write access)
        
    Raises:
        HTTPException 403: If user does not have write/mutate permission
        
    Usage:
        Before scaling a deployment, updating an image, or modifying configs:
        require_mutation_permission(current_user, app_name, db)
    """
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
    Get list of all applications a user has READ access to.
    
    This is used for:
    - Displaying available apps in UI
    - Filtering query suggestions
    - Including in JWT token payload
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        List of app names user can read.
        For infra-admin, returns ['*'] to signal wildcard access.
        
    Note:
        The special value ['*'] for infra-admin indicates "all applications"
        and is used by the UI to show an unrestricted view.
    """
    # infra-admin gets wildcard access indicator
    if is_infra_admin(user):
        return ["*"]

    # Query all apps user has read permission for
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
    """
    Get list of all applications a user has WRITE access to.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        List of app names user can mutate.
        For infra-admin, returns ['*'] to signal wildcard access.
        
    Usage:
        Used to determine which apps show "Edit" or "Deploy" buttons in UI.
    """
    # infra-admin gets wildcard write access
    if is_infra_admin(user):
        return ["*"]

    # Query all apps user has mutate permission for
    ownerships = (
        db.query(AppOwnership)
        .filter(
            AppOwnership.user_id == user.id,
            AppOwnership.can_mutate == True,
        )
        .all()
    )
    return [o.app_name for o in ownerships]
