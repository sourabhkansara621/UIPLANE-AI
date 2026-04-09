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
    Look up user by username and verify password.

    Returns
    -------
    User object if credentials are valid, None otherwise.
    """
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


def login_user(request: LoginRequest, db: Session) -> TokenResponse:
    """
    Full login flow: authenticate → create token → return response.

    Raises
    ------
    HTTP 401 if credentials are invalid.
    """
    user = authenticate_user(request.username, request.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Build JWT payload
    allowed_apps = get_user_allowed_apps(user, db)
    token_data = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "allowed_apps": allowed_apps,
    }
    token = create_access_token(token_data)
    update_last_login(user, db)

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
    Create a new platform user.

    Raises
    ------
    HTTP 400 if username or email already exists.
    """
    # Check uniqueness
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

    user = User(
        id=str(uuid.uuid4()),
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        role=data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_last_login(user: User, db: Session) -> None:
    """Stamp the last_login timestamp on successful login."""
    user.last_login = datetime.utcnow()
    db.commit()


def get_user_by_id(user_id: str, db: Session) -> Optional[User]:
    """Fetch a single user by their UUID."""
    return db.query(User).filter(User.id == user_id).first()


def deactivate_user(user_id: str, db: Session) -> bool:
    """Soft-delete a user. Returns True if found and deactivated."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.is_active = False
    db.commit()
    return True
