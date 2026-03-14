import datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    Exercise,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
)
from app.planner import accept_plan, suggest_today, suggest_week

router = APIRouter(prefix="/api/planner", tags=["planner"])


# ── Pydantic schemas ──


class ProgramDayExerciseCreate(BaseModel):
    exercise_id: int
    target_sets: int = 3
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    notes: str | None = None


class ProgramDayCreate(BaseModel):
    day_label: str
    target_regions: list[str] | None = None
    exercises: list[ProgramDayExerciseCreate]
    notes: str | None = None


class ProgramCreate(BaseModel):
    name: str
    notes: str | None = None
    days: list[ProgramDayCreate]


class ProgramUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None
    active: int | None = None
    days: list[ProgramDayCreate] | None = None


class AcceptPlanRequest(BaseModel):
    program_day_id: int
    date: datetime.date


# ── Endpoints ──


@router.get("/today")
def get_today(
    as_of: datetime.date | None = None,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return suggest_today(session, as_of=as_of)


@router.get("/week")
def get_week(
    as_of: datetime.date | None = None,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return suggest_week(session, as_of=as_of)


@router.post("/programs", status_code=201)
def create_program(
    data: ProgramCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    program = TrainingProgram(name=data.name, notes=data.notes)
    session.add(program)
    session.commit()
    session.refresh(program)

    for i, day_data in enumerate(data.days):
        # Validate exercises exist
        for ex_data in day_data.exercises:
            exercise = session.get(Exercise, ex_data.exercise_id)
            if not exercise:
                raise HTTPException(
                    status_code=404,
                    detail=f"Exercise {ex_data.exercise_id} not found",
                )

        day = ProgramDay(
            program_id=program.id,
            day_label=day_data.day_label,
            target_regions=(
                json.dumps(day_data.target_regions) if day_data.target_regions else None
            ),
            sort_order=i,
            notes=day_data.notes,
        )
        session.add(day)
        session.commit()
        session.refresh(day)

        for j, ex_data in enumerate(day_data.exercises):
            pde = ProgramDayExercise(
                program_day_id=day.id,
                exercise_id=ex_data.exercise_id,
                target_sets=ex_data.target_sets,
                target_rep_min=ex_data.target_rep_min,
                target_rep_max=ex_data.target_rep_max,
                sort_order=j,
                notes=ex_data.notes,
            )
            session.add(pde)

    session.commit()
    return _serialize_program(session, program)


@router.get("/programs")
def list_programs(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    programs = list(
        session.exec(
            select(TrainingProgram).order_by(col(TrainingProgram.created_at).desc())
        ).all()
    )
    return [_serialize_program(session, p) for p in programs]


@router.put("/programs/{program_id}")
def update_program(
    program_id: int,
    data: ProgramUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    program = session.get(TrainingProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    if data.name is not None:
        program.name = data.name
    if data.notes is not None:
        program.notes = data.notes
    if data.active is not None:
        program.active = data.active
    session.add(program)

    if data.days is not None:
        # Delete existing days and their exercises
        old_days = list(
            session.exec(
                select(ProgramDay).where(ProgramDay.program_id == program_id)
            ).all()
        )
        for old_day in old_days:
            old_exercises = list(
                session.exec(
                    select(ProgramDayExercise).where(
                        ProgramDayExercise.program_day_id == old_day.id
                    )
                ).all()
            )
            for oe in old_exercises:
                session.delete(oe)
            session.delete(old_day)
        session.commit()

        # Create new days
        for i, day_data in enumerate(data.days):
            for ex_data in day_data.exercises:
                exercise = session.get(Exercise, ex_data.exercise_id)
                if not exercise:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Exercise {ex_data.exercise_id} not found",
                    )

            day = ProgramDay(
                program_id=program_id,
                day_label=day_data.day_label,
                target_regions=(
                    json.dumps(day_data.target_regions)
                    if day_data.target_regions
                    else None
                ),
                sort_order=i,
                notes=day_data.notes,
            )
            session.add(day)
            session.commit()
            session.refresh(day)

            for j, ex_data in enumerate(day_data.exercises):
                pde = ProgramDayExercise(
                    program_day_id=day.id,
                    exercise_id=ex_data.exercise_id,
                    target_sets=ex_data.target_sets,
                    target_rep_min=ex_data.target_rep_min,
                    target_rep_max=ex_data.target_rep_max,
                    sort_order=j,
                    notes=ex_data.notes,
                )
                session.add(pde)

    session.commit()
    session.refresh(program)
    return _serialize_program(session, program)


@router.post("/accept", status_code=201)
def accept_today(
    data: AcceptPlanRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    day = session.get(ProgramDay, data.program_day_id)
    if not day:
        raise HTTPException(status_code=404, detail="Program day not found")
    try:
        return accept_plan(session, data.program_day_id, data.date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/history")
def get_history(
    limit: int = 30,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    planned = list(
        session.exec(
            select(PlannedSession)
            .order_by(col(PlannedSession.date).desc())
            .limit(limit)
        ).all()
    )
    results = []
    for ps in planned:
        day = session.get(ProgramDay, ps.program_day_id)
        program = session.get(TrainingProgram, day.program_id) if day else None
        results.append(
            {
                "id": ps.id,
                "date": ps.date.isoformat(),
                "status": ps.status,
                "workout_session_id": ps.workout_session_id,
                "notes": ps.notes,
                "program_day": {
                    "id": day.id,
                    "day_label": day.day_label,
                    "target_regions": (
                        json.loads(day.target_regions)
                        if day.target_regions
                        else []
                    ),
                }
                if day
                else None,
                "program_name": program.name if program else None,
            }
        )
    return results


# ── Helpers ──


def _serialize_program(session: Session, program: TrainingProgram) -> dict:
    days = list(
        session.exec(
            select(ProgramDay)
            .where(ProgramDay.program_id == program.id)
            .order_by(ProgramDay.sort_order)
        ).all()
    )
    day_list = []
    for day in days:
        exercises = list(
            session.exec(
                select(ProgramDayExercise)
                .where(ProgramDayExercise.program_day_id == day.id)
                .order_by(ProgramDayExercise.sort_order)
            ).all()
        )
        ex_list = []
        for pde in exercises:
            exercise = session.get(Exercise, pde.exercise_id)
            ex_list.append(
                {
                    "id": pde.id,
                    "exercise_id": pde.exercise_id,
                    "exercise_name": exercise.name if exercise else "unknown",
                    "equipment": exercise.equipment if exercise else None,
                    "target_sets": pde.target_sets,
                    "target_rep_min": pde.target_rep_min,
                    "target_rep_max": pde.target_rep_max,
                    "sort_order": pde.sort_order,
                    "notes": pde.notes,
                }
            )
        day_list.append(
            {
                "id": day.id,
                "day_label": day.day_label,
                "target_regions": (
                    json.loads(day.target_regions) if day.target_regions else []
                ),
                "sort_order": day.sort_order,
                "notes": day.notes,
                "exercises": ex_list,
            }
        )
    return {
        "id": program.id,
        "name": program.name,
        "active": program.active,
        "notes": program.notes,
        "created_at": program.created_at.isoformat(),
        "days": day_list,
    }
