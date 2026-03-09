"""Helper functions for log-table queries (tissue, exercise_tissue, tissue_condition)."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from app.config import settings
from app.models import (
    ExerciseTissue,
    Tissue,
    TissueCondition,
    WorkoutSession,
)

# Default workout time when started_at is not available
_DEFAULT_WORKOUT_HOUR = 8


def session_trained_at(ws: WorkoutSession) -> datetime:
    """Best estimate of when a workout session actually happened.

    Priority: started_at > date at 8am in configured timezone.
    """
    if ws.started_at:
        return ws.started_at if ws.started_at.tzinfo else ws.started_at.replace(tzinfo=UTC)
    tz = ZoneInfo(settings.default_timezone)
    local_dt = datetime.combine(ws.date, time(_DEFAULT_WORKOUT_HOUR), tzinfo=tz)
    return local_dt.astimezone(UTC)


def get_current_tissues(session: Session) -> list[Tissue]:
    """Get current tissue definitions (latest row per name)."""
    # Subquery: max updated_at per name
    from sqlalchemy import func

    sub = (
        select(Tissue.name, func.max(Tissue.updated_at).label("max_updated"))
        .group_by(Tissue.name)
        .subquery()
    )
    stmt = (
        select(Tissue)
        .join(sub, (Tissue.name == sub.c.name) & (Tissue.updated_at == sub.c.max_updated))
    )
    return list(session.exec(stmt).all())


def get_current_exercise_tissues(
    session: Session, exercise_id: int
) -> list[ExerciseTissue]:
    """Get current tissue mappings for an exercise (latest per exercise+tissue pair)."""
    from sqlalchemy import func

    sub = (
        select(
            ExerciseTissue.exercise_id,
            ExerciseTissue.tissue_id,
            func.max(ExerciseTissue.updated_at).label("max_updated"),
        )
        .where(ExerciseTissue.exercise_id == exercise_id)
        .group_by(ExerciseTissue.exercise_id, ExerciseTissue.tissue_id)
        .subquery()
    )
    stmt = select(ExerciseTissue).join(
        sub,
        (ExerciseTissue.exercise_id == sub.c.exercise_id)
        & (ExerciseTissue.tissue_id == sub.c.tissue_id)
        & (ExerciseTissue.updated_at == sub.c.max_updated),
    )
    return list(session.exec(stmt).all())


def get_current_tissue_condition(
    session: Session, tissue_id: int
) -> TissueCondition | None:
    """Get the current condition for a tissue (latest row)."""
    stmt = (
        select(TissueCondition)
        .where(TissueCondition.tissue_id == tissue_id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def get_all_current_conditions(session: Session) -> list[TissueCondition]:
    """Get current condition for all tissues that have condition records."""
    from sqlalchemy import func

    sub = (
        select(
            TissueCondition.tissue_id,
            func.max(TissueCondition.updated_at).label("max_updated"),
        )
        .group_by(TissueCondition.tissue_id)
        .subquery()
    )
    stmt = select(TissueCondition).join(
        sub,
        (TissueCondition.tissue_id == sub.c.tissue_id)
        & (TissueCondition.updated_at == sub.c.max_updated),
    )
    return list(session.exec(stmt).all())


def get_tissue_tree(session: Session) -> list[dict]:
    """Get hierarchical tree of all current tissues."""
    tissues = get_current_tissues(session)

    by_id: dict[int, dict] = {}
    roots: list[dict] = []

    for t in tissues:
        node = {
            "id": t.id,
            "name": t.name,
            "display_name": t.display_name,
            "type": t.type,
            "parent_id": t.parent_id,
            "recovery_hours": t.recovery_hours,
            "notes": t.notes,
            "children": [],
        }
        by_id[t.id] = node

    for node in by_id.values():
        pid = node["parent_id"]
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    return roots
