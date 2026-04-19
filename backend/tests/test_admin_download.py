"""Admin download endpoint tests (passkey + HTTP Basic auth paths)."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.db_engines as db_engines
from app.auth_models import User
from app.config import settings
from app.main import app
from app.models import Food


@pytest.fixture
def tmp_data_root(monkeypatch, tmp_path):
    """Isolate both auth.db and per-user DBs in a fresh tmp dir."""
    monkeypatch.setattr(db_engines, "_data_root", lambda: tmp_path.resolve())
    monkeypatch.setattr(db_engines, "_auth_engine", None, raising=False)
    db_engines._user_engines.clear()
    db_engines._user_migrate_locks.clear()
    yield tmp_path
    for user_id in list(db_engines._user_engines.keys()):
        db_engines.dispose_user_engine(user_id)
    if db_engines._auth_engine is not None:
        db_engines._auth_engine.dispose()
        db_engines._auth_engine = None


def _seed_user(user_id: str, *, is_admin: bool = False) -> User:
    """Create an auth User row + a per-user DB with a Food marker."""
    with Session(db_engines.auth_engine()) as s:
        u = User(
            id=user_id,
            email=f"{user_id}@example.test",
            display_name=user_id,
            is_admin=is_admin,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
    # Touch the user engine so diet_tracker.db exists.
    engine = db_engines.user_engine(user_id)
    with Session(engine) as dbs:
        dbs.add(Food(
            name=f"{user_id}-marker",
            calories_per_serving=1, fat_per_serving=0,
            carbs_per_serving=0, protein_per_serving=0,
            serving_size_grams=100,
        ))
        dbs.commit()
    return u


def _basic_auth_header(user: str, password: str) -> dict[str, str]:
    raw = f"{user}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


@pytest.fixture
def client(tmp_data_root):
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Auth coverage ────────────────────────────────────────────────


def test_download_requires_auth(client, tmp_data_root):
    _seed_user("alice")
    r = client.get("/api/admin/users/alice/download-db")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("www-authenticate", "")


def test_download_rejects_bad_basic(client, tmp_data_root, monkeypatch):
    _seed_user("alice")
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "correct")
    r = client.get(
        "/api/admin/users/alice/download-db",
        headers=_basic_auth_header("ops", "wrong"),
    )
    assert r.status_code == 401


def test_download_basic_disabled_without_env(client, tmp_data_root, monkeypatch):
    _seed_user("alice")
    # Explicitly unset both so the Basic branch short-circuits.
    monkeypatch.setattr(settings, "logs_user", "")
    monkeypatch.setattr(settings, "logs_password", "")
    r = client.get(
        "/api/admin/users/alice/download-db",
        headers=_basic_auth_header("anyone", "anything"),
    )
    assert r.status_code == 401


def test_download_accepts_valid_basic(client, tmp_data_root, monkeypatch):
    _seed_user("alice")
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "secret")
    r = client.get(
        "/api/admin/users/alice/download-db",
        headers=_basic_auth_header("ops", "secret"),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.sqlite3")
    # SQLite files start with this magic string.
    assert r.content.startswith(b"SQLite format 3")


# ── Single-user download ────────────────────────────────────────


def test_download_unknown_user_404(client, tmp_data_root, monkeypatch):
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "secret")
    r = client.get(
        "/api/admin/users/no-such-user/download-db",
        headers=_basic_auth_header("ops", "secret"),
    )
    assert r.status_code == 404


def test_download_filename_includes_user_id(client, tmp_data_root, monkeypatch):
    _seed_user("alice")
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "secret")
    r = client.get(
        "/api/admin/users/alice/download-db",
        headers=_basic_auth_header("ops", "secret"),
    )
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "alice" in disposition
    assert ".db" in disposition


# ── Bulk download ───────────────────────────────────────────────


def test_download_all_yields_zip_with_manifest(
    client, tmp_data_root, monkeypatch,
):
    _seed_user("alice")
    _seed_user("bob")
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "secret")

    r = client.get(
        "/api/admin/users/download-all",
        headers=_basic_auth_header("ops", "secret"),
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    buf = io.BytesIO(r.content)
    with zipfile.ZipFile(buf) as z:
        names = z.namelist()
        assert "manifest.json" in names
        # Each user gets their own folder with a .db.
        assert any(n.startswith("alice/") and n.endswith(".db") for n in names)
        assert any(n.startswith("bob/") and n.endswith(".db") for n in names)

        import json
        manifest = json.loads(z.read("manifest.json"))
        ids = {row["user_id"] for row in manifest}
        assert ids == {"alice", "bob"}
        for row in manifest:
            assert row["included"] is True
            assert row["size_bytes"] > 0


def test_download_all_handles_missing_user_db(
    client, tmp_data_root, monkeypatch,
):
    """A user row can exist in auth.db without ever having touched their per-user
    DB (e.g. invite consumed but no first request yet). The manifest should
    record them as excluded rather than erroring out."""
    with Session(db_engines.auth_engine()) as s:
        s.add(User(
            id="ghost", email="ghost@example.test",
            display_name="ghost", is_admin=False,
        ))
        s.commit()
    monkeypatch.setattr(settings, "logs_user", "ops")
    monkeypatch.setattr(settings, "logs_password", "secret")

    r = client.get(
        "/api/admin/users/download-all",
        headers=_basic_auth_header("ops", "secret"),
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        import json
        manifest = json.loads(z.read("manifest.json"))
    ghost_row = next(r for r in manifest if r["user_id"] == "ghost")
    assert ghost_row["included"] is False
    assert ghost_row["reason"] == "no_db_on_disk"
