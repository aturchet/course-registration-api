import pytest
from fastapi.testclient import TestClient
from main import app, catalog_db, students_db


@pytest.fixture(autouse=True)
def reset_db():
    """Clear memory stores before each test to ensure isolation."""
    main.catalog_db.clear()
    main.students_db.clear()
    yield


@pytest.fixture
def client():
    """The TestClient instance for hitting endpoints."""
    return TestClient(app)
