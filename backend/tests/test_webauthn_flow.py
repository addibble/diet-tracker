"""End-to-end WebAuthn (passkey) flow test using soft-webauthn."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from soft_webauthn import SoftWebauthnDevice
from sqlmodel import Session

import app.db_engines as db_engines
from app.auth_models import Invite
from app.config import settings
from app.main import app


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _json_options_to_soft(opts: dict) -> dict:
    """Convert py_webauthn JSON options (base64url strings) to soft-webauthn's
    raw-bytes format."""

    pk = dict(opts["publicKey"])
    pk["challenge"] = _b64url_decode(pk["challenge"])
    if "user" in pk:
        u = dict(pk["user"])
        u["id"] = _b64url_decode(u["id"])
        pk["user"] = u
    if pk.get("excludeCredentials"):
        pk["excludeCredentials"] = [
            {**c, "id": _b64url_decode(c["id"])} for c in pk["excludeCredentials"]
        ]
    if pk.get("allowCredentials"):
        pk["allowCredentials"] = [
            {**c, "id": _b64url_decode(c["id"])} for c in pk["allowCredentials"]
        ]
    return {"publicKey": pk}


def _soft_cred_to_api(cred: dict) -> dict:
    """Convert soft-webauthn's raw-bytes credential into the base64url JSON
    shape our register/login endpoints consume (matches @simplewebauthn/browser)."""

    out = {"type": cred["type"]}
    # id/rawId — soft-webauthn returns bytes for rawId and b64-bytes for id.
    raw_id = cred["rawId"]
    out["rawId"] = _b64url_encode(raw_id)
    out["id"] = _b64url_encode(raw_id)
    resp = {}
    for k, v in cred["response"].items():
        if isinstance(v, bytes):
            resp[k] = _b64url_encode(v)
        elif v is None:
            resp[k] = None
        else:
            resp[k] = v
    out["response"] = resp
    out["clientExtensionResults"] = {}
    return out


@pytest.fixture
def isolated_deployment(monkeypatch, tmp_path):
    """Point the auth DB and user data at tmp_path and clear overrides."""

    monkeypatch.setattr(db_engines, "_data_root", lambda: tmp_path.resolve())
    monkeypatch.setattr(db_engines, "_auth_engine", None, raising=False)
    db_engines._user_engines.clear()
    db_engines._user_migrate_locks.clear()
    monkeypatch.setattr(settings, "webauthn_rp_id", "localhost")
    monkeypatch.setattr(settings, "webauthn_rp_name", "TestRP")
    monkeypatch.setattr(settings, "webauthn_origin", "https://localhost")
    # Ensure no test has installed overrides that bypass auth.
    app.dependency_overrides.clear()
    yield tmp_path
    app.dependency_overrides.clear()
    for user_id in list(db_engines._user_engines.keys()):
        db_engines.dispose_user_engine(user_id)
    if db_engines._auth_engine is not None:
        db_engines._auth_engine.dispose()
        db_engines._auth_engine = None


def _seed_invite(email: str) -> str:
    token = secrets.token_urlsafe(32)
    with Session(db_engines.auth_engine()) as db:
        db.add(
            Invite(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                email_hint=email,
                created_by=None,
                expires_at=datetime.now(UTC) + timedelta(days=1),
                is_bootstrap=False,
            )
        )
        db.commit()
    return token


def test_register_then_login_with_passkey(isolated_deployment):
    client = TestClient(app)
    origin = settings.webauthn_origin
    email = "alice@example.com"

    invite_token = _seed_invite(email)

    # ── Register options
    r = client.post(
        "/api/auth/register/options",
        json={
            "invite_token": invite_token,
            "email": email,
            "display_name": "Alice",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    challenge_id = body["challenge_id"]

    device = SoftWebauthnDevice()
    raw_cred = device.create(_json_options_to_soft(body), origin)

    r = client.post(
        "/api/auth/register/verify",
        json={
            "challenge_id": challenge_id,
            "credential": _soft_cred_to_api(raw_cred),
        },
    )
    assert r.status_code == 200, r.text
    assert "session" in client.cookies

    # ── /me works now
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    me = r.json()
    assert me["user"]["email"] == email
    assert len(me["passkeys"]) == 1

    # ── Clear cookies and log back in.
    client.cookies.clear()
    r = client.get("/api/auth/me")
    assert r.status_code == 401

    r = client.post("/api/auth/login/options", json={"email": email})
    assert r.status_code == 200, r.text
    body = r.json()
    challenge_id = body["challenge_id"]

    raw_cred = device.get(_json_options_to_soft(body), origin)
    r = client.post(
        "/api/auth/login/verify",
        json={
            "challenge_id": challenge_id,
            "credential": _soft_cred_to_api(raw_cred),
        },
    )
    assert r.status_code == 200, r.text
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["email"] == email


def test_login_rejects_unknown_email(isolated_deployment):
    client = TestClient(app)
    r = client.post("/api/auth/login/options", json={"email": "nobody@example.com"})
    # Still 200 (avoid enumeration), but verifying will fail without credential.
    assert r.status_code == 200


def test_invite_is_single_use(isolated_deployment):
    client = TestClient(app)
    email = "bob@example.com"
    invite_token = _seed_invite(email)

    origin = settings.webauthn_origin
    r = client.post(
        "/api/auth/register/options",
        json={"invite_token": invite_token, "email": email, "display_name": "Bob"},
    )
    assert r.status_code == 200
    body = r.json()
    device = SoftWebauthnDevice()
    raw_cred = device.create(_json_options_to_soft(body), origin)
    r = client.post(
        "/api/auth/register/verify",
        json={"challenge_id": body["challenge_id"], "credential": _soft_cred_to_api(raw_cred)},
    )
    assert r.status_code == 200

    # Reusing the same invite token should now fail.
    client.cookies.clear()
    r = client.post(
        "/api/auth/register/options",
        json={"invite_token": invite_token, "email": "eve@example.com", "display_name": "Eve"},
    )
    assert r.status_code == 400
