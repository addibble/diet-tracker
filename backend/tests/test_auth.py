"""Basic checks for the new passkey-based auth.

Full WebAuthn flow tests live in test_webauthn.py (to be added).
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_unauthenticated():
    client = TestClient(app)
    app.dependency_overrides.clear()
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_protected_endpoint_without_auth():
    client = TestClient(app)
    app.dependency_overrides.clear()
    resp = client.get("/api/foods")
    assert resp.status_code == 401


def test_me_endpoint_without_auth():
    client = TestClient(app)
    app.dependency_overrides.clear()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401

