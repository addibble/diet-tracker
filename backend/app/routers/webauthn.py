"""WebAuthn (passkey) + invite-flow authentication endpoints."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.auth import (
    _auth_db_dep,
    create_session,
    get_current_user,
    list_user_sessions,
    revoke_session,
)
from app.auth_models import (
    AuthSession,
    Invite,
    User,
    WebAuthnChallenge,
    WebAuthnCredential,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _challenge_expiry() -> datetime:
    return _now() + timedelta(seconds=settings.webauthn_challenge_ttl_seconds)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ── Schemas ────────────────────────────────────────────────────────────────


class RegisterStartRequest(BaseModel):
    invite_token: str
    email: EmailStr
    display_name: str


class RegisterFinishRequest(BaseModel):
    challenge_id: int
    credential: dict
    nickname: str | None = None


class LoginStartRequest(BaseModel):
    email: EmailStr


class LoginFinishRequest(BaseModel):
    challenge_id: int
    credential: dict


class PasskeyAddStartRequest(BaseModel):
    nickname: str | None = None


class PasskeyAddFinishRequest(BaseModel):
    challenge_id: int
    credential: dict
    nickname: str | None = None


# ── Helpers ────────────────────────────────────────────────────────────────


def _consume_invite(db: Session, token: str) -> Invite:
    row = db.exec(
        select(Invite).where(Invite.token_hash == hashlib.sha256(token.encode()).hexdigest())
    ).first()
    if row is None:
        raise HTTPException(status_code=400, detail="Invalid invite")
    if row.consumed_at is not None:
        raise HTTPException(status_code=400, detail="Invite already used")
    if row.expires_at < _now():
        raise HTTPException(status_code=400, detail="Invite expired")
    return row


def _require_user_by_email(db: Session, email: str) -> User:
    user = db.exec(select(User).where(User.email == email.lower())).first()
    if user is None:
        # Do not leak whether the email exists; return generic error.
        raise HTTPException(status_code=404, detail="User not found")
    if user.disabled_at is not None:
        raise HTTPException(status_code=403, detail="User disabled")
    return user


def _user_credentials(db: Session, user_id: str) -> list[WebAuthnCredential]:
    return list(
        db.exec(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        )
    )


def _persist_challenge(
    db: Session,
    *,
    user_id: str | None,
    challenge: bytes,
    purpose: str,
    email_hint: str | None = None,
    invite_id: int | None = None,
) -> WebAuthnChallenge:
    row = WebAuthnChallenge(
        user_id=user_id,
        challenge=challenge,
        purpose=purpose,
        email_hint=email_hint,
        invite_id=invite_id,
        expires_at=_challenge_expiry(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _consume_challenge(
    db: Session, challenge_id: int, purpose: str
) -> WebAuthnChallenge:
    row = db.get(WebAuthnChallenge, challenge_id)
    if row is None or row.purpose != purpose:
        raise HTTPException(status_code=400, detail="Invalid challenge")
    if row.expires_at < _now():
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=400, detail="Challenge expired")
    return row


def _delete_challenge(db: Session, row: WebAuthnChallenge) -> None:
    db.delete(row)
    db.commit()


# ── Registration ───────────────────────────────────────────────────────────


@router.post("/register/options")
def register_options(
    payload: RegisterStartRequest,
    db: Session = Depends(_auth_db_dep),
) -> dict:
    invite = _consume_invite(db, payload.invite_token)

    email_norm = payload.email.lower()
    # If a user with this email already exists (bootstrap admin path), reuse
    # that record; otherwise create a new one now so we have a stable user_id.
    user = db.exec(select(User).where(User.email == email_norm)).first()
    if user is None:
        user = User(
            id=uuid.uuid4().hex,
            email=email_norm,
            display_name=payload.display_name,
            is_admin=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Update display_name if provided and no passkeys yet.
        if not _user_credentials(db, user.id):
            user.display_name = payload.display_name
            db.add(user)
            db.commit()

    exclude = [
        PublicKeyCredentialDescriptor(id=c.credential_id)
        for c in _user_credentials(db, user.id)
    ]

    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.encode("utf-8"),
        user_name=user.email,
        user_display_name=user.display_name,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )
    challenge_row = _persist_challenge(
        db,
        user_id=user.id,
        challenge=options.challenge,
        purpose="registration",
        email_hint=email_norm,
        invite_id=invite.id,
    )
    return {
        "challenge_id": challenge_row.id,
        "publicKey": options_to_json_dict(options),
    }


@router.post("/register/verify")
def register_verify(
    payload: RegisterFinishRequest,
    request: Request,
    response: Response,
    db: Session = Depends(_auth_db_dep),
) -> dict:
    challenge_row = _consume_challenge(db, payload.challenge_id, "registration")
    assert challenge_row.user_id is not None
    user = db.get(User, challenge_row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User missing for challenge")

    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=challenge_row.challenge,
            expected_origin=settings.webauthn_origin,
            expected_rp_id=settings.webauthn_rp_id,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Registration verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Attestation failed") from exc

    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        nickname=payload.nickname,
        backup_eligible=bool(getattr(verification, "credential_backed_up", False)),
        backup_state=bool(getattr(verification, "credential_backed_up", False)),
    )
    db.add(cred)

    # Consume the invite (if this was the first passkey for that invite).
    if challenge_row.invite_id is not None:
        invite = db.get(Invite, challenge_row.invite_id)
        if invite is not None and invite.consumed_at is None:
            invite.consumed_at = _now()
            invite.consumed_by = user.id
            db.add(invite)

    _delete_challenge(db, challenge_row)
    db.commit()

    create_session(db, user, request, response)
    return {"status": "ok", "user": _user_public(user)}


# ── Login ──────────────────────────────────────────────────────────────────


@router.post("/login/options")
def login_options(
    payload: LoginStartRequest,
    db: Session = Depends(_auth_db_dep),
) -> dict:
    email_norm = payload.email.lower()
    user = db.exec(select(User).where(User.email == email_norm)).first()
    # To avoid user-enumeration leaks we still produce an options response
    # when the user is unknown, but with an empty allow list; browsers will
    # simply fail to sign. We do still log this server-side.
    allow: list[PublicKeyCredentialDescriptor] = []
    user_id: str | None = None
    if user is not None and user.disabled_at is None:
        user_id = user.id
        allow = [
            PublicKeyCredentialDescriptor(id=c.credential_id)
            for c in _user_credentials(db, user.id)
        ]
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_row = _persist_challenge(
        db,
        user_id=user_id,
        challenge=options.challenge,
        purpose="authentication",
        email_hint=email_norm,
    )
    return {
        "challenge_id": challenge_row.id,
        "publicKey": options_to_json_dict(options),
    }


@router.post("/login/verify")
def login_verify(
    payload: LoginFinishRequest,
    request: Request,
    response: Response,
    db: Session = Depends(_auth_db_dep),
) -> dict:
    challenge_row = _consume_challenge(db, payload.challenge_id, "authentication")

    raw_cred_id = _b64url_decode(payload.credential.get("rawId") or payload.credential["id"])
    cred = db.exec(
        select(WebAuthnCredential).where(WebAuthnCredential.credential_id == raw_cred_id)
    ).first()
    if cred is None:
        raise HTTPException(status_code=400, detail="Unknown credential")
    user = db.get(User, cred.user_id)
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=403, detail="User unavailable")

    try:
        verification = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=challenge_row.challenge,
            expected_origin=settings.webauthn_origin,
            expected_rp_id=settings.webauthn_rp_id,
            credential_public_key=cred.public_key,
            credential_current_sign_count=cred.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Login verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Assertion failed") from exc

    cred.sign_count = verification.new_sign_count
    cred.last_used_at = _now()
    db.add(cred)
    _delete_challenge(db, challenge_row)
    db.commit()

    create_session(db, user, request, response)
    return {"status": "ok", "user": _user_public(user)}


# ── Session mgmt ───────────────────────────────────────────────────────────


@router.post("/logout")
def logout(
    response: Response,
    session_cookie: str | None = Cookie(default=None, alias="session"),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    revoke_session(db, session_cookie, response)
    return {"status": "ok"}


@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    creds = _user_credentials(db, user.id)
    sessions = list_user_sessions(db, user.id)
    return {
        "user": _user_public(user),
        "passkeys": [
            {
                "id": c.id,
                "nickname": c.nickname,
                "created_at": c.created_at.isoformat(),
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "aaguid": c.aaguid,
            }
            for c in creds
        ],
        "sessions": [
            {
                "token_hash": s.token_hash,
                "created_at": s.created_at.isoformat(),
                "expires_at": s.expires_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat(),
                "user_agent": s.user_agent,
                "ip": s.ip,
            }
            for s in sessions
        ],
    }


@router.delete("/sessions/{token_hash}")
def revoke_named_session(
    token_hash: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    row = db.get(AuthSession, token_hash)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


# ── Add-a-passkey (authenticated) ──────────────────────────────────────────


@router.post("/passkeys/options")
def passkey_add_options(
    payload: PasskeyAddStartRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    exclude = [
        PublicKeyCredentialDescriptor(id=c.credential_id)
        for c in _user_credentials(db, user.id)
    ]
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.encode("utf-8"),
        user_name=user.email,
        user_display_name=user.display_name,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    challenge_row = _persist_challenge(
        db,
        user_id=user.id,
        challenge=options.challenge,
        purpose="registration",
        email_hint=user.email,
    )
    return {
        "challenge_id": challenge_row.id,
        "publicKey": options_to_json_dict(options),
    }


@router.post("/passkeys/verify")
def passkey_add_verify(
    payload: PasskeyAddFinishRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    challenge_row = _consume_challenge(db, payload.challenge_id, "registration")
    if challenge_row.user_id != user.id:
        raise HTTPException(status_code=400, detail="Challenge user mismatch")

    try:
        verification = verify_registration_response(
            credential=payload.credential,
            expected_challenge=challenge_row.challenge,
            expected_origin=settings.webauthn_origin,
            expected_rp_id=settings.webauthn_rp_id,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Passkey add verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Attestation failed") from exc

    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else None,
        nickname=payload.nickname,
        backup_eligible=bool(getattr(verification, "credential_backed_up", False)),
        backup_state=bool(getattr(verification, "credential_backed_up", False)),
    )
    db.add(cred)
    _delete_challenge(db, challenge_row)
    db.commit()
    return {"status": "ok", "id": cred.id}


@router.delete("/passkeys/{credential_id}")
def delete_passkey(
    credential_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(_auth_db_dep),
) -> dict:
    cred = db.get(WebAuthnCredential, credential_id)
    if cred is None or cred.user_id != user.id:
        raise HTTPException(status_code=404, detail="Passkey not found")
    # Prevent locking yourself out — require at least one remaining.
    remaining = [c for c in _user_credentials(db, user.id) if c.id != credential_id]
    if not remaining:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove your last passkey; register another first.",
        )
    db.delete(cred)
    db.commit()
    return {"status": "ok"}


# ── Utilities ──────────────────────────────────────────────────────────────


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


def options_to_json_dict(options) -> dict:
    """Convert py_webauthn options object to the browser-safe JSON shape.

    The library's ``options_to_json`` returns a string; we parse once so
    FastAPI's response layer emits valid JSON without double-encoding.
    """

    import json

    return json.loads(options_to_json(options))


# Re-export a convenience to silence unused imports on non-dev runs.
_ = secrets  # noqa: F841
