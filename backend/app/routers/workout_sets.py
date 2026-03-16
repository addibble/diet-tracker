import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.auth import get_current_user
from app.database import get_session
from app.models import Exercise, ProgramDayExercise, WorkoutSession, WorkoutSet

router = APIRouter(tags=["workout-sets"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SetUpdate(BaseModel):
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
    rpe: float | None = None
    rep_completion: str | None = None
    notes: str | None = None


class SetCreate(BaseModel):
    exercise_id: int
    set_order: int | None = None
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
    rpe: float | None = None
    rep_completion: str | None = None
    notes: str | None = None


class ProgramDayExerciseUpdate(BaseModel):
    target_sets: int | None = None
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    target_weight: float | None = None
    rep_scheme: str | None = None
    sort_order: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_response(s: WorkoutSet, session: Session) -> dict:
    exercise = session.get(Exercise, s.exercise_id)
    return {
        "id": s.id,
        "session_id": s.session_id,
        "exercise_id": s.exercise_id,
        "exercise_name": exercise.name if exercise else "unknown",
        "set_order": s.set_order,
        "reps": s.reps,
        "weight": s.weight,
        "duration_secs": s.duration_secs,
        "distance_steps": s.distance_steps,
        "rpe": s.rpe,
        "rep_completion": s.rep_completion,
        "notes": s.notes,
    }


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
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(ws, key, value)
    session.add(ws)
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

    new_set = WorkoutSet(
        session_id=session_id,
        exercise_id=data.exercise_id,
        set_order=set_order,
        reps=data.reps,
        weight=data.weight,
        duration_secs=data.duration_secs,
        distance_steps=data.distance_steps,
        rpe=data.rpe,
        rep_completion=data.rep_completion,
        notes=data.notes,
    )
    session.add(new_set)
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

    # target_weight and rep_scheme live in the notes JSON blob
    meta_keys = {"target_weight", "rep_scheme"}
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
        "sort_order": pde.sort_order,
    }
