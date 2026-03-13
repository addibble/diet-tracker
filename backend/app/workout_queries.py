"""Helper functions for querying workout-related tables."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import settings
from app.models import ExerciseTissue, Tissue, TissueCondition, WorkoutSession, WorkoutSet

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


def get_last_trained_by_tissue(
    session: Session, exercise_tissues: dict[int, list[int]]
) -> dict[int, datetime]:
    """Get most recent trained timestamp per tissue using session time, not row id.

    Workout session ids are not guaranteed to be chronological after imports or
    data repairs, so we must compare actual training timestamps.
    """
    last_trained_map: dict[int, datetime] = {}
    stmt = (
        select(WorkoutSet.exercise_id, WorkoutSession.id)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .distinct()
    )
    for exercise_id, session_id in session.exec(stmt).all():
        tissue_ids = exercise_tissues.get(exercise_id)
        if not tissue_ids:
            continue
        ws = session.get(WorkoutSession, session_id)
        if not ws:
            continue
        last_dt = session_trained_at(ws)
        for tissue_id in tissue_ids:
            existing = last_trained_map.get(tissue_id)
            if existing is None or last_dt > existing:
                last_trained_map[tissue_id] = last_dt
    return last_trained_map


def get_current_tissues(session: Session) -> list[Tissue]:
    """Get all tissue definitions."""
    return list(session.exec(select(Tissue).order_by(Tissue.name)).all())


def get_current_exercise_tissues(
    session: Session, exercise_id: int
) -> list[ExerciseTissue]:
    """Get tissue mappings for an exercise."""
    return list(session.exec(
        select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
    ).all())


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
