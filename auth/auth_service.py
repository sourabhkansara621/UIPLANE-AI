"""
auth/auth_service.py
--------------------
Business logic for authentication flows.

Functions
---------
authenticate_user(username, password, db)    -> Optional[User]
login_user(request, db)                      -> TokenResponse
create_user(data, db)                        -> User
update_last_login(user, db)                  -> None
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from models.database import User
from models.schemas import LoginRequest, TokenResponse, UserCreate, UserOut
from auth.jwt_handler import (
    hash_password, verify_password,
    create_access_token, get_token_expiry_seconds,
)
from auth.rbac import get_user_allowed_apps


# ── Core auth functions ───────────────────────────────────────────────────────

def authenticate_user(
    username: str,
    password: str,
    db: Session,
) -> Optional[User]:
    """
    Authenticate a user with username and password.
    
    This is the core authentication function used by the login flow.
    Performs three critical checks:
    1. User exists in database
    2. Password matches the stored hash
    3. User account is active
    
    Args:
        username: The username to authenticate
        password: Plain-text password to verify
        db: Database session
        
    Returns:
        User object if authentication succeeds, None otherwise
        
    Security Notes:
        - Always returns None for failed authentication (no details leaked)
        - Uses constant-time password comparison via bcrypt
        - Inactive accounts are treated as authentication failures
    """
    # Look up user by username
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    
    # Verify password hash
    if not verify_password(password, user.hashed_password):
        return None
    
    # Ensure account is active
    if not user.is_active:
        return None
    
    return user


def login_user(request: LoginRequest, db: Session) -> TokenResponse:
    """
    Complete login flow: authenticate user and generate JWT token.
    
    This is the main entry point for user authentication. It:
    1. Authenticates the username/password
    2. Loads user's app permissions
    3. Creates a JWT token with user info and permissions
    4. Updates last_login timestamp
    5. Returns token + user profile
    
    Args:
        request: LoginRequest containing username and password
        db: Database session
        
    Returns:
        TokenResponse containing:
        - access_token: JWT bearer token
        - token_type: "bearer"
        - expires_in: Seconds until token expires
        - user: Complete user profile with allowed apps
        
    Raises:
        HTTPException 401: If credentials are invalid (wrong username/password or inactive account)
        
    Token Payload:
        The JWT contains:
        - sub: User ID (primary key)
        - username: Username string
        - role: User role (developer, team-lead, infra-admin)
        - allowed_apps: List of apps user can access (or ["*"] for infra-admin)
        - exp: Expiration timestamp
        - iat: Issued-at timestamp
    """
    # Authenticate user credentials
    user = authenticate_user(request.username, request.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Build JWT payload with user info and permissions
    allowed_apps = get_user_allowed_apps(user, db)
    token_data = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "allowed_apps": allowed_apps,
    }
    token = create_access_token(token_data)
    
    # Update last login timestamp
    update_last_login(user, db)

    # Build user profile response
    user_out = UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        allowed_apps=allowed_apps,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=get_token_expiry_seconds(),
        user=user_out,
    )


def create_user(data: UserCreate, db: Session) -> User:
    """
    Create a new platform user account.
    
    Only infra-admin users can call this function (enforced at the router level).
    
    Args:
        data: UserCreate schema with username, email, password, full_name, role
        db: Database session
        
    Returns:
        Newly created User object
        
    Raises:
        HTTPException 400: If username or email already exists in the database
        
    Process:
        1. Check username and email uniqueness
        2. Generate UUID for user ID
        3. Hash the password with bcrypt
        4. Insert user record into database
        5. Commit transaction and return user
        
    Note:
        New users start with no app permissions. An infra-admin or team-lead
        must grant access using the /api/auth/grant-access endpoint.
    """
    # Check uniqueness of username and email
    existing = (
        db.query(User)
        .filter(
            (User.username == data.username) | (User.email == data.email)
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered",
        )

    # Create new user record
    user = User(
        id=str(uuid.uuid4()),
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),  # Secure bcrypt hash
        full_name=data.full_name,
        role=data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_last_login(user: User, db: Session) -> None:
    """
    Update the user's last_login timestamp to current UTC time.
    
    Called automatically on successful login to track user activity.
    
    Args:
        user: User object to update
        db: Database session
        
    Returns:
        None
        
    Note:
        This modifies the user object in-place and commits the transaction.
    """
    user.last_login = datetime.utcnow()
    db.commit()


def get_user_by_id(user_id: str, db: Session) -> Optional[User]:
    """
    Fetch a single user by their UUID.
    
    Args:
        user_id: User's UUID (primary key)
        db: Database session
        
    Returns:
        User object if found, None otherwise
        
    Usage:
        Used internally for user management and permission granting.
    """
    return db.query(User).filter(User.id == user_id).first()


def deactivate_user(user_id: str, db: Session) -> bool:
    """
    Soft-delete a user by setting is_active to False.
    
    Deactivated users:
    - Cannot log in (authentication fails)
    - Existing tokens remain valid until expiry (check is_active in get_current_user)
    - User data is preserved (not deleted from database)
    
    Args:
        user_id: UUID of user to deactivate
        db: Database session
        
    Returns:
        True if user was found and deactivated, False if user not found
        
    Note:
        To fully revoke access immediately, also add their tokens to the blacklist.
        This function only prevents future logins.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.is_active = False
    db.commit()
    return True
