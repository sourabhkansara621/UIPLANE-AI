"""
conftest.py — shared pytest fixtures
"""
import pytest
from unittest.mock import MagicMock
from models.database import User


@pytest.fixture
def mock_developer():
    user = MagicMock(spec=User)
    user.id = "dev-001"
    user.username = "priya"
    user.role = "developer"
    user.is_active = True
    return user


@pytest.fixture
def mock_admin():
    user = MagicMock(spec=User)
    user.id = "admin-001"
    user.username = "james"
    user.role = "infra-admin"
    user.is_active = True
    return user


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    return db
