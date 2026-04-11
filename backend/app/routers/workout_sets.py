from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.auth import get_current_user
from app.database import get_session
from app.exercise_history import REP_SCHEME_VERSION, empty_scheme_history, get_exercise_scheme_history_map
from app.models import (
    Exercise,
    ProgramDayExercise,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
    WorkoutSetTissueFeedback,
)
from app.tracked_tissues import default_performed_side, get_active_rehab_plans_by_tracked_tissue

router = APIRouter(tags=["workout-sets"])


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
    tissue_feedback: list[SetTissueFeedbackInput] | None = None


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
    tissue_feedback: list[SetTissueFeedbackInput] | None = None


class SetTissueFeedbackInput(BaseModel):
    tracked_tissue_id: int
    pain_0_10: int = 0
    symptom_note: str | None = None


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
    feedback_rows = session.exec(
        select(WorkoutSetTissueFeedback).where(WorkoutSetTissueFeedback.workout_set_id == s.id)
    ).all()
    tracked_rows = {
        row.id: row
        for row in session.exec(select(TrackedTissue)).all()
    }
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    return {
        "id": s.id,
        "session_id": s.session_id,
        "exercise_id": s.exercise_id,
        "exercise_name": exercise.name if exercise else "unknown",
        "set_order": s.set_order,
        "performed_side": s.performed_side,
        "reps": s.reps,
        "weight": s.weight,
        "duration_secs": s.duration_secs,
        "distance_steps": s.distance_steps,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
        "rpe": s.rpe,
        "rep_completion": s.rep_completion,
        "notes": s.notes,
        "scheme_history": scheme_history,
        "tissue_feedback": [
            {
                "id": row.id,
                "tracked_tissue_id": row.tracked_tissue_id,
                "tracked_tissue_display_name": tracked_rows.get(row.tracked_tissue_id).display_name
                if tracked_rows.get(row.tracked_tissue_id)
                else "unknown",
                "pain_0_10": row.pain_0_10,
                "symptom_note": row.symptom_note,
                "recorded_at": row.recorded_at,
                "above_threshold": (
                    active_rehab_plans.get(row.tracked_tissue_id) is not None
                    and row.pain_0_10 > active_rehab_plans[row.tracked_tissue_id].pain_monitoring_threshold
                ),
            }
            for row in feedback_rows
        ],
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


def _replace_tissue_feedback(
    *,
    session: Session,
    workout_set_id: int,
    entries: list[SetTissueFeedbackInput],
) -> None:
    existing = session.exec(
        select(WorkoutSetTissueFeedback).where(WorkoutSetTissueFeedback.workout_set_id == workout_set_id)
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()
    for entry in entries:
        session.add(
            WorkoutSetTissueFeedback(
                workout_set_id=workout_set_id,
                tracked_tissue_id=entry.tracked_tissue_id,
                pain_0_10=entry.pain_0_10,
                symptom_note=entry.symptom_note,
            )
        )


# ---------------------------------------------------------------------------
# Individual workout set endpoints
# ---------------------------------------------------------------------------


@router.patch("/api/workout-sets/{set_id}")
def update_set(
    set_id: int,
    data: SetUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    ws = session.get(WorkoutSet, set_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Set not found")
    exercise = session.get(Exercise, ws.exercise_id)
    tissue_feedback = (
        data.tissue_feedback
        if "tissue_feedback" in data.model_fields_set
        else None
    )
    updates = data.model_dump(exclude_unset=True, exclude={"tissue_feedback"})
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
    for key, value in updates.items():
        setattr(ws, key, value)
    session.add(ws)
    if tissue_feedback is not None:
        _replace_tissue_feedback(session=session, workout_set_id=ws.id, entries=tissue_feedback)
    _normalize_session_set_order(session, ws.session_id)
    session.commit()
    session.refresh(ws)
    return _set_response(ws, session)


@router.post("/api/workout-sessions/{session_id}/sets", status_code=201)
def add_set(
    session_id: int,
    data: SetCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
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
        reps=data.reps,
        weight=data.weight,
        duration_secs=data.duration_secs,
        distance_steps=data.distance_steps,
        started_at=started_at,
        completed_at=completed_at,
        rpe=rpe_val,
        rep_completion=data.rep_completion,
        notes=data.notes,
    )
    session.add(new_set)
    session.flush()
    if data.tissue_feedback:
        _replace_tissue_feedback(session=session, workout_set_id=new_set.id, entries=data.tissue_feedback)
    _normalize_session_set_order(session, session_id)
    session.commit()
    session.refresh(new_set)
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
