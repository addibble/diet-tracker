"""CurveFit storage-sync adapter.

Implements the minimal GET/PUT-of-one-JSON-document contract CurveFit's
``StorageProvider`` speaks (docs/SYNC_PLAN.md), backed by the user's existing
diet-tracker SQLite DB. This module is **storage only** — it projects rows to
CurveFit's document shape and (Phase 3) ingests them back; it never runs the
strength model.

Auth is a scoped bearer token (``CurveFitSyncToken``), so the cross-origin
CurveFit app authenticates with an ``Authorization: Bearer`` header instead of
the passkey session cookie. Token management endpoints below are themselves
guarded by the normal passkey session (only a logged-in user can mint a token).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from collections.abc import Generator
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import _auth_db_dep, get_current_user, hash_token
from app.auth_models import CurveFitSyncToken, User
from app.config import settings
from app.curvefit_ingest import ingest_curvefit_document
from app.curvefit_projection import build_curvefit_document
from app.db_engines import user_engine

router = APIRouter(prefix="/api/curvefit-sync", tags=["curvefit-sync"])

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── Bearer-token auth ──────────────────────────────────────────────────────


def get_sync_user(
    authorization: str | None = Header(default=None),
    auth_db: Session = Depends(_auth_db_dep),
) -> User:
    """Resolve the user from an ``Authorization: Bearer <token>`` header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    row = auth_db.get(CurveFitSyncToken, hash_token(token))
    if row is None or row.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Invalid sync token")
    user = auth_db.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="User disabled")
    row.last_used_at = _now()
    auth_db.add(row)
    auth_db.commit()
    return user


def get_sync_session(user: User = Depends(get_sync_user)) -> Generator[Session, None, None]:
    """Yield a Session bound to the token owner's per-user data engine."""
    with Session(user_engine(user.id)) as session:
        yield session


# ── Document version token (ETag) ──────────────────────────────────────────


def _document_etag(document: dict) -> str:
    """Stable content hash of the projected document.

    Excludes the volatile ``updated_at`` stamp so the token only changes when
    the *data* changes — that's what makes diet-tracker-side edits (made
    through its own UI) invalidate a stale CurveFit push. See SYNC_PLAN.md.
    """
    stable = {k: v for k, v in document.items() if k != "updated_at"}
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def _normalize_etag(etag: str | None) -> str:
    """Normalize a validator for comparison.

    A compressing proxy in front of the origin (e.g. Cloudflare) rewrites the
    strong ``ETag: "abc"`` we emit into a *weak* ``W/"abc"`` when it gzip/brotli
    the response. The client sends that weak value back as ``If-Match``, so a
    raw string compare against our freshly-recomputed strong tag always fails
    (=> perpetual 412). Strip the ``W/`` prefix and surrounding quotes so the
    comparison is proxy-agnostic.
    """
    if not etag:
        return ""
    value = etag.strip()
    if value[:2].upper() == "W/":
        value = value[2:]
    return value.strip().strip('"')


# ── Sync document endpoints ────────────────────────────────────────────────


@router.get("")
def get_document(response: Response, session: Session = Depends(get_sync_session)):
    """Project the caller's workout log to a CurveFit sync document."""
    document = build_curvefit_document(session)
    response.headers["ETag"] = _document_etag(document)
    return document


@router.put("")
def put_document(
    document: dict,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    session: Session = Depends(get_sync_session),
):
    """Ingest a CurveFit sync document (merge/upsert), honoring ``If-Match``.

    The version token is a content hash of the current projection, so any
    diet-tracker-side edit made between the client's pull and push changes it
    and yields 412 — the client then pull-merge-retries.
    """
    if if_match is not None:
        current = _document_etag(build_curvefit_document(session))
        if _normalize_etag(if_match) != _normalize_etag(current):
            logger.info(
                "curvefit-sync PUT If-Match mismatch: sent=%r computed=%r",
                if_match,
                current,
            )
            raise HTTPException(status_code=412, detail="Version conflict")
    summary = ingest_curvefit_document(session, document)
    new_etag = _document_etag(build_curvefit_document(session))
    response.headers["ETag"] = new_etag
    return {"ok": True, "summary": summary}


# ── Popup link page (mint a token for the cross-origin CurveFit app) ────────


def _validated_curvefit_origin(origin: str) -> str | None:
    """Return ``origin`` iff it is an allowed CurveFit origin, else None.

    Accepts the configured production origins exactly. Localhost is allowed
    **only when no production origins are configured** (dev), and is validated
    by parsing the URL (exact host, numeric port, no path/query/userinfo) so a
    crafted value like ``http://localhost:1"</script>...`` can't slip through a
    substring check and get reflected into the page.
    """
    if not origin:
        return None
    if origin in settings.curvefit_origins_list:
        return origin
    if settings.curvefit_origins_list:
        return None  # prod: only the configured allowlist, never arbitrary localhost
    try:
        parsed = urlparse(origin)
        port = parsed.port  # raises ValueError on a non-numeric port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return None
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    canonical = f"{parsed.scheme}://{parsed.hostname}"
    if port is not None:
        canonical += f":{port}"
    return canonical if canonical == origin else None


@router.get("/link", response_class=HTMLResponse)
def link_page(origin: str = ""):
    """Serve the passkey-gated linking page opened by CurveFit in a popup.

    Runs on diet-tracker's own origin, so the user's existing passkey session
    cookie authorizes the token mint. On success the token is posted back to
    the (validated) CurveFit opener via ``postMessage`` and the popup closes.

    The validated origin is embedded with ``json.dumps`` (never raw string
    interpolation), and the page derives its own API base from
    ``window.location.origin`` so it works behind TLS-terminating proxies
    without mixed-content failures.
    """
    target = _validated_curvefit_origin(origin)
    html = _LINK_PAGE_HTML.replace("__TARGET_ORIGIN__", json.dumps(target))
    return HTMLResponse(html)


# ── Token management (passkey-session guarded) ─────────────────────────────


class TokenCreate(BaseModel):
    label: str | None = None


class TokenCreated(BaseModel):
    token: str
    label: str | None
    created_at: datetime


class TokenInfo(BaseModel):
    label: str | None
    created_at: datetime
    last_used_at: datetime | None
    disabled: bool


@router.post("/tokens", response_model=TokenCreated, status_code=201)
def create_token(
    data: TokenCreate,
    user: User = Depends(get_current_user),
    auth_db: Session = Depends(_auth_db_dep),
):
    """Mint a new CurveFit sync token. The raw token is returned only once."""
    raw = secrets.token_urlsafe(32)
    row = CurveFitSyncToken(
        token_hash=hash_token(raw),
        user_id=user.id,
        label=data.label,
    )
    auth_db.add(row)
    auth_db.commit()
    return TokenCreated(token=raw, label=row.label, created_at=row.created_at)


@router.get("/tokens", response_model=list[TokenInfo])
def list_tokens(
    user: User = Depends(get_current_user),
    auth_db: Session = Depends(_auth_db_dep),
):
    rows = auth_db.exec(
        select(CurveFitSyncToken).where(CurveFitSyncToken.user_id == user.id)
    ).all()
    return [
        TokenInfo(
            label=r.label,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            disabled=r.disabled_at is not None,
        )
        for r in rows
    ]


@router.delete("/tokens", status_code=204)
def revoke_all_tokens(
    user: User = Depends(get_current_user),
    auth_db: Session = Depends(_auth_db_dep),
):
    """Revoke (disable) all of the caller's sync tokens."""
    rows = auth_db.exec(
        select(CurveFitSyncToken).where(CurveFitSyncToken.user_id == user.id)
    ).all()
    for r in rows:
        if r.disabled_at is None:
            r.disabled_at = _now()
            auth_db.add(r)
    auth_db.commit()


# ── Link page HTML (inline; served under /api so nginx proxies it) ──────────

_LINK_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Connect CurveFit</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; background:#f8fafc;
         color:#0f172a; display:flex; min-height:100vh; margin:0;
         align-items:center; justify-content:center; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:16px;
          padding:28px; max-width:360px; width:calc(100% - 32px); text-align:center; }
  h1 { font-size:18px; margin:0 0 6px; }
  p { font-size:13px; color:#475569; margin:6px 0; }
  button { margin-top:14px; background:#059669; color:#fff; border:0;
           border-radius:12px; padding:10px 16px; font-size:14px; font-weight:600;
           cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  a { color:#059669; }
  .err { color:#dc2626; }
</style>
</head>
<body>
  <div class="card">
    <h1>Connect CurveFit</h1>
    <p id="msg">Authorize this browser's CurveFit app to read your diet-tracker
       workout log.</p>
    <button id="go">Authorize CurveFit</button>
  </div>
<script>
  var TARGET_ORIGIN = __TARGET_ORIGIN__;
  // Derive the API base from the browser's own origin so this works behind a
  // TLS-terminating proxy (uvicorn may see http; the page is loaded over https).
  var API_BASE = window.location.origin + "/api/curvefit-sync";
  var msg = document.getElementById("msg");
  var btn = document.getElementById("go");

  if (TARGET_ORIGIN === null) {
    msg.innerHTML = '<span class="err">This CurveFit origin is not allowed. ' +
      'Set CURVEFIT_ORIGINS on the diet-tracker server and reopen.</span>';
    btn.disabled = true;
  }

  btn.addEventListener("click", function () {
    btn.disabled = true;
    msg.textContent = "Authorizing\u2026";
    fetch(API_BASE + "/tokens", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: "CurveFit" })
    }).then(function (res) {
      if (res.status === 401) {
        msg.innerHTML = 'Please <a href="/" target="_blank" rel="noopener">log in to ' +
          'diet-tracker</a> (opens a new tab), then click Authorize again.';
        btn.disabled = false;
        return null;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).then(function (data) {
      if (!data) return;
      if (window.opener) {
        window.opener.postMessage(
          { type: "curvefit-sync-token", token: data.token, endpoint: API_BASE },
          TARGET_ORIGIN
        );
      }
      msg.textContent = "Connected! You can close this window.";
      btn.style.display = "none";
      setTimeout(function () { window.close(); }, 1200);
    }).catch(function (e) {
      // textContent (not innerHTML) so an error string can never inject markup.
      msg.textContent = "Failed: " + e.message;
      btn.disabled = false;
    });
  });
</script>
</body>
</html>
"""
