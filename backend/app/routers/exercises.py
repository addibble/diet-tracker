from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    WorkoutSession,
    WorkoutSet,
)
from app.workout_queries import get_current_exercise_tissues

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


class TissueMappingInput(BaseModel):
    tissue_id: int
    role: str = "primary"
    loading_factor: float = 1.0


class ExerciseCreate(BaseModel):
    name: str
    equipment: str | None = None
    notes: str | None = None
    tissues: list[TissueMappingInput] = []


class ExerciseUpdate(BaseModel):
    name: str | None = None
    equipment: str | None = None
    notes: str | None = None
    tissues: list[TissueMappingInput] | None = None


def _build_exercise_response(exercise: Exercise, session: Session) -> dict:
    mappings = get_current_exercise_tissues(session, exercise.id)  # type: ignore[arg-type]
    tissues = []
    for m in mappings:
        tissue = session.get(Tissue, m.tissue_id)
        if not tissue:
            continue
        tissues.append({
            "exercise_tissue_id": m.id,
            "tissue_id": m.tissue_id,
            "tissue_name": tissue.name,
            "tissue_display_name": tissue.display_name,
            "tissue_type": tissue.type,
            "role": m.role,
            "loading_factor": m.loading_factor,
        })
    return {
        "id": exercise.id,
        "name": exercise.name,
        "equipment": exercise.equipment,
        "notes": exercise.notes,
        "created_at": exercise.created_at,
        "tissues": tissues,
    }


@router.get("")
def list_exercises(
    search: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(Exercise)
    if search:
        stmt = stmt.where(Exercise.name.contains(search))  # type: ignore[union-attr]
    stmt = stmt.order_by(Exercise.name)
    exercises = session.exec(stmt).all()
    return [_build_exercise_response(e, session) for e in exercises]


@router.get("/{exercise_id}")
def get_exercise(
    exercise_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return _build_exercise_response(exercise, session)


@router.post("", status_code=201)
def create_exercise(
    data: ExerciseCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    existing = session.exec(select(Exercise).where(Exercise.name == data.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Exercise already exists")
    exercise = Exercise(name=data.name, equipment=data.equipment, notes=data.notes)
    session.add(exercise)
    session.commit()
    session.refresh(exercise)
    for t in data.tissues:
        session.add(ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=t.tissue_id,
            role=t.role,
            loading_factor=t.loading_factor,
        ))
    session.commit()
    return _build_exercise_response(exercise, session)


@router.put("/{exercise_id}")
def update_exercise(
    exercise_id: int,
    data: ExerciseUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    if data.name is not None:
        exercise.name = data.name
    if data.equipment is not None:
        exercise.equipment = data.equipment
    if data.notes is not None:
        exercise.notes = data.notes
    session.add(exercise)
    session.commit()
    if data.tissues is not None:
        for t in data.tissues:
            session.add(ExerciseTissue(
                exercise_id=exercise.id,
                tissue_id=t.tissue_id,
                role=t.role,
                loading_factor=t.loading_factor,
            ))
        session.commit()
    return _build_exercise_response(exercise, session)


@router.delete("/{exercise_id}", status_code=204)
def delete_exercise(
    exercise_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    # Delete related sets, exercise_tissues, routine_exercises
    for s in session.exec(select(WorkoutSet).where(WorkoutSet.exercise_id == exercise_id)).all():
        session.delete(s)
    ex_tissues = session.exec(
        select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
    ).all()
    for et in ex_tissues:
        session.delete(et)
    session.delete(exercise)
    session.commit()


@router.get("/{exercise_id}/history")
def get_exercise_history(
    exercise_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    # Get all sets for this exercise, joined with session date
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    results = session.exec(stmt).all()

    # Group by session date
    sessions_map: dict[date, list] = {}
    for ws, d in results:
        sessions_map.setdefault(d, []).append(ws)

    sessions_out = []
    for d in sorted(sessions_map.keys(), reverse=True)[:limit]:
        sets = sessions_map[d]
        max_weight = max((s.weight or 0) for s in sets)
        total_volume = sum((s.reps or 0) * (s.weight or 0) for s in sets)
        completions = [s.rep_completion for s in sets if s.rep_completion]
        sessions_out.append({
            "date": str(d),
            "sets": [
                {
                    "set_order": s.set_order,
                    "reps": s.reps,
                    "weight": s.weight,
                    "duration_secs": s.duration_secs,
                    "rpe": s.rpe,
                    "rep_completion": s.rep_completion,
                    "notes": s.notes,
                }
                for s in sets
            ],
            "max_weight": max_weight,
            "total_volume": total_volume,
            "rep_completions": completions,
        })

    return {
        "exercise": _build_exercise_response(exercise, session),
        "sessions": sessions_out,
    }
