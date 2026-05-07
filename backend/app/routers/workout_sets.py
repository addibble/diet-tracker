from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.auth import get_current_user
from app.auth_models import User
from app.chart_cache import invalidate_for_set_change
from app.database import get_session
from app.exercise_history import REP_SCHEME_VERSION, empty_scheme_history, get_exercise_scheme_history_map
from app.exercise_laterality import default_performed_side
from app.models import (
    Exercise,
    ProgramDayExercise,
    WorkoutSession,
    WorkoutSet,
)
from app.session_readiness import update_session_readiness
from app.strength_model import BODYWEIGHT_MODES
from app.units import endurance_value_from_legacy, legacy_metric_fields

router = APIRouter(tags=["workout-sets"])

logger = logging.getLogger(__name__)


def _exercise_can_change_readiness(exercise: Exercise | None) -> bool:
    """Return True when sets on this exercise contribute to β.

    Bodyweight and duration-mode exercises are filtered out of the curve fit
    and therefore add no observations to ``fit_session_beta``. Saving a set
    on those exercises cannot change β, so we skip the (~0.5–1.5 s) refit.
    """
    if exercise is None:
        return False
    if (exercise.set_metric_mode or "reps") == "duration":
        return False
    if (exercise.load_input_mode or "") in BODYWEIGHT_MODES:
        return False
    return True


def _schedule_readiness_refit(
    background: BackgroundTasks, user_id: str, workout_session_id: int,
) -> None:
    """Queue a non-blocking readiness refit on a fresh DB session.

    The refit is computation-heavy (fits one curve per unique exercise in
    the session); running it inline on the request thread serialises the
    next ``prescribe-next`` call behind it. Pushing to a BackgroundTask
    lets the set-save response return immediately.
    """

    def _job() -> None:
        from app.db_engines import user_engine

        try:
            with Session(user_engine(user_id)) as sess:
                update_session_readiness(sess, workout_session_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "background readiness refit failed (user=%s, session=%s)",
                user_id, workout_session_id,
            )

    background.add_task(_job)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SetUpdate(BaseModel):
    performed_side: Literal["left", "right", "center", "bilateral"] | None = None
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rpe: float | None = None
    rir: float | None = None  # converted to rpe = 10 - rir
    rep_completion: str | None = None
    notes: str | None = None
    training_mode: Literal["heavy", "volume", "burnout"] | None = None


class SetCreate(BaseModel):
    exercise_id: int
    set_order: int | None = None
    performed_side: Literal["left", "right", "center", "bilateral"] | None = None
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rpe: float | None = None
    rir: float | None = None  # converted to rpe = 10 - rir
    rep_completion: str | None = None
    notes: str | None = None
    training_mode: Literal["heavy", "volume", "burnout"] | None = None


class ProgramDayExerciseUpdate(BaseModel):
    target_sets: int | None = None
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    target_weight: float | None = None
    rep_scheme: str | None = None
    performed_side: Literal["left", "right", "center", "bilateral"] | None = None
    side_explanation: str | None = None
    sort_order: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rir_to_rpe(updates: dict, rir_field: str = "rir") -> dict:
    """Convert RIR to RPE (rpe = 10 - rir) if rir provided."""
    if rir_field in updates and updates[rir_field] is not None:
        updates["rpe"] = 10.0 - updates.pop(rir_field)
    else:
        updates.pop(rir_field, None)
    return updates


def _set_response(s: WorkoutSet, session: Session) -> dict:
    exercise = session.get(Exercise, s.exercise_id)
    scheme_history = get_exercise_scheme_history_map(
        session,
        [s.exercise_id],
        limit=40,
        exclude_session_ids=[s.session_id] if s.session_id is not None else None,
    ).get(s.exercise_id, empty_scheme_history())
    return {
        "id": s.id,
        "session_id": s.session_id,
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
        "training_mode": s.training_mode,
        "scheme_history": scheme_history,
    }


def _normalize_session_set_order(session: Session, session_id: int) -> None:
    rows = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == session_id)
        .order_by(
            WorkoutSet.completed_at.is_(None),
            WorkoutSet.completed_at,
            WorkoutSet.started_at,
            WorkoutSet.set_order,
            WorkoutSet.id,
        )
    ).all()
    changed = False
    for index, row in enumerate(rows, start=1):
        if row.set_order != index:
            row.set_order = index
            session.add(row)
            changed = True
    if changed:
        session.flush()


# ---------------------------------------------------------------------------
# Individual workout set endpoints
# ---------------------------------------------------------------------------


@router.patch("/api/workout-sets/{set_id}")
def update_set(
    set_id: int,
    data: SetUpdate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    ws = session.get(WorkoutSet, set_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Set not found")
    exercise = session.get(Exercise, ws.exercise_id)
    updates = data.model_dump(exclude_unset=True)
    _rir_to_rpe(updates)
    if "performed_side" in updates and exercise:
        updates["performed_side"] = default_performed_side(
            exercise_name=exercise.name,
            exercise_laterality=exercise.laterality,
            provided_side=updates["performed_side"],
        )
    if "completed_at" not in updates and any(
        updates.get(key) is not None
        for key in ("reps", "weight", "duration_secs", "distance_steps", "rpe", "rep_completion")
    ):
        updates["completed_at"] = datetime.now(UTC)
    if "started_at" not in updates and "completed_at" in updates and updates["completed_at"] is not None:
        updates["started_at"] = ws.started_at or updates["completed_at"]
    # Translate any incoming legacy metric field to ``endurance_value``.
    # The legacy DB columns no longer exist; we accept the keys for wire-compat.
    legacy_keys = {"reps", "duration_secs", "distance_steps"}
    legacy_in = {k: updates.pop(k) for k in list(updates) if k in legacy_keys}
    if exercise and legacy_in:
        ws.endurance_value = endurance_value_from_legacy(
            exercise,
            reps=legacy_in.get("reps"),
            duration_secs=legacy_in.get("duration_secs"),
            distance_steps=legacy_in.get("distance_steps"),
        )
    for key, value in updates.items():
        setattr(ws, key, value)
    session.add(ws)
    _normalize_session_set_order(session, ws.session_id)
    # Invalidate chart cache in the same transaction so a failed commit
    # leaves no stale rows. Curve / β-evolution depend on rpe, weight,
    # and endurance_value (anything else can't change the fit).
    readiness_fields = {"rpe", "endurance_value", "weight"}
    touched_readiness = bool(readiness_fields & set(updates.keys())) or bool(legacy_in)
    parent_session = session.get(WorkoutSession, ws.session_id)
    if touched_readiness and parent_session is not None:
        invalidate_for_set_change(
            session, ws.session_id, ws.exercise_id, parent_session.date,
        )
    session.commit()
    session.refresh(ws)
    # Live readiness re-fit: only when the patch actually touches a field
    # that influences β (rpe, reps/endurance, weight). Edits to notes/side/
    # ordering should not pay the refit cost; nor do duration- or
    # bodyweight-mode sets (they're filtered out of the curve fit).
    if (
        ws.rpe is not None
        and touched_readiness
        and _exercise_can_change_readiness(exercise)
    ):
        _schedule_readiness_refit(background, current_user.id, ws.session_id)
    return _set_response(ws, session)


@router.post("/api/workout-sessions/{session_id}/sets", status_code=201)
def add_set(
    session_id: int,
    data: SetCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    ws = session.get(WorkoutSession, session_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Session not found")
    exercise = session.get(Exercise, data.exercise_id)
    if not exercise:
        raise HTTPException(
            status_code=400, detail=f"Exercise {data.exercise_id} not found"
        )

    # Auto-compute set_order if not provided
    set_order = data.set_order
    if set_order is None:
        max_order = session.exec(
            select(func.max(WorkoutSet.set_order)).where(
                WorkoutSet.session_id == session_id
            )
        ).first()
        set_order = (max_order or 0) + 1

    # Convert RIR → RPE and auto-timestamp
    rpe_val = data.rpe
    if data.rir is not None:
        rpe_val = 10.0 - data.rir
    completed_at = data.completed_at
    if completed_at is None and any([
        data.reps is not None, data.weight is not None,
        data.duration_secs is not None, data.distance_steps is not None,
        rpe_val is not None,
    ]):
        completed_at = datetime.now(UTC)
    started_at = data.started_at or completed_at

    new_set = WorkoutSet(
        session_id=session_id,
        exercise_id=data.exercise_id,
        set_order=set_order,
        performed_side=default_performed_side(
            exercise_name=exercise.name,
            exercise_laterality=exercise.laterality,
            provided_side=data.performed_side,
        ),
        weight=data.weight,
        endurance_value=endurance_value_from_legacy(
            exercise,
            reps=data.reps,
            duration_secs=data.duration_secs,
            distance_steps=data.distance_steps,
        ),
        started_at=started_at,
        completed_at=completed_at,
        rpe=rpe_val,
        rep_completion=data.rep_completion,
        notes=data.notes,
        training_mode=data.training_mode,
    )
    session.add(new_set)
    session.flush()
    _normalize_session_set_order(session, session_id)
    invalidate_for_set_change(session, session_id, data.exercise_id, ws.date)
    session.commit()
    session.refresh(new_set)
    if new_set.rpe is not None and _exercise_can_change_readiness(exercise):
        _schedule_readiness_refit(background, current_user.id, session_id)
    return _set_response(new_set, session)


@router.delete("/api/workout-sets/{set_id}", status_code=204)
def delete_set(
    set_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = session.get(WorkoutSet, set_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Set not found")
    parent = session.get(WorkoutSession, ws.session_id)
    if parent is not None:
        invalidate_for_set_change(
            session, ws.session_id, ws.exercise_id, parent.date,
        )
    session.delete(ws)
    session.commit()


# ---------------------------------------------------------------------------
# ProgramDayExercise target editing
# ---------------------------------------------------------------------------


@router.patch("/api/program-day-exercises/{pde_id}")
def update_program_day_exercise(
    pde_id: int,
    data: ProgramDayExerciseUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    pde = session.get(ProgramDayExercise, pde_id)
    if not pde:
        raise HTTPException(
            status_code=404, detail="Program day exercise not found"
        )

    updates = data.model_dump(exclude_unset=True)

    # target_weight and planner-specific metadata live in the notes JSON blob
    meta_keys = {"target_weight", "rep_scheme", "performed_side", "side_explanation"}
    meta_updates = {k: updates.pop(k) for k in meta_keys if k in updates}

    for key, value in updates.items():
        setattr(pde, key, value)

    if meta_updates:
        meta: dict = {}
        if pde.notes:
            try:
                meta = json.loads(pde.notes)
            except (json.JSONDecodeError, TypeError):
                pass
        meta.update(meta_updates)
        if "rep_scheme" in meta_updates:
            meta["rep_scheme_version"] = REP_SCHEME_VERSION
        pde.notes = json.dumps(meta)

    session.add(pde)
    session.commit()
    session.refresh(pde)

    exercise = session.get(Exercise, pde.exercise_id)
    meta = {}
    if pde.notes:
        try:
            meta = json.loads(pde.notes)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": pde.id,
        "program_day_id": pde.program_day_id,
        "exercise_id": pde.exercise_id,
        "exercise_name": exercise.name if exercise else "unknown",
        "target_sets": pde.target_sets,
        "target_rep_min": pde.target_rep_min,
        "target_rep_max": pde.target_rep_max,
        "rep_scheme": meta.get("rep_scheme"),
        "target_weight": meta.get("target_weight"),
        "performed_side": meta.get("performed_side"),
        "side_explanation": meta.get("side_explanation"),
        "sort_order": pde.sort_order,
    }

