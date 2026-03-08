from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    Exercise,
    RoutineExercise,
    WorkoutSession,
    WorkoutSet,
)

router = APIRouter(prefix="/api/routine", tags=["routine"])


class RoutineExerciseCreate(BaseModel):
    exercise_id: int
    target_sets: int
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    sort_order: int = 0
    active: int = 1
    notes: str | None = None


class RoutineExerciseUpdate(BaseModel):
    target_sets: int | None = None
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    sort_order: int | None = None
    active: int | None = None
    notes: str | None = None


def _build_routine_response(re: RoutineExercise, session: Session) -> dict:
    exercise = session.get(Exercise, re.exercise_id)

    # Get last performance for this exercise
    last_perf = None
    if exercise:
        stmt = (
            select(WorkoutSet, WorkoutSession.date)
            .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
            .where(WorkoutSet.exercise_id == exercise.id)
            .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
        )
        sets = session.exec(stmt).all()
        if sets:
            last_date = sets[0][1]
            last_sets = [s for s, d in sets if d == last_date]
            last_perf = {
                "date": str(last_date),
                "sets": [
                    {"reps": s.reps, "weight": s.weight, "rep_completion": s.rep_completion}
                    for s in last_sets
                ],
            }

    return {
        "id": re.id,
        "exercise_id": re.exercise_id,
        "exercise_name": exercise.name if exercise else "unknown",
        "equipment": exercise.equipment if exercise else None,
        "target_sets": re.target_sets,
        "target_rep_min": re.target_rep_min,
        "target_rep_max": re.target_rep_max,
        "sort_order": re.sort_order,
        "active": re.active,
        "notes": re.notes,
        "last_performance": last_perf,
    }


@router.get("")
def list_routine(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    routine = session.exec(
        select(RoutineExercise).order_by(RoutineExercise.sort_order)
    ).all()
    return [_build_routine_response(re, session) for re in routine]


@router.post("", status_code=201)
def add_routine_exercise(
    data: RoutineExerciseCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, data.exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    # Check for duplicate
    existing = session.exec(
        select(RoutineExercise).where(RoutineExercise.exercise_id == data.exercise_id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Exercise already in routine")
    re = RoutineExercise(**data.model_dump())
    session.add(re)
    session.commit()
    session.refresh(re)
    return _build_routine_response(re, session)


@router.put("/{routine_id}")
def update_routine_exercise(
    routine_id: int,
    data: RoutineExerciseUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    re = session.get(RoutineExercise, routine_id)
    if not re:
        raise HTTPException(status_code=404, detail="Routine exercise not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(re, key, value)
    session.add(re)
    session.commit()
    session.refresh(re)
    return _build_routine_response(re, session)


@router.delete("/{routine_id}", status_code=204)
def delete_routine_exercise(
    routine_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    re = session.get(RoutineExercise, routine_id)
    if not re:
        raise HTTPException(status_code=404, detail="Routine exercise not found")
    session.delete(re)
    session.commit()
