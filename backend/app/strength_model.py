"""Strength-curve model: r_fresh(W) = k * (M/W - 1)^gamma.

Fits the fresh-set strength curve from recent RPE data and provides
weight/rep prescription for progressive-overload workouts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize
from sqlmodel import Session, select

from app.exercise_loads import (
    bodyweight_by_date,
    effective_weight,
    entered_weight_for_effective_weight,
    latest_bodyweight,
    supports_strength_estimate,
)
from app.models import Exercise, WeightLog, WorkoutSession, WorkoutSet

BODYWEIGHT_MODES = {"bodyweight", "assisted_bodyweight"}

# Default class prior for gamma from Stage A RPE-only fitting
DEFAULT_GAMMA = 0.20

# Minimum qualifying sets for curve fitting
MIN_SETS_TIER1 = 5  # full 3-param (M, k, gamma)
MIN_SETS_TIER2 = 3  # 2-param (M, k) with fixed gamma
MIN_DISTINCT_WEIGHTS_TIER1 = 2


@dataclass
class CurveFit:
    """Result of fitting r_fresh(W) = k * (M/W - 1)^gamma."""

    M: float  # estimated 1RM ceiling (effective weight)
    k: float  # endurance scaling
    gamma: float  # curve shape exponent
    n_obs: int
    rmse: float
    max_observed_weight: float  # max effective weight in fitting data
    fit_tier: str  # "tier1" or "tier2"
    identifiability: float = 1.0  # 0-1 quality score


@dataclass
class SetPrescription:
    """Prescription for a single set in a progressive workout."""

    set_number: int  # 1, 2, or 3
    effective_weight: float
    entered_weight: float | None  # what user types (None for bodyweight)
    target_reps: int  # reps to perform (after RIR subtraction)
    target_rpe: float  # RPE to aim for
    r_fail: float  # predicted reps-to-failure at this weight
    acceptable_rep_min: int
    acceptable_rep_max: int


# ── Progressive set schemes ──

HEAVY_SCHEME = [
    # (r_fail_target, rir, target_rpe, expected_actual, rep_min, rep_max)
    (18, 3, 7.0, 15, 12, 18),
    (12, 2, 8.0, 10, 8, 12),
    (6, 1, 9.0, 5, 4, 6),
]

LIGHT_SCHEME = [
    (23, 3, 7.0, 20, 17, 23),
    (20, 2, 8.0, 18, 16, 20),
    (16, 1, 9.0, 15, 13, 16),
]


# ── Core math ──


def fresh_curve(
    W: float | np.ndarray, M: float, k: float, gamma: float
) -> float | np.ndarray:
    """r_fresh(W) = k * (M/W - 1)^gamma. Returns 0 for W >= M."""
    ratio = M / W - 1.0
    if isinstance(ratio, np.ndarray):
        return np.where(ratio > 0, k * np.power(ratio, gamma), 0.0)
    return k * (ratio**gamma) if ratio > 0 else 0.0


def predict_reps(weight: float, fit: CurveFit) -> float:
    """Predict reps-to-failure at a given effective weight."""
    return float(fresh_curve(weight, fit.M, fit.k, fit.gamma))


def solve_weight(target_reps: float, fit: CurveFit) -> float:
    """Invert the curve: find effective weight W where r_fresh(W) = target_reps.

    W = M / (1 + (target_reps / k)^(1/gamma))
    """
    if target_reps <= 0 or fit.k <= 0:
        return fit.M * 0.95
    ratio = (target_reps / fit.k) ** (1.0 / fit.gamma)
    return fit.M / (1.0 + ratio)


# ── RPE confidence and recency ──


def _rpe_confidence(rpe: float) -> float:
    """Higher confidence for sets closer to failure."""
    rir = 10.0 - rpe
    return max(0.2, math.exp(-0.25 * rir))


def _recency_weights(
    ages_days: list[float], half_life_days: float = 30.0
) -> np.ndarray:
    """Exponential recency weighting: recent sets count more."""
    arr = np.array(ages_days, dtype=float)
    return np.exp(-np.log(2) * arr / half_life_days)


# ── Brzycki bounds ──


def _brzycki_1rm(weight: float, reps: float) -> float:
    """Brzycki 1RM estimate: W * 36 / (37 - r). Capped at 30 reps."""
    if reps >= 37:
        return weight * 2.5
    return weight * 36.0 / (37.0 - min(reps, 36))


def _estimate_M_bounds(
    weights: list[float], reps_to_failure: list[float]
) -> tuple[float, float, float]:
    """Estimate M bounds using Brzycki 1RM cross-checks.

    Returns (lower_bound, upper_bound, M_prior).
    """
    max_w = max(weights)
    brzycki_estimates = [
        _brzycki_1rm(w, r) for w, r in zip(weights, reps_to_failure) if r > 0
    ]

    if not brzycki_estimates:
        M_prior = max_w * 1.3
        return (max_w * 1.01, max_w * 2.0, M_prior)

    median_1rm = float(np.median(brzycki_estimates))
    max_1rm = float(np.max(brzycki_estimates))
    M_prior = median_1rm
    lower = max(max_w * 1.01, median_1rm * 0.8)
    upper = max(max_1rm * 1.5, max_w * 2.0)
    return (lower, upper, M_prior)


def _identifiability_score(
    weights: list[float], reps: list[float]
) -> float:
    """Score 0-1 for how well the data can identify M."""
    if len(weights) < 3:
        return 0.0

    min_w, max_w = min(weights), max(weights)
    distinct_w = len(set(round(w, 1) for w in weights))

    range_ratio = max_w / max(min_w, 1.0)
    range_score = min(1.0, (range_ratio - 1.0) / 1.0)
    weight_variety = min(1.0, (distinct_w - 1) / 4.0)

    slope_score = 0.0
    if max_w > min_w and len(weights) > 2:
        corr = abs(np.corrcoef(weights, reps)[0, 1])
        if not np.isnan(corr):
            slope_score = corr

    return float(np.clip(range_score * 0.4 + weight_variety * 0.3 + slope_score * 0.3, 0.0, 1.0))


# ── Curve fitting ──


def _curve_loss(
    params: list[float],
    W: np.ndarray,
    r: np.ndarray,
    fit_weights: np.ndarray,
    fixed_gamma: float | None,
    M_prior: float,
    lambda_M: float,
) -> float:
    """Weighted least squares loss with log-space Brzycki prior on M."""
    if fixed_gamma is not None:
        M, k = params
        gamma = fixed_gamma
    else:
        M, k, gamma = params

    predicted = fresh_curve(W, M, k, gamma)
    residuals = r - predicted
    data_loss = float(np.sum(fit_weights * residuals**2))

    reg_term = 0.0
    if M_prior > 0 and lambda_M > 0:
        reg_term = lambda_M * math.log(M / M_prior) ** 2

    return data_loss + reg_term


def _fit_params(
    W: np.ndarray,
    r: np.ndarray,
    fit_weights: np.ndarray,
    M_lower: float,
    M_upper: float,
    M_prior: float,
    lambda_M: float,
    fixed_gamma: float | None = None,
) -> tuple[float, float, float, bool]:
    """Run multi-restart optimization. Returns (M, k, gamma, success)."""
    max_W = float(np.max(W))
    best_result = None
    best_loss = float("inf")

    gamma_inits = [0.15, 0.5, 1.0] if fixed_gamma is None else [None]
    M_factors = [1.1, 1.3, 1.5, 2.0]

    for M_factor in M_factors:
        for g_init in gamma_inits:
            M_init = float(np.clip(max_W * M_factor, M_lower, M_upper))
            k_init = float(np.median(r))

            if fixed_gamma is not None:
                x0 = [M_init, k_init]
                bounds = [(M_lower, M_upper), (0.5, 200.0)]
            else:
                x0 = [M_init, k_init, g_init]
                bounds = [(M_lower, M_upper), (0.5, 200.0), (0.05, 3.0)]

            try:
                res = minimize(
                    _curve_loss,
                    x0=x0,
                    args=(W, r, fit_weights, fixed_gamma, M_prior, lambda_M),
                    method="L-BFGS-B",
                    bounds=bounds,
                )
                if res.fun < best_loss:
                    best_loss = res.fun
                    best_result = res
            except Exception:
                continue

    if best_result is None:
        return (float(np.clip(max_W * 1.1, M_lower, M_upper)),
                float(np.median(r)), fixed_gamma or DEFAULT_GAMMA, False)

    if fixed_gamma is not None:
        M_fit, k_fit = best_result.x
        return (M_fit, k_fit, fixed_gamma, True)
    else:
        M_fit, k_fit, gamma_fit = best_result.x
        return (M_fit, k_fit, gamma_fit, True)


# ── Data loading helpers ──


def _load_recent_sets(
    exercise_id: int, session: Session, days: int
) -> tuple[Exercise | None, list[tuple[WorkoutSet, date]]]:
    """Load exercise and its recent RPE sets."""
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return None, []

    # Exclude bodyweight and non-strength exercises at the exercise level
    if (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES:
        return exercise, []

    cutoff = date.today() - timedelta(days=days)

    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.rpe.is_not(None),  # RPE-only
            WorkoutSet.reps.is_not(None),
            WorkoutSet.reps > 0,
            WorkoutSession.date >= cutoff,
        )
        .order_by(WorkoutSession.date.desc(), WorkoutSet.set_order)
    )
    rows = session.exec(stmt).all()
    return exercise, [(ws, d) for ws, d in rows]


def _load_bodyweight_lookup(session: Session) -> dict[date, float]:
    """Load bodyweight history for effective weight calculations."""
    weights = session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all()
    return bodyweight_by_date(weights)


# ── Main fitting function ──


def fit_curve(
    exercise_id: int, session: Session, *, days: int = 30
) -> CurveFit | None:
    """Fit the fresh-set strength curve for an exercise using recent RPE data.

    Returns None if insufficient qualifying data (< MIN_SETS_TIER2 RPE sets
    within the last `days` days, or exercise is bodyweight/non-strength).
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days)
    if exercise is None or not set_rows:
        return None

    bw_lookup = _load_bodyweight_lookup(session)
    today = date.today()

    # Build observations
    eff_weights: list[float] = []
    reps_to_failure: list[float] = []
    confidences: list[float] = []
    ages_days: list[float] = []

    for ws, ws_date in set_rows:
        if not supports_strength_estimate(exercise, ws):
            continue
        if ws.rpe is None or ws.rpe < 5.0 or ws.rpe > 10.0:
            continue

        ew = effective_weight(exercise, ws, bw_lookup, ws_date)
        if ew <= 0:
            continue

        rir = 10.0 - ws.rpe
        r_fail = ws.reps + rir

        eff_weights.append(ew)
        reps_to_failure.append(r_fail)
        confidences.append(_rpe_confidence(ws.rpe))
        ages_days.append((today - ws_date).days)

    n_obs = len(eff_weights)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(eff_weights)
    r = np.array(reps_to_failure)
    conf = np.array(confidences)
    recency = _recency_weights(ages_days)
    fit_w = conf * recency

    # Determine tier
    distinct_w = len(set(round(w, 1) for w in eff_weights))
    tier = "tier1" if n_obs >= MIN_SETS_TIER1 and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1 else "tier2"

    # Bounds and regularization
    M_lower, M_upper, M_prior = _estimate_M_bounds(eff_weights, reps_to_failure)
    ident = _identifiability_score(eff_weights, reps_to_failure)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, fixed_gamma
    )

    # Compute RMSE
    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit,
        k=k_fit,
        gamma=gamma_fit,
        n_obs=n_obs,
        rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier,
        identifiability=ident,
    )


# ── Prescription ──


def plan_progressive_sets(
    fit: CurveFit,
    exercise: Exercise,
    bodyweight_lb: float,
    max_entered_weight: float | None = None,
) -> list[SetPrescription]:
    """Generate 3 progressive-overload set prescriptions.

    Uses heavy scheme if exercise.allow_heavy_loading, else light scheme.
    Converts effective weight to entered weight for user display.
    Clips to max_entered_weight if the user is at machine limit.
    """
    scheme = HEAVY_SCHEME if exercise.allow_heavy_loading else LIGHT_SCHEME
    prescriptions: list[SetPrescription] = []

    for i, (r_fail, rir, target_rpe, expected_reps, rep_min, rep_max) in enumerate(scheme):
        ew = solve_weight(r_fail, fit)
        entered = entered_weight_for_effective_weight(
            exercise, effective_weight_lb=ew, bodyweight_lb=bodyweight_lb
        )

        # Clip to max available weight
        if entered is not None and max_entered_weight is not None:
            entered = min(entered, max_entered_weight)
            # Recompute effective weight from clipped entered weight
            ew = _entered_to_effective(exercise, entered, bodyweight_lb)
            # Recompute expected reps at the clipped weight
            r_fail = predict_reps(ew, fit)
            expected_reps = max(1, round(r_fail - rir))
            rep_min = max(1, expected_reps - 3)
            rep_max = expected_reps + 3

        prescriptions.append(SetPrescription(
            set_number=i + 1,
            effective_weight=round(ew, 1),
            entered_weight=round(entered, 1) if entered is not None else None,
            target_reps=expected_reps,
            target_rpe=target_rpe,
            r_fail=round(r_fail, 1),
            acceptable_rep_min=rep_min,
            acceptable_rep_max=rep_max,
        ))

    return prescriptions


def adjust_prescription(
    fit: CurveFit,
    exercise: Exercise,
    actual_entered_weight: float,
    bodyweight_lb: float,
    set_number: int,
    allow_heavy: bool,
) -> SetPrescription:
    """Recalculate target reps after user enters actual available weight."""
    ew = _entered_to_effective(exercise, actual_entered_weight, bodyweight_lb)
    r_fail = predict_reps(ew, fit)

    scheme = HEAVY_SCHEME if allow_heavy else LIGHT_SCHEME
    _, rir, target_rpe, _, _, _ = scheme[set_number - 1]

    expected_reps = max(1, round(r_fail - rir))

    return SetPrescription(
        set_number=set_number,
        effective_weight=round(ew, 1),
        entered_weight=round(actual_entered_weight, 1),
        target_reps=expected_reps,
        target_rpe=target_rpe,
        r_fail=round(r_fail, 1),
        acceptable_rep_min=max(1, expected_reps - 3),
        acceptable_rep_max=expected_reps + 3,
    )


def refit_with_observations(
    exercise_id: int,
    session: Session,
    new_obs: list[dict],
    *,
    days: int = 30,
) -> CurveFit | None:
    """Refit the curve incorporating in-session observations.

    new_obs: list of {"weight": float, "reps": int, "rpe": float}
    where weight is the entered weight (will be converted to effective).
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days)
    if exercise is None:
        return None

    bw_lookup = _load_bodyweight_lookup(session)
    bodyweight_lb = latest_bodyweight(bw_lookup, date.today())
    today = date.today()

    # Build observations from DB
    eff_weights: list[float] = []
    reps_to_failure: list[float] = []
    confidences: list[float] = []
    ages_days: list[float] = []

    for ws, ws_date in set_rows:
        if not supports_strength_estimate(exercise, ws):
            continue
        if ws.rpe is None or ws.rpe < 5.0 or ws.rpe > 10.0:
            continue

        ew = effective_weight(exercise, ws, bw_lookup, ws_date)
        if ew <= 0:
            continue

        rir = 10.0 - ws.rpe
        eff_weights.append(ew)
        reps_to_failure.append(ws.reps + rir)
        confidences.append(_rpe_confidence(ws.rpe))
        ages_days.append((today - ws_date).days)

    # Add new in-session observations (age=0, high confidence since just performed)
    for obs in new_obs:
        if obs.get("rpe") is None or obs.get("reps") is None:
            continue
        ew = _entered_to_effective(exercise, obs["weight"], bodyweight_lb)
        if ew <= 0:
            continue
        rir = 10.0 - obs["rpe"]
        eff_weights.append(ew)
        reps_to_failure.append(obs["reps"] + rir)
        confidences.append(_rpe_confidence(obs["rpe"]))
        ages_days.append(0.0)  # just happened

    n_obs = len(eff_weights)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(eff_weights)
    r = np.array(reps_to_failure)
    conf = np.array(confidences)
    recency = _recency_weights(ages_days)
    fit_w = conf * recency

    distinct_w = len(set(round(w, 1) for w in eff_weights))
    tier = "tier1" if n_obs >= MIN_SETS_TIER1 and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1 else "tier2"

    M_lower, M_upper, M_prior = _estimate_M_bounds(eff_weights, reps_to_failure)
    ident = _identifiability_score(eff_weights, reps_to_failure)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, fixed_gamma
    )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit,
        k=k_fit,
        gamma=gamma_fit,
        n_obs=n_obs,
        rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier,
        identifiability=ident,
    )


# ── Exercise menu helpers ──


def get_exercise_freshness(
    session: Session,
) -> list[dict]:
    """Return all exercises ordered by days since last trained.

    Each entry: {exercise_id, name, days_since_trained, allow_heavy_loading,
    load_input_mode, is_bodyweight}
    """
    exercises = session.exec(select(Exercise)).all()
    today = date.today()
    result = []

    for ex in exercises:
        is_bw = (ex.load_input_mode or "external_weight") in BODYWEIGHT_MODES

        # Find most recent set for this exercise
        stmt = (
            select(WorkoutSession.date)
            .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
            .where(WorkoutSet.exercise_id == ex.id)
            .order_by(WorkoutSession.date.desc())
            .limit(1)
        )
        row = session.exec(stmt).first()
        days_since = (today - row).days if row else None

        # Count recent RPE sets (for data quality indicator)
        cutoff = today - timedelta(days=30)
        rpe_stmt = (
            select(WorkoutSet)
            .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
            .where(
                WorkoutSet.exercise_id == ex.id,
                WorkoutSet.rpe.is_not(None),
                WorkoutSession.date >= cutoff,
            )
        )
        rpe_count = len(session.exec(rpe_stmt).all())

        has_curve = not is_bw and rpe_count >= MIN_SETS_TIER2

        result.append({
            "exercise_id": ex.id,
            "name": ex.name,
            "days_since_trained": days_since,
            "allow_heavy_loading": ex.allow_heavy_loading,
            "load_input_mode": ex.load_input_mode or "external_weight",
            "is_bodyweight": is_bw,
            "recent_rpe_sets": rpe_count,
            "has_curve_fit": has_curve,
        })

    # Sort: never-trained first (None), then by most days since trained
    result.sort(key=lambda x: (x["days_since_trained"] is not None,
                               -(x["days_since_trained"] or 0)))
    return result


def get_bodyweight_suggestion(
    exercise_id: int, session: Session
) -> dict:
    """Get a fixed-rep suggestion for a bodyweight exercise based on recent history."""
    cutoff = date.today() - timedelta(days=30)
    stmt = (
        select(WorkoutSet.reps)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.reps.is_not(None),
            WorkoutSet.reps > 0,
            WorkoutSession.date >= cutoff,
        )
        .order_by(WorkoutSession.date.desc())
        .limit(20)
    )
    recent_reps = session.exec(stmt).all()

    if recent_reps:
        median_reps = int(np.median(recent_reps))
    else:
        median_reps = 15  # sensible default

    return {
        "sets": 3,
        "reps_per_set": median_reps,
        "notes": "Non-progressive: fixed rep target",
    }


# ── Internal helpers ──


def _entered_to_effective(
    exercise: Exercise, entered_weight: float, bodyweight_lb: float
) -> float:
    """Convert entered weight to effective weight (inverse of entered_weight_for_effective_weight)."""
    mode = exercise.load_input_mode or "external_weight"
    multiplier = exercise.external_load_multiplier or 1.0
    if multiplier <= 0:
        multiplier = 1.0
    bw_component = bodyweight_lb * (exercise.bodyweight_fraction or 0.0)

    if mode == "bodyweight":
        return bw_component
    if mode == "mixed":
        return entered_weight * multiplier + bw_component
    if mode == "assisted_bodyweight":
        return max(0.0, bw_component - entered_weight * multiplier)
    if mode == "carry":
        return entered_weight * multiplier
    return entered_weight * multiplier


def get_max_recent_entered_weight(
    exercise_id: int, session: Session, days: int = 90
) -> float | None:
    """Get the maximum entered weight used for this exercise recently."""
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(WorkoutSet.weight)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.weight.is_not(None),
            WorkoutSet.weight > 0,
            WorkoutSession.date >= cutoff,
        )
        .order_by(WorkoutSet.weight.desc())
        .limit(1)
    )
    row = session.exec(stmt).first()
    return float(row) if row is not None else None
