from __future__ import annotations

from datetime import date

from app.models import Exercise, WeightLog, WorkoutSet

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
    """Return the unit count for volume calculations.

    Reads ``endurance_value`` (the unified y-axis quantity) and scales by
    metric mode so distance/duration sets contribute reasonable volume
    numbers next to rep-mode sets.
    """
    metric_mode = exercise.set_metric_mode or "reps"
    ev = workout_set.endurance_value
    if ev is None or ev <= 0:
        return 0.0
    if metric_mode == "distance":
        return max(1.0, float(ev) / 2.0)
    if metric_mode == "duration":
        return max(1.0, float(ev) / 5.0)
    return float(ev)


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
    """True when this set has the (weight, endurance, RPE) triple needed to fit a curve.

    Pure-bodyweight exercises are filtered out at a higher level (they're
    routed to ``get_bodyweight_suggestion`` instead). Duration-mode
    exercises (e.g., weighted plank) are excluded here: their endurance is
    seconds, which doesn't follow a strength-curve shape and produces
    pathological prescriptions like "100 lb x 20 s" when historical
    practice is "45 lb x 45 s". They are routed to
    ``get_duration_suggestion`` instead.
    """
    if (exercise.set_metric_mode or "reps") == "duration":
        return False
    endurance = workout_set.endurance_value
    if endurance is None or endurance < 1:
        return False
    return True
