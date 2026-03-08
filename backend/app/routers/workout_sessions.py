import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import Exercise, WorkoutSession, WorkoutSet

router = APIRouter(prefix="/api/workout-sessions", tags=["workout-sessions"])


class SetInput(BaseModel):
    exercise_id: int
    set_order: int
    reps: int | None = None
    weight: float | None = None
    duration_secs: int | None = None
    distance_steps: int | None = None
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
    set_details = []
    for s in sets:
        exercise = session.get(Exercise, s.exercise_id)
        set_details.append({
            "id": s.id,
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
            reps=s.reps,
            weight=s.weight,
            duration_secs=s.duration_secs,
            distance_steps=s.distance_steps,
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
            session.add(WorkoutSet(
                session_id=ws.id,
                exercise_id=s.exercise_id,
                set_order=s.set_order,
                reps=s.reps,
                weight=s.weight,
                duration_secs=s.duration_secs,
                distance_steps=s.distance_steps,
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
