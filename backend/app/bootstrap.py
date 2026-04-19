"""First-boot migration: promote existing single-tenant DB to admin user.

Behaviour:
  * If ``data/auth.db`` already has users, do nothing.
  * If there are no users but ``ADMIN_EMAIL`` is unset, log a warning and
    abort — this prevents orphaning production data.
  * Otherwise, snapshot ``data/diet_tracker.db`` into ``data/legacy/`` (if
    present), create the admin user, and move (rename) the legacy DB into
    ``data/users/<admin_id>/diet_tracker.db``.
  * Emit a single-use bootstrap invite that the admin uses once to register
    their first passkey from a browser.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.auth_models import AuthState, Invite, User
from app.config import settings
from app.db_engines import (
    auth_engine,
    data_root,
    legacy_archive_dir,
    legacy_db_path,
    user_db_dir,
    user_db_path,
)

logger = logging.getLogger(__name__)

BOOTSTRAP_FLAG = "bootstrap_completed"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_invite(
    db: Session,
    *,
    created_by: str | None,
    is_bootstrap: bool,
    email_hint: str | None = None,
) -> tuple[str, Invite]:
    token = secrets.token_urlsafe(32)
    invite = Invite(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        email_hint=email_hint,
        created_by=created_by,
        expires_at=_now() + timedelta(days=settings.invite_ttl_days),
        is_bootstrap=is_bootstrap,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return token, invite


def run_bootstrap() -> None:
    """Idempotent first-boot bootstrap. Safe to call on every startup."""

    # The auth engine creation also creates tables.
    with Session(auth_engine()) as db:
        state = db.get(AuthState, BOOTSTRAP_FLAG)
        if state is not None:
            return
        existing_users = db.exec(select(User)).first()
        if existing_users is not None:
            # Someone already registered; mark completed.
            db.add(AuthState(key=BOOTSTRAP_FLAG, value=_now().isoformat()))
            db.commit()
            return

        admin_email = (settings.admin_email or "").strip().lower()
        if not admin_email:
            logger.warning(
                "Auth DB has no users and ADMIN_EMAIL is not set — bootstrap "
                "deferred. Set ADMIN_EMAIL in .env to promote a user and "
                "migrate any existing data/diet_tracker.db."
            )
            return

        admin_id = uuid.uuid4().hex
        admin = User(
            id=admin_id,
            email=admin_email,
            display_name=admin_email.split("@")[0],
            is_admin=True,
        )
        db.add(admin)

        # Migrate the legacy DB if present.
        legacy = legacy_db_path()
        target_dir = user_db_dir(admin_id)
        target = user_db_path(admin_id)
        if legacy.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            legacy_archive_dir().mkdir(parents=True, exist_ok=True)
            ts = _now().strftime("%Y%m%dT%H%M%SZ")
            snapshot = legacy_archive_dir() / f"prod_backup_{ts}.db"
            shutil.copy2(legacy, snapshot)
            logger.info("Snapshotted legacy DB to %s", snapshot)
            shutil.move(str(legacy), str(target))
            logger.info("Migrated legacy DB to %s", target)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                "No legacy DB found; admin %s will get a fresh per-user DB.",
                admin_email,
            )

        token, _ = _make_invite(
            db,
            created_by=None,
            is_bootstrap=True,
            email_hint=admin_email,
        )
        # Print the one-time URL to the logs so the operator can grab it.
        url = f"{settings.webauthn_origin.rstrip('/')}/invite/{token}"
        logger.warning(
            "BOOTSTRAP INVITE (single use, %dd): %s",
            settings.invite_ttl_days,
            url,
        )

        db.add(AuthState(key=BOOTSTRAP_FLAG, value=_now().isoformat()))
        db.commit()

    # Touch data_root to ensure dir tree exists (no-op if already).
    data_root()
