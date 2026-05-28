"""Thin planner router: active-plan CRUD + strength-curve prescription.

After the auto-planner removal, this router exposes only the endpoints
still used by the WorkoutSetEditor/TrainingPage UI:

- ``GET /active`` / ``DELETE /active`` — load/wipe the saved plan
- ``POST /active/exercises`` / ``DELETE /active/exercises/{id}`` — add/remove
- ``PATCH /active/reorder`` — reorder exercises
- ``GET /exercise-menu`` / ``GET /weekly-menu`` — exercise freshness data
- ``POST /prescribe-next`` — strength-curve sequential prescription
- ``POST /quick-start`` — create a workout session from a list of exercise IDs
"""

import datetime
import json
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.chart_cache import (
    KIND_BANDS,
    KIND_CURVE,
    KIND_FATIGUE,
    bands_key,
    cache_get,
    cache_set,
    curve_key,
    fatigue_key,
)
from app.config import user_today
from app.database import get_session
from app.exercise_groups import get_group_exercise_menu
from app.exercise_loads import bodyweight_by_date, latest_bodyweight
from app.models import (
    Exercise,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
)
from app.planner_state import (
    add_exercises_to_plan,
    complete_plan,
    delete_plan,
    get_saved_plan,
    remove_exercises_from_plan,
    reorder_plan_exercises,
)
from app.strength_model import (
    bootstrap_curve_bands,
    check_burnout_availability,
    check_heavy_availability,
    curve_snapshot_for_date,
    fatigue_profile,
    get_exercise_freshness,
    prescribe_next_set,
)

router = APIRouter(prefix="/api/planner", tags=["planner"])


# ── Active-plan CRUD ──────────────────────────────────────────────────


@router.get("/active")
def get_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or user_today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return plan


@router.delete("/active", status_code=204)
def delete_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or user_today()
    try:
        delete_plan(session, plan_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/active/complete", status_code=200)
def complete_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Mark the latest plan for ``as_of`` as completed. Exercises the user
    skipped without logging sets are accepted as intentionally skipped —
    completion does not require every exercise to have logged sets.
    """
    plan_date = as_of or user_today()
    try:
        return complete_plan(session, plan_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddExercisesRequest(BaseModel):
    exercises: list[dict]


@router.post("/active/exercises", status_code=200)
def add_exercises(
    data: AddExercisesRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or user_today()
    try:
        return add_exercises_to_plan(session, plan_date, data.exercises)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/active/exercises/{exercise_id}", status_code=200)
def remove_exercise(
    exercise_id: int,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or user_today()
    try:
        return remove_exercises_from_plan(session, plan_date, [exercise_id])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ReorderRequest(BaseModel):
    pde_ids: list[int]


@router.patch("/active/reorder", status_code=200)
def reorder_exercises(
    data: ReorderRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or user_today()
    try:
        return reorder_plan_exercises(session, plan_date, data.pde_ids)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Exercise menus (freshness-sorted) ─────────────────────────────────


@router.get("/exercise-menu")
def exercise_menu(
    workout_session_id: int | None = Query(
        None, description="Current workout session ID for heavy availability check"
    ),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    items = get_exercise_freshness(session)
    for item in items:
        if item.get("allow_heavy_loading"):
            avail = check_heavy_availability(
                item["exercise_id"], session, workout_session_id
            )
            item["heavy_available"] = avail["available"]
            item["heavy_blocked_reason"] = avail["reason"]
        else:
            item["heavy_available"] = False
            item["heavy_blocked_reason"] = None
        burnout = check_burnout_availability(item["exercise_id"], session)
        item["burnout_available"] = burnout["available"]
        item["burnout_blocked_reason"] = burnout["reason"]
    return items


@router.get("/weekly-menu")
def weekly_menu(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Return exercises grouped by training group with freshness-based availability."""
    return get_group_exercise_menu(session)


# ── Strength-curve sequential prescription ────────────────────────────


class PrescribeNextRequest(BaseModel):
    exercise_id: int
    prior_sets: list[dict] = []
    actual_weight: float | None = None
    training_mode: Literal["heavy", "volume", "burnout"] = "volume"


@router.post("/prescribe-next")
def prescribe_next(
    data: PrescribeNextRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    bw_lookup = _get_bw_lookup(session)
    bw_lb = latest_bodyweight(bw_lookup, user_today())

    return prescribe_next_set(
        exercise_id=data.exercise_id,
        session=session,
        prior_sets=data.prior_sets,
        bodyweight_lb=bw_lb,
        actual_weight=data.actual_weight,
        training_mode=data.training_mode,
    )


@router.get("/curve-snapshot/{exercise_id}")
def curve_snapshot(
    exercise_id: int,
    date: datetime.date = Query(..., description="Date to snapshot the curve for"),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Historical curve fit for the completed-exercise view (Recent Sessions).

    Lazy-cached via ``chart_cache``: invalidated by add/update/delete of
    workout sets within the 30-day fitting window of ``date``, plus
    bodyweight log changes and exercise-model edits.
    """
    key = curve_key(exercise_id, date)
    cached = cache_get(session, key)
    if cached is not None:
        return cached
    from app.telemetry import phase
    with phase("curve_snapshot.compute"):
        bw_lookup = _get_bw_lookup(session)
        bw_lb = latest_bodyweight(bw_lookup, date)
        payload = curve_snapshot_for_date(
            exercise_id, session, date, bodyweight_lb=bw_lb,
        )
    with phase("curve_snapshot.cache_set"):
        cache_set(
            session, key, KIND_CURVE, payload,
            exercise_id=exercise_id, on_date=date,
        )
    return payload


@router.get("/fatigue-profile/{exercise_id}")
def get_fatigue_profile(
    exercise_id: int,
    days: int = Query(default=30, ge=7, le=180),
    session_date: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Per-set fatigue decomposition for the reps-by-set-index companion chart.

    Returns `r_fresh(W_s) + β_s` where β_s is the learned per-(exercise, set_index)
    residual over the trailing window, with a global fallback when data is sparse.
    Pass ``session_date`` to anchor ``session_observations`` to a specific
    historical session (otherwise the most recent session in the window).
    """
    key = fatigue_key(exercise_id, days, session_date)
    cached = cache_get(session, key)
    if cached is not None:
        return cached
    payload = fatigue_profile(
        exercise_id, session, days=days, session_date=session_date,
    )
    cache_set(
        session, key, KIND_FATIGUE, payload,
        exercise_id=exercise_id, on_date=session_date,
    )
    return payload


# Single-flight lock map for bootstrap band computation. Bootstrap fits
# are expensive (~600 ms cold) so we coalesce concurrent requests for the
# same (db_url, exercise, date) tuple. Each request acquires the lock
# only after the cache-miss check; warm reads stay lock-free. Entries
# are never GCed: they're cheap (~one mutex each) and the keyspace is
# bounded by users × exercises × distinct anchor dates.
_BANDS_LOCKS: dict[tuple[str, int, str], threading.Lock] = {}
_BANDS_LOCKS_MUTEX = threading.Lock()


def _bands_lock(
    db_url: str, exercise_id: int, on_date: datetime.date,
) -> threading.Lock:
    key = (db_url, exercise_id, on_date.isoformat())
    with _BANDS_LOCKS_MUTEX:
        lock = _BANDS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _BANDS_LOCKS[key] = lock
        return lock


@router.get("/curve-bands/{exercise_id}")
def get_curve_bands(
    exercise_id: int,
    date: datetime.date = Query(..., description="Anchor date for the bootstrap fit window"),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Bootstrap CI bands for the historical curve fit at ``date``.

    Same data window as ``curve-snapshot`` but with session-level
    resampling to produce p05/p25/p50/p75/p95 envelopes evaluated on an
    entered-space W grid. The point fit (``curve-snapshot``) excludes
    today's sets; the band uses the same ``exclude_today`` semantics so
    they're directly comparable.

    Returns ``{"has_bands": false}`` when the exercise has fewer than 3
    distinct training sessions in the fit window, when the bootstrap
    success rate is below ``MIN_BOOT_SUCCESS_FRAC``, or for
    bodyweight-only exercises where the entered-weight axis collapses.

    Cache-backed via ``chart_cache`` (kind=``curve_bands``); single-flight
    via a process-level lock so concurrent cold requests for the same
    (exercise, date) coalesce.
    """
    from app.telemetry import phase
    key = bands_key(exercise_id, date)
    cached = cache_get(session, key)
    if cached is not None:
        return cached
    db_url = str(session.bind.url) if session.bind is not None else ""
    lock = _bands_lock(db_url, exercise_id, date)
    with lock:
        # Re-check after acquiring lock: the previous holder may have
        # populated the cache for this key.
        cached = cache_get(session, key)
        if cached is not None:
            return cached
        with phase("curve_bands.compute"):
            payload = bootstrap_curve_bands(
                exercise_id, session, as_of=date,
            )
        if payload is None:
            response = {"has_bands": False}
        else:
            response = {"has_bands": True, **payload}
        with phase("curve_bands.cache_set"):
            cache_set(
                session, key, KIND_BANDS, response,
                exercise_id=exercise_id, on_date=date,
            )
        return response


def _get_bw_lookup(session: Session) -> dict:
    weights = session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all()
    return bodyweight_by_date(weights)


# ── Quick-start: skip plan/save, go directly to workout ───────────────


class QuickStartRequest(BaseModel):
    exercise_ids: list[int]
    date: datetime.date | None = None


@router.post("/quick-start", status_code=201)
def quick_start(
    data: QuickStartRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Create a workout session immediately with the selected exercises."""
    plan_date = data.date or user_today()

    program = session.exec(
        select(TrainingProgram).where(TrainingProgram.name == "__auto_plan__")
    ).first()
    if not program:
        program = TrainingProgram(
            name="__auto_plan__", notes="Auto-generated daily plans"
        )
        session.add(program)
        session.commit()
        session.refresh(program)

    existing = session.exec(
        select(PlannedSession).where(PlannedSession.date == plan_date)
    ).all()
    for ps in existing:
        old_day = session.get(ProgramDay, ps.program_day_id)
        if old_day and old_day.program_id == program.id:
            old_exercises = session.exec(
                select(ProgramDayExercise).where(
                    ProgramDayExercise.program_day_id == old_day.id
                )
            ).all()
            for oe in old_exercises:
                session.delete(oe)
            session.delete(old_day)
            session.delete(ps)
    session.commit()

    exercise_names = []
    for eid in data.exercise_ids:
        ex = session.get(Exercise, eid)
        if ex:
            exercise_names.append(ex.name)

    day = ProgramDay(
        program_id=program.id,
        day_label="Quick Start",
        target_regions=json.dumps([]),
        sort_order=0,
    )
    session.add(day)
    session.commit()
    session.refresh(day)

    for i, eid in enumerate(data.exercise_ids):
        pde = ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=eid,
            target_sets=3,
            sort_order=i,
            notes=json.dumps({"workflow_role": "quick_start"}),
        )
        session.add(pde)

    ws = WorkoutSession(
        date=plan_date,
        started_at=datetime.datetime.now(datetime.UTC),
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)

    planned = PlannedSession(
        program_day_id=day.id,
        date=plan_date,
        status="in_progress",
        workout_session_id=ws.id,
    )
    session.add(planned)
    session.commit()

    return {
        "workout_session_id": ws.id,
        "exercise_ids": data.exercise_ids,
        "exercise_names": exercise_names,
        "date": plan_date.isoformat(),
    }
