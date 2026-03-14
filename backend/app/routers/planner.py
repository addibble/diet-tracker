import datetime

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.planner import suggest_today

router = APIRouter(prefix="/api/planner", tags=["planner"])


@router.get("/today")
def get_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return suggest_today(session, as_of=as_of)
