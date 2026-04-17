from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlmodel import Session, col, select

from app.models import Exercise, ExerciseTissue, WeightLog, WorkoutSet

REP_COMPLETION_FACTORS = {
    "full": 1.0,
    "partial": 0.9,
    "failed": 1.05,
}


def bodyweight_by_date(weights: list[WeightLog]) -> dict[date, float]:
    result: dict[date, float] = {}
    for row in weights:
        result[row.logged_at.date()] = row.weight_lb
    return result


def latest_bodyweight(bodyweight_lookup: dict[date, float], workout_date: date) -> float:
    available = [day for day in bodyweight_lookup if day <= workout_date]
    if not available:
        return 0.0
    return bodyweight_lookup[max(available)]


def effective_weight(
    exercise: Exercise,
    workout_set: WorkoutSet,
    bodyweight_lookup: dict[date, float],
    workout_date: date,
) -> float:
    external_multiplier = exercise.external_load_multiplier or 1.0
    external = (workout_set.weight or 0.0) * external_multiplier
    bodyweight = latest_bodyweight(bodyweight_lookup, workout_date)
    bodyweight_component = effective_bodyweight_component(exercise, bodyweight)
    mode = exercise.load_input_mode or "external_weight"
    if mode == "bodyweight":
        return bodyweight_component
    if mode == "mixed":
        return external + bodyweight_component
    if mode == "assisted_bodyweight":
        return max(0.0, bodyweight_component - external)
    if mode == "carry":
        return external
    return external


def effective_bodyweight_component(exercise: Exercise, bodyweight_lb: float) -> float:
    return bodyweight_lb * (exercise.bodyweight_fraction or 0.0)


def entered_weight_for_effective_weight(
    exercise: Exercise,
    *,
    effective_weight_lb: float,
    bodyweight_lb: float,
) -> float | None:
    """Convert effective target load back into the user-entered weight field.

    The planner and training model operate on effective load, but the UI stores
    what the user actually types into the weight field. Those differ for
    assisted bodyweight, mixed bodyweight + external load, and exercises where a
    single entered weight represents multiple implements.
    """
    mode = exercise.load_input_mode or "external_weight"
    multiplier = exercise.external_load_multiplier or 1.0
    if multiplier <= 0:
        multiplier = 1.0

    bodyweight_component = effective_bodyweight_component(exercise, bodyweight_lb)

    if mode == "bodyweight":
        return None
    if mode == "mixed":
        external_effective = max(0.0, effective_weight_lb - bodyweight_component)
        return max(0.0, external_effective / multiplier)
    if mode == "assisted_bodyweight":
        assist_effective = max(0.0, bodyweight_component - effective_weight_lb)
        return max(0.0, assist_effective / multiplier)
    return max(0.0, effective_weight_lb / multiplier)


def load_progression_direction(exercise: Exercise) -> int:
    """Return 1 when higher entered weight is harder, -1 when lower is harder."""
    return -1 if (exercise.load_input_mode or "external_weight") == "assisted_bodyweight" else 1


def effective_set_units(exercise: Exercise, workout_set: WorkoutSet) -> float:
    metric_mode = exercise.set_metric_mode or "reps"
    reps = float(workout_set.reps or 0)
    if metric_mode == "distance":
        if workout_set.distance_steps is not None and workout_set.distance_steps > 0:
            return max(1.0, workout_set.distance_steps / 2.0)
        return reps
    if metric_mode == "duration":
        if workout_set.duration_secs is not None and workout_set.duration_secs > 0:
            return max(1.0, workout_set.duration_secs / 5.0)
        return reps
    if metric_mode == "hybrid":
        if workout_set.distance_steps is not None and workout_set.distance_steps > 0:
            return max(1.0, workout_set.distance_steps / 2.0)
        if workout_set.duration_secs is not None and workout_set.duration_secs > 0:
            return max(reps, workout_set.duration_secs / 5.0)
        return reps
    if workout_set.distance_steps is not None and workout_set.distance_steps > 0:
        return max(1.0, workout_set.distance_steps / 2.0)
    if workout_set.duration_secs is not None and workout_set.duration_secs > 0:
        timed_units = workout_set.duration_secs / 5.0
        return max(reps, timed_units)
    return reps


def effective_set_load(
    exercise: Exercise,
    workout_set: WorkoutSet,
    effective_weight_lb: float,
) -> float:
    units = effective_set_units(exercise, workout_set)
    if units <= 0 or effective_weight_lb <= 0:
        return 0.0
    effort_factor = 1.0
    if workout_set.rpe is not None:
        effort_factor = max(0.85, min(1.15, 1.0 + 0.05 * (workout_set.rpe - 7.0)))
    completion_factor = REP_COMPLETION_FACTORS.get(
        workout_set.rep_completion or "full",
        1.0,
    )
    return max(0.0, units * effective_weight_lb * effort_factor * completion_factor)


def supports_strength_estimate(exercise: Exercise, workout_set: WorkoutSet) -> bool:
    if workout_set.reps is None or workout_set.reps < 1:
        return False
    metric_mode = exercise.set_metric_mode or "reps"
    if metric_mode in {"duration", "distance"}:
        return False
    if workout_set.duration_secs is not None and metric_mode != "hybrid":
        return False
    if workout_set.distance_steps is not None and metric_mode != "hybrid":
        return False
    return (exercise.load_input_mode or "external_weight") != "carry"


def exercise_routing_multipliers(
    session: Session,
    *,
    exercise_ids: Iterable[int] | None = None,
) -> dict[int, float]:
    """Per-exercise routing multiplier matching the daily total of
    /training-model/volume-by-region.

    For each ExerciseTissue row (primary/secondary) whose tissue has >=1 region,
    the by-region endpoint adds ``effective_set_load * routing`` once per region,
    with ``routing = (routing_factor or loading_factor or 1.0) / len(regions)``.
    Summed across regions that equals ``(routing_factor or loading_factor or 1.0)``
    per tissue, so the per-exercise multiplier is the sum over primary+secondary
    tissues with mapped regions.
    """
    from app.tissue_regions import load_tissue_regions  # local import to avoid cycles

    stmt = select(
        ExerciseTissue.exercise_id,
        ExerciseTissue.tissue_id,
        ExerciseTissue.routing_factor,
        ExerciseTissue.loading_factor,
    ).where(col(ExerciseTissue.role).in_(["primary", "secondary"]))
    if exercise_ids is not None:
        id_list = [int(eid) for eid in exercise_ids]
        if not id_list:
            return {}
        stmt = stmt.where(col(ExerciseTissue.exercise_id).in_(id_list))

    rows = session.exec(stmt).all()
    if not rows:
        return {}

    regions_by_tissue = load_tissue_regions(
        session,
        tissue_ids={tissue_id for _, tissue_id, _, _ in rows},
    )
    multipliers: dict[int, float] = {}
    for ex_id, tissue_id, routing, loading in rows:
        if not regions_by_tissue.get(tissue_id):
            continue
        factor = routing or loading or 1.0
        multipliers[ex_id] = multipliers.get(ex_id, 0.0) + float(factor)
    return multipliers
