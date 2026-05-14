"""Telemetry endpoints: frontend event ingest + admin summary readout."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import telemetry
from app.auth import get_current_user
from app.auth_models import User
from app.routers.debug import _verify_basic_auth

router = APIRouter(prefix="/api", tags=["telemetry"])
_log = logging.getLogger(__name__)


class FrontendEvent(BaseModel):
    name: str
    duration_ms: float | None = None
    route: str | None = None
    meta: dict[str, Any] | None = None


class FrontendEventBatch(BaseModel):
    events: list[FrontendEvent]


@router.post("/telemetry/frontend")
def post_frontend_telemetry(
    batch: FrontendEventBatch,
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    """Ingest a batch of frontend timing events.

    Called from ``frontend/src/lib/telemetry.ts`` via ``navigator.sendBeacon``
    on page hide, plus a periodic background flush. Best-effort — we never
    return a 4xx for malformed individual events.
    """
    n = 0
    for ev in batch.events:
        if not ev.name:
            continue
        if len(ev.name) > 120:
            continue
        try:
            telemetry.record_frontend_event(
                user_id=user.id,
                route=ev.route,
                name=ev.name,
                duration_ms=ev.duration_ms,
                meta=ev.meta,
            )
            n += 1
        except Exception:
            _log.exception("telemetry: failed to record frontend event")
    return {"recorded": n}


@router.get("/debug/telemetry/summary")
def get_telemetry_summary(
    _user: str = Depends(_verify_basic_auth),
    hours: int = Query(default=24, ge=1, le=24 * 14),
) -> dict[str, Any]:
    """Return per-endpoint aggregates, slow queries, and frontend hot spots.

    Uses the same HTTP Basic auth as ``/api/debug/logs`` so it can be
    inspected from outside an authenticated browser session.
    """
    try:
        return telemetry.summary(hours=hours)
    except Exception as e:
        _log.exception("telemetry: summary failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
