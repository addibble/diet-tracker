import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.planner import (
    add_exercises_to_plan,
    complete_workout,
    delete_plan,
    get_saved_plan,
    remove_exercises_from_plan,
    reorder_plan_exercises,
    save_plan,
    start_workout,
    suggest_today,
)

router = APIRouter(prefix="/api/planner", tags=["planner"])


class SavePlanRequest(BaseModel):
    day_label: str
    target_regions: list[str]
    exercises: list[dict]


@router.get("/today")
def get_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return suggest_today(session, as_of=as_of)


@router.post("/save", status_code=201)
def save_today(
    data: SavePlanRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    return save_plan(session, plan_date, data.day_label, data.target_regions, data.exercises)


@router.get("/active")
def get_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return plan


@router.post("/start", status_code=200)
def start_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return start_workout(session, plan["id"])


@router.post("/complete", status_code=200)
def complete_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return complete_workout(session, plan["id"])


@router.delete("/active", status_code=204)
def delete_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        delete_plan(session, plan_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddExercisesRequest(BaseModel):
    exercises: list[dict]


@router.post("/active/exercises", status_code=200)
def add_exercises(
    data: AddExercisesRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return add_exercises_to_plan(session, plan_date, data.exercises)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/active/exercises/{exercise_id}", status_code=200)
def remove_exercise(
    exercise_id: int,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return remove_exercises_from_plan(session, plan_date, [exercise_id])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ReorderRequest(BaseModel):
    pde_ids: list[int]


@router.patch("/active/reorder", status_code=200)
def reorder_exercises(
    data: ReorderRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return reorder_plan_exercises(session, plan_date, data.pde_ids)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
