"""
tests/test_auth.py
------------------
Unit tests for JWT handling and RBAC logic.
Run with:  pytest tests/ -v
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import timedelta

from auth.jwt_handler import (
    hash_password, verify_password,
    create_access_token, decode_access_token,
)
from auth.rbac import check_app_access, get_user_allowed_apps, is_infra_admin
from models.database import User, AppOwnership


# ── JWT tests ─────────────────────────────────────────────────────────────────

def test_hash_and_verify_password():
    plain = "supersecret123"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("wrongpassword", hashed) is False


def test_create_and_decode_token():
    payload = {"sub": "user-123", "username": "testuser", "role": "developer"}
    token = create_access_token(payload)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "user-123"
    assert decoded["username"] == "testuser"


def test_expired_token_raises():
    from jose import JWTError
    payload = {"sub": "user-123"}
    token = create_access_token(payload, expires_delta=timedelta(seconds=-1))
    with pytest.raises(JWTError):
        decode_access_token(token)


# ── RBAC tests ────────────────────────────────────────────────────────────────

def make_user(role="developer", user_id="u1"):
    user = MagicMock(spec=User)
    user.id = user_id
    user.role = role
    user.username = "testuser"
    return user


def make_db_with_ownership(user_id, app_name, can_read=True):
    """Mock a DB session that returns an AppOwnership record."""
    ownership = MagicMock(spec=AppOwnership)
    ownership.user_id = user_id
    ownership.app_name = app_name
    ownership.can_read = can_read

    db = MagicMock()
    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = ownership
    query_mock.all.return_value = [ownership]
    return db


def make_empty_db():
    """Mock a DB session that returns no records."""
    db = MagicMock()
    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None
    query_mock.all.return_value = []
    return db


def test_infra_admin_has_access_to_any_app():
    admin = make_user(role="infra-admin")
    db = make_empty_db()
    assert check_app_access(admin, "any-app", db) is True


def test_developer_with_ownership_has_access():
    user = make_user(role="developer", user_id="u1")
    db = make_db_with_ownership("u1", "payments-api", can_read=True)
    assert check_app_access(user, "payments-api", db) is True


def test_developer_without_ownership_denied():
    user = make_user(role="developer", user_id="u2")
    db = make_empty_db()
    assert check_app_access(user, "payments-api", db) is False


def test_is_infra_admin():
    admin = make_user(role="infra-admin")
    dev = make_user(role="developer")
    assert is_infra_admin(admin) is True
    assert is_infra_admin(dev) is False


def test_get_user_allowed_apps_infra_admin():
    admin = make_user(role="infra-admin")
    db = make_empty_db()
    result = get_user_allowed_apps(admin, db)
    assert result == ["*"]


def test_get_user_allowed_apps_developer():
    user = make_user(role="developer", user_id="u3")
    ownership = MagicMock(spec=AppOwnership)
    ownership.app_name = "billing-service"

    db = MagicMock()
    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = [ownership]

    apps = get_user_allowed_apps(user, db)
    assert "billing-service" in apps


# ── Auth service tests ────────────────────────────────────────────────────────

def test_authenticate_user_wrong_password():
    from auth.auth_service import authenticate_user

    user = MagicMock(spec=User)
    user.hashed_password = hash_password("correct-password")
    user.is_active = True

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user("anyuser", "wrong-password", db)
    assert result is None


def test_authenticate_inactive_user():
    from auth.auth_service import authenticate_user

    user = MagicMock(spec=User)
    user.hashed_password = hash_password("password")
    user.is_active = False

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user("anyuser", "password", db)
    assert result is None
