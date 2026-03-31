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
    RehabCheckIn,
    Tissue,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.rehab_protocols import get_rehab_protocol
from app.tracked_tissues import (
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    list_tracked_tissues,
    tracked_volume_and_last_trained,
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


@router.get("/tracked")
def get_tracked_tissue_readiness(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    now = datetime.now(UTC)
    tracked_rows = list_tracked_tissues(session)
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    tracked_conditions = get_all_current_tracked_conditions(session)
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    latest_check_ins = _latest_rehab_check_ins_by_tracked_tissue(session)

    current_ets = session.exec(select(ExerciseTissue)).all()
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    pdes_by_exercise = _active_program_exercises_by_id(session)

    cutoff = now - timedelta(days=7)
    weight_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
    raw_rows = session.exec(
        select(WorkoutSession, WorkoutSet)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= cutoff.date())
    ).all()
    recent_sets: list[tuple[WorkoutSession, WorkoutSet, float]] = []
    for workout_session, workout_set in raw_rows:
        exercise = exercises.get(workout_set.exercise_id)
        if not exercise:
            continue
        set_weight = effective_weight(exercise, workout_set, weight_lookup, workout_session.date)
        effective_load = effective_set_load(exercise, workout_set, set_weight)
        if effective_load <= 0:
            continue
        recent_sets.append((workout_session, workout_set, effective_load))

    volume_7d, cross_education_7d, last_trained = tracked_volume_and_last_trained(
        session=session,
        set_rows=recent_sets,
    )

    mappings_by_tissue: dict[int, list[ExerciseTissue]] = {}
    for mapping in current_ets:
        mappings_by_tissue.setdefault(mapping.tissue_id, []).append(mapping)

    result = []
    for tracked in tracked_rows:
        tissue = tissues.get(tracked.tissue_id)
        if tissue is None:
            continue
        condition = tracked_conditions.get(tracked.id)
        rehab_plan = active_rehab_plans.get(tracked.id)
        latest_check_in = latest_check_ins.get(tracked.id)

        effective_recovery = tissue.recovery_hours
        if condition and condition.recovery_hours_override is not None:
            effective_recovery = condition.recovery_hours_override

        last_trained_at = last_trained.get(tracked.id)
        hours_since = None
        recovery_pct = 100.0
        if last_trained_at is not None:
            hours_since = (now - last_trained_at).total_seconds() / 3600
            if effective_recovery > 0:
                recovery_pct = min(100.0, (hours_since / effective_recovery) * 100)

        protected = bool(
            rehab_plan
            or (condition and condition.status in {"injured", "rehabbing"})
        )
        ready = not protected and (hours_since is None or recovery_pct >= 100.0)

        available = []
        for mapping in mappings_by_tissue.get(tracked.tissue_id, []):
            if mapping.exercise_id not in pdes_by_exercise:
                continue
            exercise = exercises.get(mapping.exercise_id)
            if exercise is None:
                continue
            pde = pdes_by_exercise[mapping.exercise_id]
            available.append({
                "exercise_id": exercise.id,
                "exercise_name": exercise.name,
                "laterality": exercise.laterality,
                "laterality_mode": mapping.laterality_mode,
                "role": mapping.role,
                "target_sets": pde.target_sets,
                "target_rep_min": pde.target_rep_min,
                "target_rep_max": pde.target_rep_max,
            })

        result.append({
            "tracked_tissue": {
                "id": tracked.id,
                "tissue_id": tracked.tissue_id,
                "tissue_name": tissue.name,
                "tissue_display_name": tissue.display_name,
                "tissue_type": tissue.type,
                "region": tissue.region,
                "side": tracked.side,
                "display_name": tracked.display_name,
                "tracking_mode": tissue.tracking_mode,
                "active": tracked.active,
            },
            "condition": _serialize_condition(condition),
            "active_rehab_plan": _serialize_rehab_plan(rehab_plan),
            "latest_rehab_check_in": _serialize_rehab_check_in(latest_check_in),
            "last_trained": last_trained_at.isoformat() if last_trained_at else None,
            "hours_since": round(hours_since, 1) if hours_since is not None else None,
            "effective_recovery_hours": effective_recovery,
            "recovery_pct": round(recovery_pct, 1),
            "protected": protected,
            "ready": ready,
            "volume_7d": round(volume_7d.get(tracked.id, 0.0)),
            "cross_education_7d": round(cross_education_7d.get(tracked.id, 0.0)),
            "exercises_available": available,
        })

    return result


def _active_program_exercises_by_id(session: Session) -> dict[int, ProgramDayExercise]:
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
    return program_by_exercise


def _latest_rehab_check_ins_by_tracked_tissue(
    session: Session,
) -> dict[int, RehabCheckIn]:
    rows = session.exec(
        select(RehabCheckIn).order_by(RehabCheckIn.recorded_at.desc())
    ).all()
    result: dict[int, RehabCheckIn] = {}
    for row in rows:
        result.setdefault(row.tracked_tissue_id, row)
    return result


def _serialize_condition(condition) -> dict | None:
    if condition is None:
        return None
    return {
        "id": condition.id,
        "status": condition.status,
        "severity": condition.severity,
        "max_loading_factor": condition.max_loading_factor,
        "recovery_hours_override": condition.recovery_hours_override,
        "rehab_protocol": condition.rehab_protocol,
        "notes": condition.notes,
        "updated_at": condition.updated_at,
    }


def _serialize_rehab_plan(plan) -> dict | None:
    if plan is None:
        return None
    try:
        protocol = get_rehab_protocol(plan.protocol_id)
        stage = next((item for item in protocol["stages"] if item["id"] == plan.stage_id), None)
        protocol_title = protocol["title"]
        stage_label = stage["label"] if stage else plan.stage_id
    except KeyError:
        protocol_title = plan.protocol_id
        stage_label = plan.stage_id
    return {
        "id": plan.id,
        "protocol_id": plan.protocol_id,
        "protocol_title": protocol_title,
        "stage_id": plan.stage_id,
        "stage_label": stage_label,
        "status": plan.status,
        "pain_monitoring_threshold": plan.pain_monitoring_threshold,
        "max_next_day_flare": plan.max_next_day_flare,
        "sessions_per_week_target": plan.sessions_per_week_target,
        "max_weekly_set_progression": plan.max_weekly_set_progression,
        "max_load_progression_pct": plan.max_load_progression_pct,
        "notes": plan.notes,
        "started_at": plan.started_at,
        "updated_at": plan.updated_at,
    }


def _serialize_rehab_check_in(check_in) -> dict | None:
    if check_in is None:
        return None
    return {
        "id": check_in.id,
        "rehab_plan_id": check_in.rehab_plan_id,
        "pain_0_10": check_in.pain_0_10,
        "stiffness_0_10": check_in.stiffness_0_10,
        "weakness_0_10": check_in.weakness_0_10,
        "neural_symptoms_0_10": check_in.neural_symptoms_0_10,
        "during_load_pain_0_10": check_in.during_load_pain_0_10,
        "next_day_flare": check_in.next_day_flare,
        "confidence_0_10": check_in.confidence_0_10,
        "notes": check_in.notes,
        "recorded_at": check_in.recorded_at,
    }
