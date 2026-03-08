from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    Exercise,
    ExerciseTissue,
    RoutineExercise,
    WorkoutSession,
    WorkoutSet,
)
from app.workout_queries import (
    get_all_current_conditions,
    get_current_tissues,
)

router = APIRouter(prefix="/api/tissue-readiness", tags=["tissue-readiness"])


@router.get("")
def get_tissue_readiness(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    now = datetime.now(UTC)
    tissues = get_current_tissues(session)
    conditions = {c.tissue_id: c for c in get_all_current_conditions(session)}

    # Build map: tissue_id → last trained datetime
    # Find the most recent workout_set for each tissue via exercise_tissue mappings
    last_trained_map: dict[int, datetime] = {}

    # Get all exercise_tissue mappings (current)
    et_sub = (
        select(
            ExerciseTissue.exercise_id,
            ExerciseTissue.tissue_id,
            func.max(ExerciseTissue.updated_at).label("max_updated"),
        )
        .group_by(ExerciseTissue.exercise_id, ExerciseTissue.tissue_id)
        .subquery()
    )
    current_ets = session.exec(
        select(ExerciseTissue).join(
            et_sub,
            (ExerciseTissue.exercise_id == et_sub.c.exercise_id)
            & (ExerciseTissue.tissue_id == et_sub.c.tissue_id)
            & (ExerciseTissue.updated_at == et_sub.c.max_updated),
        )
    ).all()

    # Map exercise_id → list of tissue_ids
    exercise_tissues: dict[int, list[int]] = {}
    for et in current_ets:
        exercise_tissues.setdefault(et.exercise_id, []).append(et.tissue_id)

    # Get most recent session date per exercise
    stmt = (
        select(WorkoutSet.exercise_id, func.max(WorkoutSession.date).label("last_date"))
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .group_by(WorkoutSet.exercise_id)
    )
    for row in session.exec(stmt).all():
        exercise_id, last_date = row
        if exercise_id in exercise_tissues:
            # Convert date to datetime for hours calculation
            last_dt = datetime(last_date.year, last_date.month, last_date.day, tzinfo=UTC)
            for tissue_id in exercise_tissues[exercise_id]:
                existing = last_trained_map.get(tissue_id)
                if existing is None or last_dt > existing:
                    last_trained_map[tissue_id] = last_dt

    # Propagate to parents: if a child was trained, parent is also trained
    tissue_by_id = {t.id: t for t in tissues}
    for tissue_id, last_dt in list(last_trained_map.items()):
        t = tissue_by_id.get(tissue_id)
        while t and t.parent_id:
            parent_dt = last_trained_map.get(t.parent_id)
            if parent_dt is None or last_dt > parent_dt:
                last_trained_map[t.parent_id] = last_dt
            t = tissue_by_id.get(t.parent_id)

    # Get routine exercises for "exercises_available"
    routine_exercises = session.exec(
        select(RoutineExercise).where(RoutineExercise.active == 1)
    ).all()
    routine_by_exercise: dict[int, RoutineExercise] = {
        re.exercise_id: re for re in routine_exercises
    }

    # Build readiness for each tissue
    result = []
    for t in tissues:
        condition = conditions.get(t.id)
        last_trained = last_trained_map.get(t.id)

        # Effective recovery hours (condition override > tissue default)
        effective_recovery = t.recovery_hours
        if condition and condition.recovery_hours_override is not None:
            effective_recovery = condition.recovery_hours_override

        hours_since = None
        recovery_pct = 100.0
        ready = True
        if last_trained:
            hours_since = (now - last_trained).total_seconds() / 3600
            if effective_recovery > 0:
                recovery_pct = min(100.0, (hours_since / effective_recovery) * 100)
            else:
                recovery_pct = 100.0
            ready = recovery_pct >= 100.0

        # Injured tissues are never ready
        if condition and condition.status == "injured":
            ready = False

        # Find routine exercises that target this tissue
        available = []
        for et in current_ets:
            if et.tissue_id == t.id and et.exercise_id in routine_by_exercise:
                exercise = session.get(Exercise, et.exercise_id)
                if exercise:
                    re = routine_by_exercise[et.exercise_id]
                    available.append({
                        "exercise_id": exercise.id,
                        "exercise_name": exercise.name,
                        "role": et.role,
                        "target_sets": re.target_sets,
                        "target_rep_min": re.target_rep_min,
                        "target_rep_max": re.target_rep_max,
                    })

        result.append({
            "tissue": {
                "id": t.id,
                "name": t.name,
                "display_name": t.display_name,
                "type": t.type,
                "parent_id": t.parent_id,
                "recovery_hours": t.recovery_hours,
            },
            "condition": {
                "status": condition.status,
                "severity": condition.severity,
                "max_loading_factor": condition.max_loading_factor,
                "recovery_hours_override": condition.recovery_hours_override,
            } if condition else None,
            "last_trained": last_trained.isoformat() if last_trained else None,
            "hours_since": round(hours_since, 1) if hours_since is not None else None,
            "effective_recovery_hours": effective_recovery,
            "recovery_pct": round(recovery_pct, 1),
            "ready": ready,
            "exercises_available": available,
        })

    return result
