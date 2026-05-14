"""Chart payload cache for slow rendering endpoints.

Lazy-fill cache for two expensive endpoints, both of which run one
``fit_curve`` per (exercise_id, as_of_date):

* ``GET /api/planner/curve-snapshot/{exercise_id}?date=…`` — historical
  curve view in the dashboard's "Recent Sessions" expanded panel.
* ``GET /api/workout-sessions/{ws_id}/beta-evolution`` — in-session β
  evolution sparkline (active workout + dashboard expanded days).

Cache rows are versioned via ``CACHE_VERSION``; bumping the version
invalidates everything implicitly on the next read by changing the key
prefix. Invalidation is otherwise driven by domain events (set add /
update / delete, session date change, session delete, bodyweight log
change, exercise model-affecting edit). Invalidation runs in the same
transaction as the mutation so a failed commit doesn't leave stale
rows behind.

The fitting window for ``fit_curve`` is ``[as_of - 30d, as_of]``, so a
new set on date ``D`` can affect curve-snapshot rows with ``on_date``
in ``[D, D + 30d]`` (earlier ``as_of`` dates don't see ``D`` because
the set is in their future). β-evolution payloads depend on
per-exercise fresh-curve fits using the same window, so the same
``[D, D + 30d]`` rule applies for cross-session β-evolution
invalidation.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from app.models import ChartCache, WorkoutSession, WorkoutSet

logger = logging.getLogger(__name__)

# Bump when payload shape or upstream computation semantics change.
# v2 (this commit): scipy fit grid reduced + per-request bodyweight memo
# changes the numerical curve values microscopically; bumping forces a
# clean re-fit on first access so users see the new performance profile.
CACHE_VERSION = "v2"

# Per-kind versions override CACHE_VERSION on a single cache namespace
# without invalidating the others. Bump when a particular endpoint's
# payload semantics change without touching the rest.
BETA_EVOL_VERSION = "v3"  # v3: same scipy-grid bump as CACHE_VERSION v2

KIND_CURVE = "curve_snapshot"
KIND_BETA_EVOL = "beta_evolution"
KIND_FATIGUE = "fatigue_profile"

# Match fit_curve's lookback window in strength_model.
WINDOW_DAYS = 30


def curve_key(exercise_id: int, on_date: date) -> str:
    return f"curve:{CACHE_VERSION}:{exercise_id}:{on_date.isoformat()}"


def beta_evol_key(workout_session_id: int) -> str:
    return f"betaevol:{BETA_EVOL_VERSION}:{workout_session_id}"


def fatigue_key(
    exercise_id: int, days: int, session_date: date | None,
) -> str:
    sd = session_date.isoformat() if session_date is not None else "auto"
    return f"fatigue:{CACHE_VERSION}:{exercise_id}:{days}:{sd}"


def cache_get(session: Session, key: str) -> dict[str, Any] | None:
    row = session.exec(
        select(ChartCache).where(ChartCache.cache_key == key)
    ).first()
    if row is None:
        return None
    try:
        return json.loads(row.payload_json)
    except (ValueError, TypeError):
        # Corrupt payload — drop and treat as miss.
        session.delete(row)
        session.commit()
        return None


def cache_set(
    session: Session,
    key: str,
    kind: str,
    payload: dict[str, Any],
    *,
    workout_session_id: int | None = None,
    exercise_id: int | None = None,
    on_date: date | None = None,
) -> None:
    """Atomic upsert. Concurrent fills resolve to the second writer winning."""
    payload_json = json.dumps(payload, default=str)
    existing = session.exec(
        select(ChartCache).where(ChartCache.cache_key == key)
    ).first()
    if existing is not None:
        existing.payload_json = payload_json
        existing.kind = kind
        existing.workout_session_id = workout_session_id
        existing.exercise_id = exercise_id
        existing.on_date = on_date
        session.add(existing)
        session.commit()
        return
    row = ChartCache(
        cache_key=key,
        kind=kind,
        workout_session_id=workout_session_id,
        exercise_id=exercise_id,
        on_date=on_date,
        payload_json=payload_json,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Another writer beat us to the unique key. Roll back our insert
        # and update theirs in place so the latest value still wins.
        session.rollback()
        loser = session.exec(
            select(ChartCache).where(ChartCache.cache_key == key)
        ).first()
        if loser is not None:
            loser.payload_json = payload_json
            loser.kind = kind
            loser.workout_session_id = workout_session_id
            loser.exercise_id = exercise_id
            loser.on_date = on_date
            session.add(loser)
            session.commit()


# ─────────────────────────── invalidation ────────────────────────────


def _drop_curve_window(
    session: Session, exercise_id: int, anchor: date,
) -> None:
    """Drop curve_snapshot rows for this exercise whose on_date is in
    ``[anchor, anchor + WINDOW_DAYS]`` (the range affected by a set
    landing on ``anchor``)."""
    end = anchor + timedelta(days=WINDOW_DAYS)
    session.exec(
        delete(ChartCache).where(
            ChartCache.kind == KIND_CURVE,
            ChartCache.exercise_id == exercise_id,
            ChartCache.on_date >= anchor,
            ChartCache.on_date <= end,
        )
    )


def _drop_betaevol_for_session(session: Session, ws_id: int) -> None:
    session.exec(
        delete(ChartCache).where(
            ChartCache.kind == KIND_BETA_EVOL,
            ChartCache.workout_session_id == ws_id,
        )
    )


def _drop_betaevol_for_later_sessions_with_exercise(
    session: Session, exercise_id: int, anchor: date,
) -> None:
    """β-evolution depends on per-exercise fresh curves with
    ``as_of=session_date, exclude_today=True``. A set on ``anchor`` for
    ``exercise_id`` can therefore affect later sessions whose date is in
    ``(anchor, anchor + WINDOW_DAYS]`` AND that contain that exercise."""
    end = anchor + timedelta(days=WINDOW_DAYS)
    later_session_ids = session.exec(
        select(WorkoutSession.id).where(
            WorkoutSession.date > anchor,
            WorkoutSession.date <= end,
            WorkoutSession.id.in_(
                select(WorkoutSet.session_id).where(
                    WorkoutSet.exercise_id == exercise_id,
                )
            ),
        )
    ).all()
    if not later_session_ids:
        return
    session.exec(
        delete(ChartCache).where(
            ChartCache.kind == KIND_BETA_EVOL,
            ChartCache.workout_session_id.in_(later_session_ids),
        )
    )


def _drop_fatigue_for_exercise(
    session: Session, exercise_id: int,
) -> None:
    """``fatigue_profile`` depends on the trailing 30-day window of sets
    for ``exercise_id`` plus a "latest session" anchor that's data-driven.
    Any set change for the exercise can shift the window contents or the
    auto-anchor, so drop every cached row for this exercise."""
    session.exec(
        delete(ChartCache).where(
            ChartCache.kind == KIND_FATIGUE,
            ChartCache.exercise_id == exercise_id,
        )
    )


def invalidate_for_set_change(
    session: Session,
    workout_session_id: int,
    exercise_id: int,
    on_date: date,
) -> None:
    """Call after add/update/delete of a workout set, in the same
    transaction as the mutation. Drops:

    * curve snapshots for ``(exercise_id, D)`` where D ∈ ``[on_date,
      on_date + 30d]``
    * β-evolution for ``workout_session_id``
    * β-evolution for any later session in the same 30-day window that
      contains ``exercise_id``
    """
    _drop_curve_window(session, exercise_id, on_date)
    _drop_betaevol_for_session(session, workout_session_id)
    _drop_betaevol_for_later_sessions_with_exercise(
        session, exercise_id, on_date,
    )
    _drop_fatigue_for_exercise(session, exercise_id)


def invalidate_for_session_delete(
    session: Session,
    workout_session_id: int,
    on_date: date,
    exercise_ids: list[int],
) -> None:
    """Call before deleting a workout session. Drops β-evolution for
    the session and curve windows for every exercise it touched."""
    _drop_betaevol_for_session(session, workout_session_id)
    for ex_id in set(exercise_ids):
        _drop_curve_window(session, ex_id, on_date)
        _drop_betaevol_for_later_sessions_with_exercise(
            session, ex_id, on_date,
        )
        _drop_fatigue_for_exercise(session, ex_id)


def invalidate_all_charts(session: Session) -> None:
    """Broad nuke for events that cross-cut every exercise/session
    (bodyweight log changes, exercise-model edits)."""
    session.exec(delete(ChartCache))
