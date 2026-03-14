import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.planner import (
    complete_workout,
    get_saved_plan,
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
