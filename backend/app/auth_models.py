"""Auth-DB tables: users, WebAuthn credentials + challenges, sessions, invites.

These models live in a separate logical namespace from the per-user data
models in ``app.models``. They all still register against ``SQLModel.metadata``
because SQLModel shares one metadata registry, but ``app.db_engines`` uses the
``AUTH_TABLE_NAMES`` set to scope ``create_all`` so each engine only creates the
tables it owns.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime

from sqlalchemy import LargeBinary
from sqlmodel import Column, Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(SQLModel, table=True):
    __tablename__ = "auth_users"
    id: str = Field(primary_key=True)  # uuid4 hex
    email: str = Field(unique=True, index=True)
    display_name: str
    is_admin: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    last_login_at: datetime | None = None
    disabled_at: datetime | None = None


class WebAuthnCredential(SQLModel, table=True):
    __tablename__ = "auth_webauthn_credentials"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="auth_users.id", index=True)
    credential_id: bytes = Field(
        sa_column=Column(LargeBinary, unique=True, index=True, nullable=False)
    )
    public_key: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    sign_count: int = 0
    transports: str | None = None  # comma-separated list
    aaguid: str | None = None
    nickname: str | None = None
    backup_eligible: bool = False
    backup_state: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None


class WebAuthnChallenge(SQLModel, table=True):
    __tablename__ = "auth_webauthn_challenges"
    id: int | None = Field(default=None, primary_key=True)
    user_id: str | None = Field(default=None, foreign_key="auth_users.id", index=True)
    challenge: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    purpose: str  # "registration" | "authentication"
    # For authentication we may not know the user yet (discoverable credentials);
    # store the email hint that was used to scope allowCredentials.
    email_hint: str | None = None
    invite_id: int | None = Field(default=None, foreign_key="auth_invites.id")
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    # token_hash is the SHA-256 of the opaque token we send in the cookie.
    token_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="auth_users.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=_utcnow)
    user_agent: str | None = None
    ip: str | None = None


class Invite(SQLModel, table=True):
    __tablename__ = "auth_invites"
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    email_hint: str | None = None  # optional display, not validated at consume time
    created_by: str | None = Field(default=None, foreign_key="auth_users.id")
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    consumed_at: datetime | None = None
    consumed_by: str | None = Field(default=None, foreign_key="auth_users.id")
    is_bootstrap: bool = False  # auto-generated first-boot invite


class AuthState(SQLModel, table=True):
    """Key/value singletons for the auth DB (e.g. bootstrap_completed)."""

    __tablename__ = "auth_state"
    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=_utcnow)


AUTH_TABLE_NAMES: set[str] = {
    "auth_users",
    "auth_webauthn_credentials",
    "auth_webauthn_challenges",
    "auth_sessions",
    "auth_invites",
    "auth_state",
}


__all__ = [
    "AUTH_TABLE_NAMES",
    "AuthSession",
    "AuthState",
    "Invite",
    "User",
    "WebAuthnChallenge",
    "WebAuthnCredential",
    "_utcnow",
    "dt",
]
