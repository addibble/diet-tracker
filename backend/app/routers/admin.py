"""Admin endpoints: user list, disable/delete, invite management."""

from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, delete, select

from app.auth import _auth_db_dep, require_admin
from app.auth_models import (
    AuthSession,
    Invite,
    User,
    WebAuthnChallenge,
    WebAuthnCredential,
)
from app.config import settings
from app.db_engines import dispose_user_engine, user_db_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class InviteCreate(BaseModel):
    email_hint: EmailStr | None = None


@router.get("/users")
def list_users(
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> list[dict]:
    users = list(db.exec(select(User).order_by(User.created_at)))
    creds_count: dict[str, int] = {}
    for u in users:
        creds_count[u.id] = len(
            list(
                db.exec(
                    select(WebAuthnCredential).where(WebAuthnCredential.user_id == u.id)
                )
            )
        )
    return [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "created_at": u.created_at.isoformat(),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "disabled_at": u.disabled_at.isoformat() if u.disabled_at else None,
            "passkey_count": creds_count.get(u.id, 0),
        }
        for u in users
    ]


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Refusing to disable yourself")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.disabled_at = _now()
    db.add(user)
    # Revoke all existing sessions for safety.
    db.exec(delete(AuthSession).where(AuthSession.user_id == user_id))  # type: ignore[arg-type]
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.disabled_at = None
    db.add(user)
    db.commit()
    return {"status": "ok"}


class DeleteConfirm(BaseModel):
    email_confirm: EmailStr


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: str,
    payload: DeleteConfirm,
    admin: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Refusing to delete yourself")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.email_confirm.lower() != user.email.lower():
        raise HTTPException(status_code=400, detail="Email confirmation mismatch")

    # Dispose engine first so SQLite file handle is released.
    dispose_user_engine(user_id)
    # Remove credentials, challenges, sessions, then the user row.
    db.exec(delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id))  # type: ignore[arg-type]
    db.exec(delete(WebAuthnChallenge).where(WebAuthnChallenge.user_id == user_id))  # type: ignore[arg-type]
    db.exec(delete(AuthSession).where(AuthSession.user_id == user_id))  # type: ignore[arg-type]
    db.delete(user)
    db.commit()

    # Remove per-user DB directory.
    udir = user_db_dir(user_id)
    if udir.exists():
        try:
            shutil.rmtree(udir)
        except OSError:
            logger.exception("Failed to remove user dir %s", udir)
    return {"status": "ok"}


@router.get("/invites")
def list_invites(
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> list[dict]:
    rows = list(db.exec(select(Invite).order_by(Invite.created_at.desc())))  # type: ignore[arg-type]
    return [
        {
            "id": r.id,
            "email_hint": r.email_hint,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "consumed_at": r.consumed_at.isoformat() if r.consumed_at else None,
            "consumed_by": r.consumed_by,
            "is_bootstrap": r.is_bootstrap,
        }
        for r in rows
    ]


@router.post("/invites")
def create_invite(
    payload: InviteCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    token = secrets.token_urlsafe(32)
    invite = Invite(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        email_hint=payload.email_hint.lower() if payload.email_hint else None,
        created_by=admin.id,
        expires_at=_now() + timedelta(days=settings.invite_ttl_days),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    url = f"{settings.webauthn_origin.rstrip('/')}/invite/{token}"
    return {
        "id": invite.id,
        "url": url,
        "expires_at": invite.expires_at.isoformat(),
    }


@router.delete("/invites/{invite_id}")
def revoke_invite(
    invite_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    row = db.get(Invite, invite_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if row.consumed_at is not None:
        raise HTTPException(status_code=400, detail="Already consumed")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.get("/sessions")
def list_all_sessions(
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> list[dict]:
    rows = list(
        db.exec(select(AuthSession).order_by(AuthSession.last_seen_at.desc()))  # type: ignore[arg-type]
    )
    return [
        {
            "token_hash": s.token_hash,
            "user_id": s.user_id,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat(),
            "user_agent": s.user_agent,
            "ip": s.ip,
        }
        for s in rows
    ]


@router.delete("/sessions/{token_hash}")
def revoke_any_session(
    token_hash: str,
    _: User = Depends(require_admin),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    row = db.get(AuthSession, token_hash)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}
