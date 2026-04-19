"""Bootstrap + startup status: promote existing single-tenant DB to admin user.

Behaviour on every startup (idempotent, safe to call repeatedly):
  * Log a status banner: user count, admin email/id, passkey count,
    WebAuthn RP config — handy when debugging a fresh deploy.
  * If there are no users and ``ADMIN_EMAIL`` is unset, log a warning and
    abort — this prevents orphaning production data.
  * If the admin user does not yet exist, create them, and (if a legacy
    ``data/diet_tracker.db`` is present) snapshot it to ``data/legacy/`` and
    move it into ``data/users/<admin_id>/diet_tracker.db``.
  * If the admin has no WebAuthn passkeys registered yet, reuse the most
    recent un-consumed bootstrap invite (if any is still valid) or mint a
    fresh one, and log the one-time URL at WARNING level.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.auth_models import AuthState, Invite, User, WebAuthnCredential
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


def _emit_invite_url(token: str, *, reused: bool) -> None:
    origin = (settings.webauthn_origin or "").rstrip("/")
    url = f"{origin}/invite/{token}" if origin else f"/invite/{token}"
    logger.warning(
        "BOOTSTRAP INVITE (single use, %dd): %s",
        settings.invite_ttl_days,
        url,
    )


def _log_startup_status(
    *,
    user_count: int,
    admin: User | None,
    admin_passkeys: int,
    admin_email_env: str,
) -> None:
    logger.warning(
        "AUTH STATUS: users=%d admin_env=%s admin_db=%s passkeys=%d "
        "rp_id=%s origin=%s",
        user_count,
        admin_email_env or "<unset>",
        admin.email if admin else "<none>",
        admin_passkeys,
        settings.webauthn_rp_id or "<unset>",
        settings.webauthn_origin or "<unset>",
    )


def run_bootstrap() -> None:
    """Idempotent startup bootstrap. Safe to call on every startup."""

    # The auth engine creation also creates tables.
    with Session(auth_engine()) as db:
        admin_email = (settings.admin_email or "").strip().lower()
        users = db.exec(select(User)).all()
        admin = next(
            (u for u in users if u.is_admin and (not admin_email or u.email == admin_email)),
            None,
        ) or next((u for u in users if u.is_admin), None)
        admin_passkeys = 0
        if admin is not None:
            admin_passkeys = len(
                db.exec(
                    select(WebAuthnCredential).where(WebAuthnCredential.user_id == admin.id)
                ).all()
            )
        _log_startup_status(
            user_count=len(users),
            admin=admin,
            admin_passkeys=admin_passkeys,
            admin_email_env=admin_email,
        )

        # No admin yet → create one (first boot path, also runs if an earlier
        # boot skipped because ADMIN_EMAIL was unset).
        if admin is None:
            if not admin_email:
                logger.warning(
                    "No admin user and ADMIN_EMAIL is unset — skipping bootstrap. "
                    "Set ADMIN_EMAIL in .env and restart to promote a user."
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
            db.commit()

        # Admin exists but no passkey registered → mint a single bootstrap
        # invite *if one doesn't already exist*. If there's already an active
        # unconsumed bootstrap invite, just note it in the log — we can't
        # re-emit the URL since we only stored the hash. Operator can call
        # the admin API (or redeploy after bumping INVITE_TTL) to get a new
        # one if the original was lost.
        if admin_passkeys == 0:
            now = _now()
            active = db.exec(
                select(Invite)
                .where(Invite.is_bootstrap == True)  # noqa: E712
                .where(Invite.consumed_at.is_(None))
                .where(Invite.expires_at > now)
                .order_by(Invite.created_at.desc())
            ).first()
            if active is None:
                token, _ = _make_invite(
                    db,
                    created_by=None,
                    is_bootstrap=True,
                    email_hint=admin.email,
                )
                _emit_invite_url(token, reused=False)
            else:
                logger.warning(
                    "Admin %s has no passkey; an active bootstrap invite "
                    "already exists (expires %s). POST /api/admin/invites to "
                    "mint a replacement if the URL was lost.",
                    admin.email,
                    active.expires_at.isoformat(),
                )
        else:
            # Mark bootstrap as complete once the admin has at least one key.
            if db.get(AuthState, BOOTSTRAP_FLAG) is None:
                db.add(AuthState(key=BOOTSTRAP_FLAG, value=_now().isoformat()))
                db.commit()

    data_root()
