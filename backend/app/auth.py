"""Passkey-backed, server-side-session authentication.

This module provides the dependency helpers that the rest of the app uses:

* ``get_current_user`` — returns the authenticated ``User`` row.
* ``get_current_user_id`` — returns the user id string, used by
  ``app.database.get_session`` to pick the user's DB engine.
* ``require_admin`` — dep that 401s non-admins.

The HTTP endpoints themselves live in :mod:`app.routers.webauthn`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from app.auth_models import AuthSession, User
from app.config import settings
from app.db_engines import auth_engine


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _auth_session_dep():
    """Yield a Session bound to the auth DB."""

    with Session(auth_engine()) as s:
        yield s


def _cookie_token(request: Request) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


def get_current_user(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias="session"),
    auth_db: Session = Depends(_auth_session_dep),
) -> User:
    token = session_cookie or _cookie_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = auth_db.get(AuthSession, hash_token(token))
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid session")
    if row.expires_at < _now():
        auth_db.delete(row)
        auth_db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    user = auth_db.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="User disabled")
    # Sliding last-seen update (best-effort; no extra round-trip on failure).
    row.last_seen_at = _now()
    auth_db.add(row)
    auth_db.commit()
    # Tag log records for this request.
    from app.routers.debug import current_user_id as _user_ctx

    _user_ctx.set(user.id)
    return user


def get_current_user_id(user: User = Depends(get_current_user)) -> str:
    return user.id


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user


def create_session(
    auth_db: Session,
    user: User,
    request: Request,
    response: Response,
) -> str:
    """Create a server-side session row and set the cookie; return the raw token."""

    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(days=settings.session_ttl_days)
    row = AuthSession(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=expires_at,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip=(request.client.host if request.client else None),
    )
    auth_db.add(row)
    user.last_login_at = _now()
    auth_db.add(user)
    auth_db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 86400,
    )
    return token


def revoke_session(
    auth_db: Session,
    token: str | None,
    response: Response,
) -> None:
    if token:
        row = auth_db.get(AuthSession, hash_token(token))
        if row is not None:
            auth_db.delete(row)
            auth_db.commit()
    response.delete_cookie(key=settings.session_cookie_name)


def list_user_sessions(auth_db: Session, user_id: str) -> list[AuthSession]:
    return list(
        auth_db.exec(
            select(AuthSession)
            .where(AuthSession.user_id == user_id)
            .order_by(AuthSession.last_seen_at.desc())  # type: ignore[arg-type]
        )
    )


def _auth_db_dep():
    """Public re-export for routers."""

    yield from _auth_session_dep()


# Backwards-compat: tests and some routers imported ``get_current_user``
# from this module. Keep the name; behavior now enforces real auth.
__all__ = [
    "AuthSession",
    "User",
    "_auth_db_dep",
    "create_session",
    "get_current_user",
    "get_current_user_id",
    "hash_token",
    "list_user_sessions",
    "require_admin",
    "revoke_session",
]
