"""Bootstrap migration tests.

Verify that an existing single-tenant ``data/diet_tracker.db`` is moved to the
admin user's per-user path on first boot, and that the bootstrap is idempotent.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlmodel import Session, select

import app.bootstrap as bootstrap
import app.db_engines as db_engines
from app.auth_models import AuthState, Invite, User
from app.config import settings


@pytest.fixture
def tmp_data_root(monkeypatch, tmp_path):
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


def _write_legacy_db(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO legacy_marker (note) VALUES ('hello')")
    conn.commit()
    conn.close()


def test_bootstrap_creates_admin_and_migrates_legacy_db(
    monkeypatch, tmp_data_root
):
    monkeypatch.setattr(settings, "admin_email", "admin@example.test")

    legacy = db_engines.legacy_db_path()
    _write_legacy_db(legacy)
    assert legacy.exists()

    bootstrap.run_bootstrap()

    # Legacy should be moved (no longer at old path).
    assert not legacy.exists()

    # Admin user created.
    with Session(db_engines.auth_engine()) as db:
        admin = db.exec(select(User).where(User.email == "admin@example.test")).first()
        assert admin is not None
        assert admin.is_admin is True
        # Bootstrap invite exists.
        invite = db.exec(select(Invite).where(Invite.is_bootstrap)).first()
        assert invite is not None
        # Bootstrap flag is NOT set yet — gating happens after the admin
        # actually registers a passkey.
        flag = db.get(AuthState, bootstrap.BOOTSTRAP_FLAG)
        assert flag is None

    # DB file is now at per-user path.
    admin_db = db_engines.user_db_path(admin.id)
    assert admin_db.exists()

    # And it retained the legacy data.
    conn = sqlite3.connect(str(admin_db))
    rows = conn.execute("SELECT note FROM legacy_marker").fetchall()
    conn.close()
    assert rows == [("hello",)]

    # Snapshot archived.
    snapshots = list(db_engines.legacy_archive_dir().glob("prod_backup_*.db"))
    assert len(snapshots) == 1


def test_bootstrap_is_idempotent(monkeypatch, tmp_data_root):
    monkeypatch.setattr(settings, "admin_email", "admin@example.test")
    bootstrap.run_bootstrap()
    with Session(db_engines.auth_engine()) as db:
        users_before = len(list(db.exec(select(User))))
        # Simulate the admin having registered a passkey so subsequent
        # startups don't mint fresh invites.
        from app.auth_models import WebAuthnCredential

        admin = db.exec(select(User).where(User.is_admin)).first()
        db.add(
            WebAuthnCredential(
                user_id=admin.id,
                credential_id=b"fake-cred-id",
                public_key=b"fake-pub-key",
            )
        )
        db.commit()
        invites_before = len(list(db.exec(select(Invite))))
    bootstrap.run_bootstrap()
    with Session(db_engines.auth_engine()) as db:
        users_after = len(list(db.exec(select(User))))
        invites_after = len(list(db.exec(select(Invite))))
        # Now that admin has a passkey, the bootstrap flag should be set.
        assert db.get(AuthState, bootstrap.BOOTSTRAP_FLAG) is not None
    assert users_before == users_after
    assert invites_before == invites_after


def test_bootstrap_reissues_invite_when_admin_has_no_passkey(
    monkeypatch, tmp_data_root
):
    """Every restart while the admin has no passkey should surface a fresh URL."""
    monkeypatch.setattr(settings, "admin_email", "admin@example.test")
    bootstrap.run_bootstrap()
    with Session(db_engines.auth_engine()) as db:
        first = db.exec(select(Invite).where(Invite.is_bootstrap)).all()
        assert len(first) == 1
    bootstrap.run_bootstrap()
    with Session(db_engines.auth_engine()) as db:
        all_invites = db.exec(select(Invite).where(Invite.is_bootstrap)).all()
        # Two invites total: the first is now expired, the second is active.
        assert len(all_invites) == 2
        active = [i for i in all_invites if i.consumed_at is None and i.expires_at > bootstrap._now()]
        assert len(active) == 1


def test_bootstrap_skips_when_admin_email_missing(monkeypatch, tmp_data_root):
    monkeypatch.setattr(settings, "admin_email", "")
    bootstrap.run_bootstrap()
    with Session(db_engines.auth_engine()) as db:
        assert db.exec(select(User)).first() is None
        # No flag written, so a subsequent run with an email still works.
        assert db.get(AuthState, bootstrap.BOOTSTRAP_FLAG) is None
