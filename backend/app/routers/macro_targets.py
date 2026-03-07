from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.macro_targets import macro_target_to_dict
from app.macros import MACRO_FIELDS
from app.models import MacroTarget

router = APIRouter(prefix="/api/macro-targets", tags=["macro-targets"])


class MacroTargetUpsert(BaseModel):
    day: date
    calories: float = Field(ge=0)
    fat: float = Field(ge=0)
    saturated_fat: float = Field(ge=0)
    cholesterol: float = Field(ge=0)
    sodium: float = Field(ge=0)
    carbs: float = Field(ge=0)
    fiber: float = Field(ge=0)
    protein: float = Field(ge=0)


@router.post("")
def upsert_macro_target(
    data: MacroTargetUpsert,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    target = session.exec(
        select(MacroTarget).where(MacroTarget.day == data.day)
    ).first()
    if not target:
        target = MacroTarget(day=data.day, **{m: getattr(data, m) for m in MACRO_FIELDS})
    else:
        for macro in MACRO_FIELDS:
            setattr(target, macro, float(getattr(data, macro)))

    session.add(target)
    session.commit()
    session.refresh(target)

    next_target = session.exec(
        select(MacroTarget)
        .where(MacroTarget.day > target.day)
        .order_by(MacroTarget.day)
    ).first()
    return macro_target_to_dict(
        target,
        next_day=next_target.day if next_target else None,
    )


@router.get("")
def list_macro_targets(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    all_targets = session.exec(
        select(MacroTarget).order_by(MacroTarget.day)
    ).all()

    result: list[dict] = []
    for index, target in enumerate(all_targets):
        if start_date and target.day < start_date:
            continue
        if end_date and target.day > end_date:
            continue
        next_day = all_targets[index + 1].day if index + 1 < len(all_targets) else None
        result.append(macro_target_to_dict(target, next_day=next_day))
    return result
