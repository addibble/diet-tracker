import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    Exercise,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
    WorkoutSetTissueFeedback,
)
from app.tracked_tissues import default_performed_side, get_active_rehab_plans_by_tracked_tissue

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
    feedback_rows = session.exec(select(WorkoutSetTissueFeedback)).all()
    feedback_by_set: dict[int, list[WorkoutSetTissueFeedback]] = {}
    for row in feedback_rows:
        feedback_by_set.setdefault(row.workout_set_id, []).append(row)
    tracked_rows = {
        row.id: row
        for row in session.exec(select(TrackedTissue)).all()
    }
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    set_details = []
    for s in sets:
        exercise = session.get(Exercise, s.exercise_id)
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
                for row in feedback_by_set.get(s.id or 0, [])
            ],
        })
    return {
        "id": ws.id,
        "date": str(ws.date),
        "started_at": ws.started_at,
        "finished_at": ws.finished_at,
        "notes": ws.notes,
        "created_at": ws.created_at,
        "sets": set_details,
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
