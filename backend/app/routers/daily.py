from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.macros import MACRO_FIELDS, sum_macros
from app.models import MealLog
from app.routers.meals import _build_meal_response

router = APIRouter(prefix="/api/daily", tags=["daily"])


def build_daily_summary(day: date, session: Session) -> dict:
    meals = session.exec(
        select(MealLog).where(MealLog.date == day).order_by(MealLog.created_at)
    ).all()
    meal_details = [_build_meal_response(m, session) for m in meals]
    meal_totals = [
        {m: detail.get(f"total_{m}", 0) for m in MACRO_FIELDS}
        for detail in meal_details
    ]
    totals = sum_macros(meal_totals)
    return {"date": str(day), "meals": meal_details, **totals}


@router.get("/{day}")
def daily_summary(
    day: date,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return build_daily_summary(day, session)
