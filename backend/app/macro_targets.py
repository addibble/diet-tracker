from datetime import date

from sqlmodel import Session, select

from app.macros import MACRO_FIELDS
from app.models import MacroTarget


def macro_target_to_dict(
    target: MacroTarget,
    next_day: date | None = None,
) -> dict:
    payload = {
        "id": target.id,
        "day": str(target.day),
        "next_day": str(next_day) if next_day else None,
    }
    for macro in MACRO_FIELDS:
        payload[macro] = float(getattr(target, macro))
    return payload


def get_active_macro_target(day: date, session: Session) -> dict | None:
    target = session.exec(
        select(MacroTarget)
        .where(MacroTarget.day <= day)
        .order_by(MacroTarget.day.desc())
    ).first()
    if not target:
        return None

    next_target = session.exec(
        select(MacroTarget)
        .where(MacroTarget.day > target.day)
        .order_by(MacroTarget.day)
    ).first()
    return macro_target_to_dict(
        target,
        next_day=next_target.day if next_target else None,
    )
