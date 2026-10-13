import os
import pytest
from fastapi.testclient import TestClient

# Set testing environment variables before importing app
os.environ["TESTING"] = "True"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-1234567890-test-environment"

from app import app, sm
from security import create_access_token


@pytest.fixture(scope="session")
def client():
    """Returns a FastAPI TestClient instance."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_headers():
    """Generates valid JWT Authorization headers for an administrator."""
    token = create_access_token({"sub": "admin@condo.com", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def superadmin_headers():
    """Generates valid JWT Authorization headers for a superadmin."""
    token = create_access_token({"sub": "superadmin@condo.com", "role": "superadmin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_headers():
    """Generates valid JWT Authorization headers for a resident owner."""
    token = create_access_token({"sub": "1713916854", "role": "owner", "unit": "101"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_sm():
    """Returns the SheetsManager instance."""
    return sm
