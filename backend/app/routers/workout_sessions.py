import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
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
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)

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
    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == ws.id)
        .order_by(WorkoutSet.set_order)
    ).all()
    scheme_history_by_exercise = get_exercise_scheme_history_map(
        session,
        {workout_set.exercise_id for workout_set in sets},
        limit=40,
        exclude_session_ids=[ws.id],
    )
    exercise_ids = {s.exercise_id for s in sets}
    exercises_by_id: dict[int, Exercise] = {}
    if exercise_ids:
        exercises_by_id = {
            e.id: e
            for e in session.exec(
                select(Exercise).where(col(Exercise.id).in_(exercise_ids))
            ).all()
            if e.id is not None
        }
    weight_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
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
        set_details.append({
            "id": s.id,
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
            "scheme_history": scheme_history_by_exercise.get(
                s.exercise_id,
                empty_scheme_history(),
            ),
        })
    return {
        "id": ws.id,
        "date": str(ws.date),
        "started_at": ws.started_at,
        "finished_at": ws.finished_at,
        "notes": ws.notes,
        "created_at": ws.created_at,
        "sets": set_details,
        "effective_volume": effective_volume,
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
        session.add(WorkoutSet(
            session_id=ws.id,
            exercise_id=s.exercise_id,
            set_order=s.set_order,
            performed_side=default_performed_side(
                exercise_name=exercise.name,
                exercise_laterality=exercise.laterality,
                provided_side=s.performed_side,
            ),
            reps=s.reps,
            weight=s.weight,
            duration_secs=s.duration_secs,
            distance_steps=s.distance_steps,
            started_at=s.started_at,
            completed_at=s.completed_at,
            rpe=s.rpe,
            rep_completion=s.rep_completion,
            notes=s.notes,
        ))
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
            session.add(WorkoutSet(
                session_id=ws.id,
                exercise_id=s.exercise_id,
                set_order=s.set_order,
                performed_side=default_performed_side(
                    exercise_name=exercise.name,
                    exercise_laterality=exercise.laterality,
                    provided_side=s.performed_side,
                ),
                reps=s.reps,
                weight=s.weight,
                duration_secs=s.duration_secs,
                distance_steps=s.distance_steps,
                started_at=s.started_at,
                completed_at=s.completed_at,
                rpe=s.rpe,
                rep_completion=s.rep_completion,
                notes=s.notes,
            ))
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
    for s in session.exec(select(WorkoutSet).where(WorkoutSet.session_id == ws.id)).all():
        session.delete(s)
    session.delete(ws)
    session.commit()
