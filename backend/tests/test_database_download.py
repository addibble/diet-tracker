"""Test database download endpoint."""
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)


def test_database_download_requires_auth():
    """Test that download endpoint requires authentication."""
    response = client.get("/api/database/download")
    assert response.status_code == 401


def test_database_download_with_auth():
    """Test database download with valid authentication."""
    # Check if DB exists
    db_path = Path(settings.database_url.split("///")[-1])
    if not db_path.exists():
        # Skip if no DB in test environment
        return
    
    # First login
    login_response = client.post(
        "/api/auth/login",
        json={"password": "yeiyio8aVai"}
    )
    assert login_response.status_code == 200
    
    # Try to download - TestClient maintains cookies automatically
    response = client.get("/api/database/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert len(response.content) > 0
