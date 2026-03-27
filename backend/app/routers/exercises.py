from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.exercise_loads import bodyweight_by_date
from app.exercise_loads import effective_weight as calc_effective_weight
from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.workout_queries import get_current_exercise_tissues

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


class TissueMappingInput(BaseModel):
    tissue_id: int
    role: str = "primary"
    loading_factor: float = 1.0
    routing_factor: float | None = None
    fatigue_factor: float | None = None
    joint_strain_factor: float | None = None
    tendon_strain_factor: float | None = None


class ExerciseCreate(BaseModel):
    name: str
    equipment: str | None = None
    load_input_mode: str = "external_weight"
    bodyweight_fraction: float = 0.0
    estimated_minutes_per_set: float = 2.0
    notes: str | None = None
    tissues: list[TissueMappingInput] = []


class ExerciseUpdate(BaseModel):
    name: str | None = None
    equipment: str | None = None
    load_input_mode: str | None = None
    bodyweight_fraction: float | None = None
    estimated_minutes_per_set: float | None = None
    notes: str | None = None
    tissues: list[TissueMappingInput] | None = None


def _build_exercise_response(exercise: Exercise, session: Session) -> dict:
    mappings = get_current_exercise_tissues(session, exercise.id)  # type: ignore[arg-type]
    tissues = []
    for m in mappings:
        tissue = session.get(Tissue, m.tissue_id)
        tissues.append({
            "tissue_id": m.tissue_id,
            "tissue_name": tissue.name if tissue else "unknown",
            "tissue_display_name": tissue.display_name if tissue else "unknown",
            "tissue_type": tissue.type if tissue else "muscle",
            "role": m.role,
            "loading_factor": m.loading_factor,
            "routing_factor": m.routing_factor,
            "fatigue_factor": m.fatigue_factor,
            "joint_strain_factor": m.joint_strain_factor,
            "tendon_strain_factor": m.tendon_strain_factor,
        })
    return {
        "id": exercise.id,
        "name": exercise.name,
        "equipment": exercise.equipment,
        "load_input_mode": exercise.load_input_mode,
        "bodyweight_fraction": exercise.bodyweight_fraction,
        "estimated_minutes_per_set": exercise.estimated_minutes_per_set,
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
    exercise = Exercise(
        name=data.name,
        equipment=data.equipment,
        load_input_mode=data.load_input_mode,
        bodyweight_fraction=data.bodyweight_fraction,
        estimated_minutes_per_set=data.estimated_minutes_per_set,
        notes=data.notes,
    )
    session.add(exercise)
    session.commit()
    session.refresh(exercise)
    for t in data.tissues:
        session.add(ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=t.tissue_id,
            role=t.role,
            loading_factor=t.loading_factor,
            routing_factor=t.routing_factor if t.routing_factor is not None else t.loading_factor,
            fatigue_factor=t.fatigue_factor if t.fatigue_factor is not None else t.loading_factor,
            joint_strain_factor=t.joint_strain_factor if t.joint_strain_factor is not None else t.loading_factor,
            tendon_strain_factor=t.tendon_strain_factor if t.tendon_strain_factor is not None else t.loading_factor,
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
    if data.load_input_mode is not None:
        exercise.load_input_mode = data.load_input_mode
    if data.bodyweight_fraction is not None:
        exercise.bodyweight_fraction = data.bodyweight_fraction
    if data.estimated_minutes_per_set is not None:
        exercise.estimated_minutes_per_set = data.estimated_minutes_per_set
    if data.notes is not None:
        exercise.notes = data.notes
    session.add(exercise)
    session.commit()
    if data.tissues is not None:
        # Delete existing mappings and replace
        old = session.exec(
            select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
        ).all()
        for et in old:
            session.delete(et)
        session.flush()
        for t in data.tissues:
            session.add(ExerciseTissue(
                exercise_id=exercise.id,
                tissue_id=t.tissue_id,
                role=t.role,
                loading_factor=t.loading_factor,
                routing_factor=t.routing_factor if t.routing_factor is not None else t.loading_factor,
                fatigue_factor=t.fatigue_factor if t.fatigue_factor is not None else t.loading_factor,
                joint_strain_factor=t.joint_strain_factor if t.joint_strain_factor is not None else t.loading_factor,
                tendon_strain_factor=t.tendon_strain_factor if t.tendon_strain_factor is not None else t.loading_factor,
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
    # Delete related sets, exercise_tissues, program_day_exercises
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
    limit: int = Query(default=20, ge=1, le=500),
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

    # Load bodyweight logs for effective weight calculation (counterweight exercises)
    bodyweight_lookup: dict[date, float] = {}
    if exercise.load_input_mode in ("bodyweight", "mixed", "assisted_bodyweight"):
        weight_logs = session.exec(select(WeightLog)).all()
        bodyweight_lookup = bodyweight_by_date(weight_logs)

    # Group by session date
    sessions_map: dict[date, list] = {}
    for ws, d in results:
        sessions_map.setdefault(d, []).append(ws)

    sessions_out = []
    for d in sorted(sessions_map.keys(), reverse=True)[:limit]:
        sets = sessions_map[d]
        if bodyweight_lookup:
            eff_weights = [calc_effective_weight(exercise, s, bodyweight_lookup, d) for s in sets]
            max_weight = max(eff_weights) if eff_weights else 0.0
            total_volume = sum((s.reps or 0) * ew for s, ew in zip(sets, eff_weights))
        else:
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
