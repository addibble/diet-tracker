import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.chart_cache import (
    KIND_BETA_EVOL,
    beta_evol_key,
    cache_get,
    cache_set,
    invalidate_for_session_delete,
    invalidate_for_set_change,
)
from app.database import get_session
from app.exercise_history import empty_scheme_history, get_exercise_scheme_history_map
from app.exercise_laterality import default_performed_side
from app.exercise_loads import (
    bodyweight_by_date,
    effective_set_load,
    effective_weight,
)
from app.models import (
    Exercise,
    PlannedSession,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.session_readiness import (
    fit_session_beta,
    is_beta_clamped,
    readiness_label,
    readiness_pct,
    session_per_set_betas,
)
from app.telemetry import phase
from app.units import endurance_value_from_legacy, legacy_metric_fields

router = APIRouter(prefix="/api/workout-sessions", tags=["workout-sessions"])


class SetInput(BaseModel):
    exercise_id: int
    set_order: int
    performed_side: Literal["left", "right", "center", "bilateral"] | None = None
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    rpe: float | None = None
    rep_completion: str | None = None
    notes: str | None = None


class SessionCreate(BaseModel):
    date: datetime.date
    started_at: str | None = None
    finished_at: str | None = None
    notes: str | None = None
    sets: list[SetInput] = []


class SessionUpdate(BaseModel):
    date: datetime.date | None = None
    notes: str | None = None
    add_sets: list[SetInput] | None = None
    remove_set_ids: list[int] | None = None


def _build_session_response(ws: WorkoutSession, session: Session) -> dict:
    sets = session.exec(select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.set_order)).all()
    scheme_history_by_exercise = get_exercise_scheme_history_map(
        session,
        {workout_set.exercise_id for workout_set in sets},
        limit=40,
        exclude_session_ids=[ws.id],
    )
    exercise_ids = {s.exercise_id for s in sets}
    exercises_by_id: dict[int, Exercise] = {}
    if exercise_ids:
        exercises_by_id = {e.id: e for e in session.exec(select(Exercise).where(col(Exercise.id).in_(exercise_ids))).all() if e.id is not None}
    weight_lookup = bodyweight_by_date(list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all()))
    # Per-session "effective_volume" sums each set's effective_set_load directly.
    # This matches the dashboard volume-by-region normalization (each set
    # contributes a fixed work budget across regions summing to 1.0), so the
    # session total isn't inflated by the per-tissue fan-out that previously
    # multiplied each set by Σ routing_factor across mapped tissues.
    effective_volume = 0.0
    set_details = []
    for s in sets:
        exercise = exercises_by_id.get(s.exercise_id) or session.get(Exercise, s.exercise_id)
        if exercise is not None:
            set_w = effective_weight(exercise, s, weight_lookup, ws.date)
            load = effective_set_load(exercise, s, set_w)
            effective_volume += load
        set_details.append(
            {
                "id": s.id,
                "exercise_id": s.exercise_id,
                "exercise_name": exercise.name if exercise else "unknown",
                "set_metric_mode": (exercise.set_metric_mode if exercise else None) or "reps",
                "set_order": s.set_order,
                "performed_side": s.performed_side,
                **legacy_metric_fields(exercise, s.endurance_value),
                "weight": s.weight,
                "endurance_value": s.endurance_value,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "rpe": s.rpe,
                "rep_completion": s.rep_completion,
                "notes": s.notes,
                "scheme_history": scheme_history_by_exercise.get(
                    s.exercise_id,
                    empty_scheme_history(),
                ),
            }
        )
    return {
        "id": ws.id,
        "date": str(ws.date),
        "started_at": ws.started_at,
        "finished_at": ws.finished_at,
        "notes": ws.notes,
        "created_at": ws.created_at,
        "sets": set_details,
        "effective_volume": effective_volume,
        # v4 readiness β: live-refit per-session multiplicative factor.
        # NULL until at least MIN_SETS_FOR_BETA RPE-eligible sets exist.
        "readiness_beta": ws.readiness_beta,
        "readiness_label": readiness_label(ws.readiness_beta),
        "readiness_pct": readiness_pct(ws.readiness_beta),
        "readiness_clamped": is_beta_clamped(ws.readiness_beta),
    }


@router.get("")
def list_sessions(
    start_date: datetime.date | None = Query(default=None),
    end_date: datetime.date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(WorkoutSession)
    if start_date:
        stmt = stmt.where(WorkoutSession.date >= start_date)
    if end_date:
        stmt = stmt.where(WorkoutSession.date <= end_date)
    stmt = stmt.order_by(col(WorkoutSession.date).desc()).limit(limit)
    sessions = session.exec(stmt).all()
    return [_build_session_response(ws, session) for ws in sessions]


@router.get("/readiness/trend")
def readiness_trend(
    days: int = Query(default=14, ge=1, le=90),
    exercise_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Recent per-session readiness β values for the trend sparkline.

    With ``exercise_id``, β at each session is recomputed using only that
    exercise's RPE-eligible sets, so the sparkline tracks the user's
    per-exercise trend rather than the overall session readiness.
    """
    from app.config import user_today

    end = user_today()
    start = end - datetime.timedelta(days=days - 1)
    stmt = select(WorkoutSession).where(WorkoutSession.date >= start).where(WorkoutSession.date <= end).order_by(col(WorkoutSession.date).asc())
    sessions = session.exec(stmt).all()

    # When filtering by exercise, drop sessions that didn't include it; this
    # keeps the sparkline's x-axis tight around days the user actually
    # trained that exercise instead of showing many null gaps.
    if exercise_id is not None and sessions:
        ids = [ws.id for ws in sessions]
        rows = session.exec(select(WorkoutSet.session_id).where(WorkoutSet.session_id.in_(ids)).where(WorkoutSet.exercise_id == exercise_id).distinct()).all()
        present = {row for row in rows}
        sessions = [ws for ws in sessions if ws.id in present]

    points = []
    for ws in sessions:
        if exercise_id is None:
            beta = ws.readiness_beta
        else:
            beta = fit_session_beta(session, ws.id, exercise_id=exercise_id)
        points.append(
            {
                "date": ws.date.isoformat(),
                "session_id": ws.id,
                "readiness_beta": beta,
                "readiness_label": readiness_label(beta),
                "readiness_pct": readiness_pct(beta),
                "readiness_clamped": is_beta_clamped(beta),
            }
        )
    return {
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "exercise_id": exercise_id,
        "points": points,
    }


@router.get("/{session_id}")
def get_session_detail(
    session_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = session.get(WorkoutSession, session_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Session not found")
    return _build_session_response(ws, session)


@router.get("/{session_id}/beta-evolution")
def get_beta_evolution(
    session_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Per-set readiness β grouped by exercise group for one workout session.

    Returns one β per RPE-eligible rep-mode set logged today, bucketed by
    the set's exercise group (Push / Pull / Legs / Shoulders / Core /
    Uncategorized). Each point's β is the single-set residual
    ``log(rtf_obs / r_fresh(W_eff))`` against the exercise's prior fresh
    curve — so the chart shows, per training group, how each individual
    set landed relative to history.

    Lazy-cached via ``chart_cache``: invalidated by add/update/delete of
    workout sets in this session, plus same-exercise sets in any session
    within the prior 30 days, plus bodyweight log and exercise-model
    changes.
    """
    key = beta_evol_key(session_id)
    with phase("beta_evol.cache_get"):
        cached = cache_get(session, key)
    if cached is not None:
        return cached
    with phase("beta_evol.compute"):
        payload = session_per_set_betas(session, session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Session not found")
    groups_out = []
    for group in payload["groups"]:
        points_out = []
        for p in group["points"]:
            beta = p["beta"]
            points_out.append(
                {
                    "exercise_id": p["exercise_id"],
                    "exercise_name": p["exercise_name"],
                    "set_id": p["set_id"],
                    "set_index": p["set_index"],
                    "set_order": p["set_order"],
                    "weight": p["weight"],
                    "reps_done": p["reps_done"],
                    "rtf": p["rtf"],
                    "beta": beta,
                    "readiness_label": readiness_label(beta),
                    "readiness_pct": readiness_pct(beta),
                    "readiness_clamped": is_beta_clamped(beta),
                }
            )
        groups_out.append({"group": group["group"], "points": points_out})
    response = {"session_id": session_id, "groups": groups_out}
    cache_set(
        session,
        key,
        KIND_BETA_EVOL,
        response,
        workout_session_id=session_id,
    )
    return response


@router.post("", status_code=201)
def create_session(
    data: SessionCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = WorkoutSession(
        date=data.date,
        notes=data.notes,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    for s in data.sets:
        exercise = session.get(Exercise, s.exercise_id)
        if not exercise:
            raise HTTPException(status_code=400, detail=f"Exercise {s.exercise_id} not found")
        session.add(
            WorkoutSet(
                session_id=ws.id,
                exercise_id=s.exercise_id,
                set_order=s.set_order,
                performed_side=default_performed_side(
                    exercise_name=exercise.name,
                    exercise_laterality=exercise.laterality,
                    provided_side=s.performed_side,
                ),
                weight=s.weight,
                endurance_value=endurance_value_from_legacy(
                    exercise,
                    reps=s.reps,
                    duration_secs=s.duration_secs,
                    distance_steps=s.distance_steps,
                ),
                started_at=s.started_at,
                completed_at=s.completed_at,
                rpe=s.rpe,
                rep_completion=s.rep_completion,
                notes=s.notes,
            )
        )
    session.commit()
    return _build_session_response(ws, session)


@router.put("/{session_id}")
def update_session(
    session_id: int,
    data: SessionUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = session.get(WorkoutSession, session_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Session not found")
    # Capture state needed to invalidate the chart cache after we mutate
    # the session: the *old* date plus every exercise the session ever
    # touched (existing + adds + removes). A session-date change also
    # affects curve windows on both old and new dates.
    old_date = ws.date
    affected_ex_ids: set[int] = set(session.exec(select(WorkoutSet.exercise_id).where(WorkoutSet.session_id == ws.id)).all())
    if data.add_sets:
        affected_ex_ids.update(s.exercise_id for s in data.add_sets)
    if data.date is not None:
        ws.date = data.date
    if data.notes is not None:
        ws.notes = data.notes
    session.add(ws)
    if data.remove_set_ids:
        for set_id in data.remove_set_ids:
            s = session.get(WorkoutSet, set_id)
            if s and s.session_id == ws.id:
                session.delete(s)
    if data.add_sets:
        for s in data.add_sets:
            exercise = session.get(Exercise, s.exercise_id)
            if not exercise:
                raise HTTPException(status_code=400, detail=f"Exercise {s.exercise_id} not found")
            session.add(
                WorkoutSet(
                    session_id=ws.id,
                    exercise_id=s.exercise_id,
                    set_order=s.set_order,
                    performed_side=default_performed_side(
                        exercise_name=exercise.name,
                        exercise_laterality=exercise.laterality,
                        provided_side=s.performed_side,
                    ),
                    weight=s.weight,
                    endurance_value=endurance_value_from_legacy(
                        exercise,
                        reps=s.reps,
                        duration_secs=s.duration_secs,
                        distance_steps=s.distance_steps,
                    ),
                    started_at=s.started_at,
                    completed_at=s.completed_at,
                    rpe=s.rpe,
                    rep_completion=s.rep_completion,
                    notes=s.notes,
                )
            )
    new_date = ws.date
    for ex_id in affected_ex_ids:
        invalidate_for_set_change(session, ws.id, ex_id, old_date)
        if new_date != old_date:
            invalidate_for_set_change(session, ws.id, ex_id, new_date)
    session.commit()
    return _build_session_response(ws, session)


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = session.get(WorkoutSession, session_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Session not found")
    # Capture exercise IDs so the cache invalidator can drop affected
    # curve windows for every exercise this session touched.
    set_ex_ids = list(session.exec(select(WorkoutSet.exercise_id).where(WorkoutSet.session_id == ws.id)).all())
    invalidate_for_session_delete(session, ws.id, ws.date, set_ex_ids)
    for s in session.exec(select(WorkoutSet).where(WorkoutSet.session_id == ws.id)).all():
        session.delete(s)
    # Clear any PlannedSession rows that point at this WorkoutSession so the
    # plan doesn't keep advertising an "in_progress" workout against a
    # workout_session_id that no longer exists. Without this the next
    # /api/workout-sessions/{stale_id}/sets POST 404s and the user is stuck.
    referencing = session.exec(select(PlannedSession).where(PlannedSession.workout_session_id == ws.id)).all()
    for planned in referencing:
        planned.workout_session_id = None
        if planned.status == "in_progress":
            planned.status = "planned"
        session.add(planned)
    session.delete(ws)
    session.commit()
