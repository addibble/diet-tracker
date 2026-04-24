"""Single source of truth for weight-space and rep-space conversions.

This module exists to prevent the class of bug where a *value* flows across a
boundary that changes its physical interpretation — for example passing an
entered weight (what the athlete types) into a function that expects effective
weight (what the body resists after multipliers and bodyweight components), or
subtracting RIR twice because one side already did it.

## Spaces

There are four scalar spaces in the strength model:

- **entered weight** (lb): what the athlete types into the weight field.
  This is what ``WorkoutSet.weight`` stores and what the frontend plots on
  the x-axis. For a pair of 10 lb dumbbells the athlete types ``10``.

- **effective weight** (lb): the load the body actually resists, computed from
  entered weight via the exercise's ``external_load_multiplier``,
  ``bodyweight_fraction``, and ``load_input_mode``. For the same dumbbell
  exercise above the effective weight is 20 lb. Strength-curve fitting runs
  in effective-weight space.

- **r_fail / rtf** (reps-to-failure): the model's y-axis. For a set taken to
  RIR 2 with 12 reps completed, ``rtf = 14``.

- **r_done / reps_done** (reps completed): what the athlete reports and what
  shows up in ``WorkoutSet.reps``. ``reps_done = rtf - rir``.

## Usage

Call the helpers below instead of inlining the arithmetic. Every inline
``reps + rir``, ``rpe_to_rir``, ``weight * multiplier``, or
``bodyweight * fraction`` in a hot code path is a potential site for a
space-mixup bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.exercise_loads import (
    effective_bodyweight_component,
    effective_weight,
    entered_weight_for_effective_weight,
)
from app.models import Exercise

__all__ = [
    "entered_to_effective_lb",
    "effective_to_entered_lb",
    "rtf_to_reps_done",
    "reps_done_to_rtf",
    "rpe_to_rir",
    "rir_to_rpe",
    "bodyweight_component_lb",
    "endurance_value_from_legacy",
    "EnduranceMetric",
    "metric_for",
    # Re-exports of the canonical helpers in exercise_loads so callers have
    # one import path for unit-aware code.
    "effective_weight",
    "entered_weight_for_effective_weight",
]


MetricKind = Literal["reps", "duration", "distance"]


@dataclass(frozen=True)
class EnduranceMetric:
    """Describes the unit of ``WorkoutSet.endurance_value`` for an exercise.

    The strength-curve math (``r_fail = k·(M/W − 1)^γ``) is unit-agnostic —
    every value on the y-axis is "endurance to failure" in the metric's
    native unit. This dataclass only carries display + formatting info so
    the API/frontend can render results correctly.
    """

    kind: MetricKind
    display_unit: str  # "reps" | "s" | "steps"
    int_valued: bool

    @property
    def label(self) -> str:
        """Human-readable label for prescription dicts."""
        return {"reps": "reps", "duration": "seconds", "distance": "steps"}[self.kind]


_METRICS: dict[MetricKind, EnduranceMetric] = {
    "reps": EnduranceMetric(kind="reps", display_unit="reps", int_valued=True),
    "duration": EnduranceMetric(kind="duration", display_unit="s", int_valued=True),
    "distance": EnduranceMetric(kind="distance", display_unit="steps", int_valued=True),
}


def metric_for(exercise: Exercise) -> EnduranceMetric:
    """Return the EnduranceMetric for an exercise's set_metric_mode."""
    mode = (exercise.set_metric_mode or "reps").lower()
    return _METRICS.get(mode, _METRICS["reps"])  # type: ignore[arg-type]


def endurance_value_from_legacy(
    exercise: Exercise,
    *,
    reps: int | float | None,
    duration_secs: int | float | None,
    distance_steps: int | float | None,
) -> float | None:
    """Pick the correct endurance-to-failure value for a new/updated set.

    The result is the value we store in ``WorkoutSet.endurance_value``; its
    unit is determined by the exercise's ``set_metric_mode``. Falls back to
    ``reps`` when the mode-specific column is missing (matches the backfill's
    tolerance for Weighted Plank's legacy seconds-as-reps rows).
    """
    mode = (exercise.set_metric_mode or "reps").lower()
    if mode == "duration":
        if duration_secs is not None:
            return float(duration_secs)
        if reps is not None:
            return float(reps)
        return None
    if mode == "distance":
        if distance_steps is not None:
            return float(distance_steps)
        if reps is not None:
            return float(reps)
        return None
    # reps / anything else
    if reps is not None:
        return float(reps)
    return None


def entered_to_effective_lb(
    exercise: Exercise, entered_weight_lb: float, bodyweight_lb: float,
) -> float:
    """Convert entered weight (athlete input) to effective weight (body load)."""
    mode = exercise.load_input_mode or "external_weight"
    multiplier = exercise.external_load_multiplier or 1.0
    if multiplier <= 0:
        multiplier = 1.0
    bw_component = bodyweight_component_lb(exercise, bodyweight_lb)

    if mode == "bodyweight":
        return bw_component
    if mode == "mixed":
        return entered_weight_lb * multiplier + bw_component
    if mode == "assisted_bodyweight":
        return max(0.0, bw_component - entered_weight_lb * multiplier)
    # external_weight / default
    return entered_weight_lb * multiplier


def effective_to_entered_lb(
    exercise: Exercise, effective_weight_lb: float, bodyweight_lb: float,
) -> float | None:
    """Convert effective weight to entered weight. None for pure bodyweight.

    Thin wrapper around `exercise_loads.entered_weight_for_effective_weight`
    with a keyword-free signature for use inside strength-model code.
    """
    return entered_weight_for_effective_weight(
        exercise,
        effective_weight_lb=effective_weight_lb,
        bodyweight_lb=bodyweight_lb,
    )


def bodyweight_component_lb(exercise: Exercise, bodyweight_lb: float) -> float:
    """Effective-weight contribution from the athlete's bodyweight."""
    return effective_bodyweight_component(exercise, bodyweight_lb)


def rtf_to_reps_done(rtf: float, rir: float) -> int:
    """Convert reps-to-failure to actual reps performed (``rtf - rir``).

    Clamped to 1 because the training-model scheme never prescribes 0 reps.
    """
    return max(1, int(round(rtf - rir)))


def reps_done_to_rtf(reps_done: float, rir: float) -> float:
    """Convert actual reps performed to reps-to-failure (``reps_done + rir``)."""
    return float(reps_done) + float(rir)


def rpe_to_rir(rpe: float) -> float:
    """Convert RPE (0-10) to RIR (reps in reserve) = ``10 - rpe``."""
    return 10.0 - float(rpe)


def rir_to_rpe(rir: float) -> float:
    """Convert RIR to RPE = ``10 - rir``."""
    return 10.0 - float(rir)
