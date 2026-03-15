from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.exercise_loads import bodyweight_by_date, effective_set_load, effective_weight
from app.models import (
    Exercise,
    ExerciseTissue,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.workout_queries import (
    get_all_current_conditions,
    get_current_tissues,
    get_last_trained_by_tissue,
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

    # Get all exercise_tissue mappings
    current_ets = session.exec(select(ExerciseTissue)).all()

    # Map exercise_id → list of tissue_ids
    exercise_tissues: dict[int, list[int]] = {}
    for et in current_ets:
        exercise_tissues.setdefault(et.exercise_id, []).append(et.tissue_id)

    # Build map: tissue_id → last trained datetime using actual session time.
    last_trained_map = get_last_trained_by_tissue(session, exercise_tissues)

    # Compute 7-day volume per tissue
    cutoff = now - timedelta(days=7)
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    weight_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
    volume_rows = session.exec(
        select(WorkoutSession.date, WorkoutSet)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= cutoff.date())
    ).all()

    # Roll up volume to each tissue
    tissue_volume_7d: dict[int, float] = {}
    routing_by_exercise: dict[int, list[tuple[int, float]]] = {}
    for et in current_ets:
        routing_by_exercise.setdefault(et.exercise_id, []).append(
            (et.tissue_id, et.routing_factor or et.loading_factor or 1.0)
        )
    for workout_date, workout_set in volume_rows:
        exercise = exercises.get(workout_set.exercise_id)
        if not exercise:
            continue
        set_weight = effective_weight(exercise, workout_set, weight_lookup, workout_date)
        vol = effective_set_load(exercise, workout_set, set_weight)
        if vol <= 0:
            continue
        for tissue_id, routing in routing_by_exercise.get(workout_set.exercise_id, []):
            tissue_volume_7d[tissue_id] = tissue_volume_7d.get(tissue_id, 0.0) + (vol * routing)

    # Get active program exercises for "exercises_available"
    active_program = session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()
    program_by_exercise: dict[int, ProgramDayExercise] = {}
    if active_program:
        pdes = session.exec(
            select(ProgramDayExercise)
            .join(ProgramDay)
            .where(ProgramDay.program_id == active_program.id)
        ).all()
        for pde in pdes:
            program_by_exercise.setdefault(pde.exercise_id, pde)

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
            if et.tissue_id == t.id and et.exercise_id in program_by_exercise:
                exercise = session.get(Exercise, et.exercise_id)
                if exercise:
                    pde = program_by_exercise[et.exercise_id]
                    available.append({
                        "exercise_id": exercise.id,
                        "exercise_name": exercise.name,
                        "role": et.role,
                        "target_sets": pde.target_sets,
                        "target_rep_min": pde.target_rep_min,
                        "target_rep_max": pde.target_rep_max,
                    })

        result.append({
            "tissue": {
                "id": t.id,
                "name": t.name,
                "display_name": t.display_name,
                "type": t.type,
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
            "volume_7d": round(tissue_volume_7d.get(t.id, 0.0)),
            "exercises_available": available,
        })

    return result
