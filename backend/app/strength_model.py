"""Strength-curve model: r_fresh(W) = k * (M/(W + delta) - 1)^gamma.

Fits the fresh-set strength curve from recent RPE data and provides
weight/rep prescription for progressive-overload workouts.

The ``delta`` parameter (added in the v4 model) shifts the asymptote left so
that low/zero weights map to a finite rep count, matching the biological
reality that an athlete cannot perform an unbounded number of reps with a
near-zero load. ``delta = 0`` recovers the v2 form ``k * (M/W - 1)^gamma``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np
from scipy.optimize import minimize
from scipy.stats import ttest_ind
from sqlmodel import Session, select

from app.config import user_today
from app.exercise_groups import get_exercise_group
from app.exercise_loads import (
    bodyweight_by_date,
    effective_weight,
    entered_weight_for_effective_weight,
    latest_bodyweight,
    supports_strength_estimate,
)
from app.models import Exercise, ExerciseTissue, Tissue, WeightLog, WorkoutSession, WorkoutSet
from app.units import (
    effective_to_entered_lb as _effective_to_entered,
)
from app.units import (
    entered_to_effective_lb as _entered_to_effective,
)
from app.units import (
    metric_for as _metric_for,
)
from app.units import (
    reps_done_to_rtf as _reps_done_to_rtf,
)
from app.units import (
    rpe_to_rir as _rpe_to_rir,
)
from app.units import (
    rtf_to_reps_done as _rtf_to_reps_done,
)

BODYWEIGHT_MODES = {"bodyweight", "assisted_bodyweight"}

# Default metric used by _curve_dict when no exercise is passed (e.g. tests).
_METRIC_REPS = _metric_for(Exercise(name="", set_metric_mode="reps"))


def _set_endurance(ws: WorkoutSet) -> float | None:
    """Endurance-to-failure value for a set, in the exercise's native unit."""
    if ws.endurance_value is not None:
        return float(ws.endurance_value)
    return None


def _obs_endurance(obs: dict) -> float | None:
    """Pull endurance value from a prior_sets/new_obs dict.

    Accepts the canonical ``endurance_value`` key or the legacy ``reps`` key
    (which the frontend still posts as a wire-compat alias).
    """
    val = obs.get("endurance_value")
    if val is None:
        val = obs.get("reps")
    return None if val is None else float(val)

# Default class prior for gamma — physiological cap: concave-down near max
DEFAULT_GAMMA = 0.9

# Gamma regularization: penalizes deviation from prior shape
GAMMA_PRIOR = 0.9
GAMMA_REG_LAMBDA = 3.0  # per-observation strength (scaled by mean fit_weight)

# M regularization: fixed strength (no longer scaled by identifiability)
M_REG_LAMBDA = 15.0

# Session refit: same-day observations get boosted to this share of total weight
SESSION_TARGET_SHARE = 0.70

# Minimum qualifying sets for curve fitting
MIN_SETS_TIER1 = 5  # full 3-param (M, k, gamma)
MIN_SETS_TIER2 = 3  # 2-param (M, k) with fixed gamma
MIN_DISTINCT_WEIGHTS_TIER1 = 2

# RPE floor: sets below this are too far from failure to inform max estimation
MIN_RPE_FOR_FIT = 7.0

# Gamma bounds: physiological range (concave-down near max, >0 for valid curve)
GAMMA_MIN = 0.01
GAMMA_MAX = 0.9

# T-test significance level for dropping stale sessions
TTEST_ALPHA = 0.05


@dataclass
class CurveFit:
    """Result of fitting r_fresh(W) = k * (M/(W + delta) - 1)^gamma.

    The parameters (M, k) are always stored in *effective*-weight space, which
    is the space `fit_curve` / `refit_with_observations` produce. When surfacing
    the fit to the frontend the backend reprojects into entered-weight space
    (see `_curve_dict`) so plot coordinates align with the user's entered
    weights — but the internal `CurveFit` instances in this module are
    uniformly effective-space. `predict_reps` / `solve_weight` assume the
    argument is in the same space as `fit.weight_space`.
    """

    M: float  # estimated 1RM ceiling (effective space)
    k: float  # endurance scaling
    gamma: float  # curve shape exponent
    n_obs: int
    rmse: float
    max_observed_weight: float
    fit_tier: str  # "tier1" or "tier2"
    identifiability: float = 1.0  # 0-1 quality score
    # v4 shifted-form bias: r_fresh(W) = k * (M / (W + delta) - 1) ** gamma.
    # Bounds: delta >= 0, with hard upper of 0.5 * max_observed_weight applied
    # at fit time. delta = 0 reproduces the v2 form exactly so all callers
    # remain backward-compatible with default-constructed CurveFit instances.
    delta: float = 0.0
    # Weight space in which (M, max_observed_weight) are expressed. Backend
    # fitting always produces "effective"; `_curve_dict` ships those same
    # effective-space params to the frontend along with bw_offset/ext_mult,
    # so the frontend can convert entered→effective before evaluating.
    weight_space: Literal["effective", "entered"] = "effective"


@dataclass
class SetPrescription:
    """Prescription for a single set in a progressive workout."""

    set_number: int
    effective_weight: float
    entered_weight: float | None  # what user types (None for bodyweight)
    target_reps: int  # reps to perform (after RIR subtraction)
    target_rpe: float  # RPE to aim for
    r_fail: float  # predicted reps-to-failure at this weight
    acceptable_rep_min: int
    acceptable_rep_max: int


@dataclass
class InflectionResult:
    """Result of checking whether per-set 1RM is declining (fatigue visible)."""

    inflecting: bool  # True if last set 1RM < previous set 1RM
    estimated_1rm: float | None  # average per-set 1RM (if inflecting)
    suggested_set4: SetPrescription | None  # only if not inflecting + heavy


# ── Progressive set schemes ──

HEAVY_SCHEME = [
    # (r_fail_target, rir, target_rpe, expected_actual, rep_min, rep_max)
    (15, 3, 7.0, 12, 9, 15),    # ~12 reps + RIR 3
    (10, 2, 8.0, 8, 6, 10),     # ~8 reps + RIR 2
    (5, 1, 9.0, 4, 3, 5),       # ~4 reps + RIR 1
]

VOLUME_SCHEME = [
    (18, 3, 7.0, 15, 12, 18),   # ~15 reps + RIR 3
    (14, 2, 8.0, 12, 10, 14),   # ~12 reps + RIR 2
    (11, 1, 9.0, 10, 8, 11),    # ~10 reps + RIR 1
]

LIGHT_SCHEME = [
    (21, 3, 7.0, 18, 15, 21),   # ~18 reps + RIR 3 (metabolic failure)
    (17, 2, 8.0, 15, 13, 17),   # ~15 reps + RIR 2
    (13, 1, 9.0, 12, 10, 13),   # ~12 reps + RIR 1
]


# ── Core math ──


def fresh_curve(
    W: float | np.ndarray, M: float, k: float, gamma: float,
    delta: float = 0.0,
) -> float | np.ndarray:
    """r_fresh(W) = k * (M/(W + delta) - 1)^gamma. Returns 0 where ratio <= 0.

    With ``delta = 0`` this is the v2 form ``k * (M/W - 1)^gamma``.
    """
    denom = W + delta
    if isinstance(denom, np.ndarray):
        # Avoid divide-by-zero warnings; ratio is masked off when <=0 anyway.
        safe = np.where(denom > 0, denom, 1.0)
        ratio = M / safe - 1.0
        return np.where((denom > 0) & (ratio > 0), k * np.power(np.maximum(ratio, 0.0), gamma), 0.0)
    if denom <= 0:
        return 0.0
    ratio = M / denom - 1.0
    return k * (ratio**gamma) if ratio > 0 else 0.0


def predict_reps(
    weight: float, fit: CurveFit,
    *, space: Literal["effective", "entered"] = "effective",
) -> float:
    """Predict reps-to-failure at a given weight.

    `space` asserts which weight space the argument is in; it must match
    `fit.weight_space`. Defaults to "effective" because all internal callers
    pass effective weights. Frontend callers should use the reprojected curve
    produced by `_curve_dict` and pass entered weights with `space="entered"`.
    """
    if fit.weight_space != space:
        raise ValueError(
            f"predict_reps called with space={space!r} but fit.weight_space="
            f"{fit.weight_space!r}; weight arguments must match the curve's "
            "stored weight space"
        )
    return float(fresh_curve(weight, fit.M, fit.k, fit.gamma, fit.delta))


def solve_weight(
    target_reps: float, fit: CurveFit,
    *, space: Literal["effective", "entered"] = "effective",
    readiness_beta: float = 0.0,
) -> float:
    """Invert the curve: find weight W where r_fresh(W) = target_reps.

    Returns weight in `fit.weight_space` (asserted to equal `space`).

    With a non-zero ``readiness_beta`` the target is divided by ``exp(β)``
    before inversion, so a positive β (strong day) prescribes heavier load
    and a negative β (recovery day) prescribes lighter load. Default β=0
    reproduces the v2 behavior exactly.

    W = M / (1 + (effective_target / k)^(1/gamma)) - delta
    """
    if fit.weight_space != space:
        raise ValueError(
            f"solve_weight called with space={space!r} but fit.weight_space="
            f"{fit.weight_space!r}; returned weight must match caller's space"
        )
    effective_target = (
        target_reps / math.exp(readiness_beta) if readiness_beta else target_reps
    )
    if effective_target <= 0 or fit.k <= 0:
        return max(0.0, fit.M * 0.95 - fit.delta)
    ratio = (effective_target / fit.k) ** (1.0 / fit.gamma)
    return max(0.0, fit.M / (1.0 + ratio) - fit.delta)


# ── RPE confidence and recency ──


def _rpe_confidence(rpe: float) -> float:
    """Higher confidence for sets closer to failure."""
    rir = _rpe_to_rir(rpe)
    return max(0.2, math.exp(-0.25 * rir))


def _recency_weights(
    ages_days: list[float], half_life_days: float = 30.0
) -> np.ndarray:
    """Exponential recency weighting: recent sets count more."""
    arr = np.array(ages_days, dtype=float)
    return np.exp(-np.log(2) * arr / half_life_days)


def _filter_stale_sessions(
    eff_weights: list[float],
    reps_to_failure: list[float],
    confidences: list[float],
    ages_days: list[float],
) -> tuple[list[float], list[float], list[float], list[float], int]:
    """Drop sets from sessions whose strength level is statistically different.

    Groups observations by session age (days), computes per-set Brzycki 1RM,
    and uses Welch's t-test to compare each older session against the most
    recent one. Sessions with p < TTEST_ALPHA are dropped (significantly
    different strength level, likely stale). Sessions with fewer than 2 sets
    are kept (can't t-test reliably, low impact).

    Returns the four filtered lists plus the number of distinct sessions that
    survived the filter (used by callers for tier demotion decisions).
    """
    if len(eff_weights) < 3:
        n_sessions = len(set(ages_days))
        return eff_weights, reps_to_failure, confidences, ages_days, n_sessions

    # Group observation indices by session age
    by_age: dict[float, list[int]] = defaultdict(list)
    for i, age in enumerate(ages_days):
        by_age[age].append(i)

    sorted_ages = sorted(by_age.keys())
    if len(sorted_ages) < 2:
        return eff_weights, reps_to_failure, confidences, ages_days, len(sorted_ages)

    # Anchor: most recent session's implied 1RM distribution
    anchor_age = sorted_ages[0]
    anchor_1rms = [
        _brzycki_1rm(eff_weights[i], reps_to_failure[i])
        for i in by_age[anchor_age]
    ]

    keep_indices = list(by_age[anchor_age])  # always keep most recent
    kept_ages = {anchor_age}

    for age in sorted_ages[1:]:
        indices = by_age[age]
        session_1rms = [
            _brzycki_1rm(eff_weights[i], reps_to_failure[i])
            for i in indices
        ]
        # Only t-test if both sessions have >= 2 data points
        if len(session_1rms) >= 2 and len(anchor_1rms) >= 2:
            _, p = ttest_ind(anchor_1rms, session_1rms, equal_var=False)
            if p < TTEST_ALPHA:
                continue  # drop this session
        keep_indices.extend(indices)
        kept_ages.add(age)

    if len(keep_indices) < MIN_SETS_TIER2:
        # Filtering removed too much data — fall back to unfiltered
        return (eff_weights, reps_to_failure, confidences, ages_days,
                len(sorted_ages))

    keep_indices.sort()
    return (
        [eff_weights[i] for i in keep_indices],
        [reps_to_failure[i] for i in keep_indices],
        [confidences[i] for i in keep_indices],
        [ages_days[i] for i in keep_indices],
        len(kept_ages),
    )


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
    """Weighted least squares loss with Brzycki prior on M and gamma.

    ``delta`` is always part of the optimization vector (last element). When
    ``fixed_gamma`` is set, params=(M, k, delta); otherwise (M, k, gamma, delta).
    """
    if fixed_gamma is not None:
        M, k, delta = params
        gamma = fixed_gamma
    else:
        M, k, gamma, delta = params

    predicted = fresh_curve(W, M, k, gamma, delta)
    residuals = r - predicted
    data_loss = float(np.sum(fit_weights * residuals**2))

    # Regularization scaled by average observation weight for consistency
    avg_fw = float(np.mean(fit_weights)) if len(fit_weights) > 0 else 1.0

    reg_M = 0.0
    if M_prior > 0 and lambda_M > 0:
        reg_M = lambda_M * avg_fw * math.log(M / M_prior) ** 2

    reg_gamma = 0.0
    if fixed_gamma is None and GAMMA_REG_LAMBDA > 0 and gamma > 0:
        reg_gamma = GAMMA_REG_LAMBDA * avg_fw * math.log(gamma / GAMMA_PRIOR) ** 2

    # delta has no regularization: let optimizer pull it to 0 when not
    # identifiable (the bounds [0, 0.5*max_W] prevent pathology).
    return data_loss + reg_M + reg_gamma


def _fit_params(
    W: np.ndarray,
    r: np.ndarray,
    fit_weights: np.ndarray,
    M_lower: float,
    M_upper: float,
    M_prior: float,
    lambda_M: float,
    fixed_gamma: float | None = None,
) -> tuple[float, float, float, float, bool]:
    """Run multi-restart optimization. Returns (M, k, gamma, delta, success)."""
    max_W = float(np.max(W))
    # Cap delta at 50% of the observed weight range. The earlier
    # `max(50.0, max_W * 0.5)` floor allowed delta to balloon to 50 lb on
    # light exercises (e.g. rotator-cuff PT work with max_W ≈ 6-8 lb),
    # making the (M, k, gamma, delta) fit non-identifiable and producing
    # absurd extrapolations at small W (e.g. 30+ reps at 6 lb where v3
    # predicted ~16). Tying the bound to max_W keeps delta on the same
    # scale as the data the curve was trained on. We still enforce a tiny
    # absolute floor so the optimizer has room to move on truly tiny
    # weights (sub-1-lb exercises are rare, but max_W * 0.5 = 0 would
    # collapse to v3 with no exploration).
    delta_upper = max(0.5, max_W * 0.5)
    best_result = None
    best_loss = float("inf")

    gamma_inits = [0.2, 0.5, 0.7, 0.9] if fixed_gamma is None else [None]
    M_factors = [1.1, 1.3, 1.5, 2.0]
    delta_inits = [0.0, max(5.0, max_W * 0.05)]

    for M_factor in M_factors:
        for g_init in gamma_inits:
            for d_init in delta_inits:
                M_init = float(np.clip(max_W * M_factor, M_lower, M_upper))
                k_init = float(np.median(r))
                d0 = float(np.clip(d_init, 0.0, delta_upper))

                if fixed_gamma is not None:
                    x0 = [M_init, k_init, d0]
                    bounds = [
                        (M_lower, M_upper), (0.5, 200.0), (0.0, delta_upper),
                    ]
                else:
                    x0 = [M_init, k_init, g_init, d0]
                    bounds = [
                        (M_lower, M_upper), (0.5, 200.0),
                        (GAMMA_MIN, GAMMA_MAX), (0.0, delta_upper),
                    ]

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
        return (
            float(np.clip(max_W * 1.1, M_lower, M_upper)),
            float(np.median(r)),
            fixed_gamma or DEFAULT_GAMMA,
            0.0,
            False,
        )

    if fixed_gamma is not None:
        M_fit, k_fit, delta_fit = best_result.x
        return (M_fit, k_fit, fixed_gamma, float(delta_fit), True)
    M_fit, k_fit, gamma_fit, delta_fit = best_result.x
    return (M_fit, k_fit, gamma_fit, float(delta_fit), True)


# ── Data loading helpers ──


def _load_recent_sets(
    exercise_id: int, session: Session, days: int,
    *, as_of: date | None = None,
) -> tuple[Exercise | None, list[tuple[WorkoutSet, date]]]:
    """Load exercise and its recent RPE sets.

    When ``as_of`` is provided, the window is anchored on that date (sets on
    dates > as_of are excluded) so we can fit a curve snapshot for a past day.
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return None, []

    # Exclude bodyweight and non-strength exercises at the exercise level
    if (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES:
        return exercise, []
    # Exclude duration-mode exercises — see supports_strength_estimate.
    if (exercise.set_metric_mode or "reps") == "duration":
        return exercise, []

    anchor = as_of if as_of is not None else user_today()
    cutoff = anchor - timedelta(days=days)

    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.rpe.is_not(None),  # RPE-only
            WorkoutSet.endurance_value.is_not(None),
            WorkoutSet.endurance_value > 0,
            WorkoutSession.date >= cutoff,
            WorkoutSession.date <= anchor,
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
    exercise_id: int, session: Session, *, days: int = 30,
    allow_heavy: bool = True, exclude_today: bool = False,
    as_of: date | None = None,
) -> CurveFit | None:
    """Fit the fresh-set strength curve for an exercise using recent RPE data.

    Returns None if insufficient qualifying data (< MIN_SETS_TIER2 RPE sets
    within the last `days` days, or exercise is bodyweight/non-strength).
    When allow_heavy is False, forces tier2 (fixed gamma) regardless of data.
    When exclude_today is True, sets performed today are dropped before fitting;
    this is used to produce the "prior" curve shown alongside today's refit.
    When as_of is provided the "today" reference is that date, and the window
    anchors on it (for historical snapshots of the completed-exercise view).
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days, as_of=as_of)
    if exercise is None or not set_rows:
        return None

    bw_lookup = _load_bodyweight_lookup(session)
    today = as_of if as_of is not None else user_today()

    if exclude_today:
        set_rows = [(ws, d) for (ws, d) in set_rows if d < today]
        if not set_rows:
            return None

    # Build observations
    eff_weights: list[float] = []
    reps_to_failure: list[float] = []
    confidences: list[float] = []
    ages_days: list[float] = []

    for ws, ws_date in set_rows:
        if not supports_strength_estimate(exercise, ws):
            continue
        if ws.rpe is None or ws.rpe < MIN_RPE_FOR_FIT or ws.rpe > 10.0:
            continue

        ew = effective_weight(exercise, ws, bw_lookup, ws_date)
        if ew <= 0:
            continue

        endurance = _set_endurance(ws)
        if endurance is None or endurance <= 0:
            continue

        rir = _rpe_to_rir(ws.rpe)
        r_fail = _reps_done_to_rtf(endurance, rir)

        eff_weights.append(ew)
        reps_to_failure.append(r_fail)
        confidences.append(_rpe_confidence(ws.rpe))
        ages_days.append((today - ws_date).days)

    # Filter stale sessions via t-test
    eff_weights, reps_to_failure, confidences, ages_days, n_sessions_kept = (
        _filter_stale_sessions(
            eff_weights, reps_to_failure, confidences, ages_days,
        )
    )

    n_obs = len(eff_weights)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(eff_weights)
    r = np.array(reps_to_failure)
    conf = np.array(confidences)
    recency = _recency_weights(ages_days)
    fit_w = conf * recency

    # Determine tier — non-heavy exercises always tier2; heavy need sufficient data
    distinct_w = len(set(round(w, 1) for w in eff_weights))
    tier = (
        "tier1"
        if (allow_heavy
            and n_obs >= MIN_SETS_TIER1
            and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1
            and n_sessions_kept >= 2)
        else "tier2"
    )

    # Bounds and regularization
    M_lower, M_upper, M_prior = _estimate_M_bounds(eff_weights, reps_to_failure)
    ident = _identifiability_score(eff_weights, reps_to_failure)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, delta_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, M_REG_LAMBDA, fixed_gamma
    )

    # Compute RMSE
    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit, delta_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit,
        k=k_fit,
        gamma=gamma_fit,
        delta=delta_fit,
        n_obs=n_obs,
        rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier,
        identifiability=ident,
    )


def fit_from_data(
    eff_weights: list[float],
    reps_to_failure: list[float],
    confidences: list[float],
    ages_days: list[float],
    *,
    allow_heavy: bool = True,
) -> CurveFit | None:
    """Fit a strength curve from raw observation arrays (no DB access).

    Runs the full pipeline: t-test filter → tier determination → curve fit.
    Useful for plotting and analysis without needing a DB session.
    When allow_heavy is False, forces tier2 (fixed gamma).
    """
    eff_weights, reps_to_failure, confidences, ages_days, n_sessions_kept = (
        _filter_stale_sessions(eff_weights, reps_to_failure, confidences, ages_days)
    )

    n_obs = len(eff_weights)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(eff_weights)
    r = np.array(reps_to_failure)
    conf = np.array(confidences)
    recency = _recency_weights(ages_days)
    fit_w = conf * recency

    distinct_w = len(set(round(w, 1) for w in eff_weights))
    tier = (
        "tier1"
        if (allow_heavy
            and n_obs >= MIN_SETS_TIER1
            and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1
            and n_sessions_kept >= 2)
        else "tier2"
    )

    M_lower, M_upper, M_prior = _estimate_M_bounds(eff_weights, reps_to_failure)
    ident = _identifiability_score(eff_weights, reps_to_failure)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, delta_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, M_REG_LAMBDA, fixed_gamma
    )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit, delta_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit, k=k_fit, gamma=gamma_fit, delta=delta_fit,
        n_obs=n_obs, rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier, identifiability=ident,
    )


def refit_from_data(
    hist_weights: list[float],
    hist_reps: list[float],
    hist_confs: list[float],
    hist_ages: list[float],
    session_weights: list[float],
    session_reps: list[float],
    session_confs: list[float],
    *,
    allow_heavy: bool = True,
) -> CurveFit | None:
    """Refit combining historical + session data with t-test and session boost.

    Session data (age=0) becomes the t-test anchor. Historical sessions that
    don't match current performance are dropped. No DB access needed.
    When allow_heavy is False, forces tier2 (fixed gamma).
    """
    all_w = hist_weights + session_weights
    all_r = hist_reps + session_reps
    all_c = hist_confs + session_confs
    all_a = hist_ages + [0.0] * len(session_weights)

    all_w, all_r, all_c, all_a, n_sessions_kept = _filter_stale_sessions(
        all_w, all_r, all_c, all_a
    )

    n_obs = len(all_w)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(all_w)
    r = np.array(all_r)
    conf = np.array(all_c)
    recency = _recency_weights(all_a)
    fit_w = conf * recency

    n_session = len(session_weights)
    n_prior = n_obs - n_session
    if n_session > 0 and n_prior > 0:
        prior_total = float(np.sum(fit_w[:n_prior]))
        session_total = float(np.sum(fit_w[n_prior:]))
        if session_total > 0 and prior_total > 0:
            target = SESSION_TARGET_SHARE
            boost = (target * prior_total) / ((1 - target) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior:] *= boost

    distinct_w = len(set(round(w, 1) for w in all_w))
    tier = (
        "tier1"
        if (allow_heavy
            and n_obs >= MIN_SETS_TIER1
            and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1
            and n_sessions_kept >= 2)
        else "tier2"
    )

    M_lower, M_upper, M_prior = _estimate_M_bounds(all_w, all_r)
    ident = _identifiability_score(all_w, all_r)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, delta_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, M_REG_LAMBDA, fixed_gamma
    )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit, delta_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit, k=k_fit, gamma=gamma_fit, delta=delta_fit,
        n_obs=n_obs, rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier, identifiability=ident,
    )


# ── Prescription ──


def plan_progressive_sets(
    fit: CurveFit,
    exercise: Exercise,
    bodyweight_lb: float,
    max_entered_weight: float | None = None,
    *,
    readiness_beta: float = 0.0,
) -> list[SetPrescription]:
    """Generate 3 progressive-overload set prescriptions.

    Uses heavy scheme if exercise.allow_heavy_loading, else light scheme.
    Converts effective weight to entered weight for user display.
    Soft-caps to 125% of max_entered_weight if provided (equipment limit).
    ``readiness_beta`` (v4) scales the target reps by ``exp(β)`` so a strong
    day prescribes heavier load and a recovery day prescribes lighter load.
    """
    scheme = HEAVY_SCHEME if exercise.allow_heavy_loading else LIGHT_SCHEME
    weight_ceiling = max_entered_weight * 1.25 if max_entered_weight else None
    prescriptions: list[SetPrescription] = []

    for i, (r_fail, rir, target_rpe, expected_reps, rep_min, rep_max) in enumerate(scheme):
        ew = solve_weight(r_fail, fit, readiness_beta=readiness_beta)
        entered = entered_weight_for_effective_weight(
            exercise, effective_weight_lb=ew, bodyweight_lb=bodyweight_lb
        )

        # Soft cap: allow overload up to 125% of recent max
        if entered is not None and weight_ceiling is not None:
            entered = min(entered, weight_ceiling)
            # Recompute effective weight from clipped entered weight
            ew = _entered_to_effective(exercise, entered, bodyweight_lb)
            # Recompute expected reps at the clipped weight
            r_fail = predict_reps(ew, fit)
            expected_reps = _rtf_to_reps_done(r_fail, rir)
            rep_min = max(1, expected_reps - 3)
            rep_max = expected_reps + 3

        # Monotonicity guard: later sets should not prescribe more reps
        # than earlier sets at the same or lower weight with higher RPE.
        if prescriptions:
            prev = prescriptions[-1]
            if (entered is not None and prev.entered_weight is not None
                    and entered <= prev.entered_weight
                    and r_fail >= prev.r_fail
                    and target_rpe >= prev.target_rpe):
                entered = prev.entered_weight * 1.1
                ew = _entered_to_effective(exercise, entered, bodyweight_lb)
                r_fail = predict_reps(ew, fit)
                expected_reps = _rtf_to_reps_done(r_fail, rir)
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

    expected_reps = _rtf_to_reps_done(r_fail, rir)

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


def detect_inflection(
    fit: CurveFit,
    session_sets: list[dict],
    exercise: Exercise,
    bodyweight_lb: float,
    max_entered_weight: float | None = None,
    *,
    readiness_beta: float = 0.0,
) -> InflectionResult:
    """Check whether heavy-mode exercise should stop or prescribe another set.

    Requires 3+ session sets.
    session_sets: [{"weight": float, "reps": int, "rpe": float}] (entered weights)

    Single stopping criterion: the last set's effective weight is past the
    concave-down bend of the fitted curve, i.e. ``last_ew > M*(γ+1)/2``. This
    is the exact second-derivative zero of ``r(W) = k*(M/W − 1)^γ`` and is
    only meaningful when γ is constrained (``allow_heavy_loading=True``).

    If we're not yet past the inflection, suggests a heavier set.
    Non-heavy exercises always return inflecting=False with no suggestion.
    """
    if len(session_sets) < 3:
        return InflectionResult(inflecting=False, estimated_1rm=None, suggested_set4=None)

    # Compute per-set 1RM estimates from raw data (model-independent).
    # Only used to report estimated_1rm when we stop — no longer consulted
    # as a stopping criterion (Brzycki 1RM is expected to drift downward
    # across a heavy session as each set refines our estimate, and that
    # drift is not evidence that we've found the real ceiling).
    set_1rms: list[float] = []
    last_ew = 0.0
    for s in session_sets:
        ew = _entered_to_effective(exercise, s["weight"], bodyweight_lb)
        if ew <= 0:
            continue
        rpe = s.get("rpe") or 7.0
        rir = _rpe_to_rir(rpe)
        r_fail = _reps_done_to_rtf(s["reps"], rir)
        denom = max(1.0 - r_fail / 37.0, 0.05)
        est_1rm = ew / denom
        set_1rms.append(est_1rm)
        last_ew = ew

    if len(set_1rms) < 3:
        return InflectionResult(inflecting=False, estimated_1rm=None, suggested_set4=None)

    # Non-heavy exercises: no further escalation
    if not exercise.allow_heavy_loading:
        return InflectionResult(inflecting=False, estimated_1rm=None, suggested_set4=None)

    # Criterion 2: curve inflection — is last set past M*(γ+1)/2?
    inflection_w = fit.M * (fit.gamma + 1) / 2.0
    past_inflection = last_ew > inflection_w

    if past_inflection:
        # We have data in the concave-down region — γ is constrained
        avg_1rm = sum(set_1rms) / len(set_1rms)
        return InflectionResult(
            inflecting=True,
            estimated_1rm=round(avg_1rm, 1),
            suggested_set4=None,
        )

    # Not past either inflection — suggest heavier set
    last_set = session_sets[-1]
    set3_reps = last_set["reps"]
    target_actual = max(1, math.ceil(set3_reps / 2))
    rir = 1
    target_rpe = 10.0 - rir
    target_r_fail = target_actual + rir

    # Account for per-set fatigue when picking weight: fresh curve target_rtf
    # = target_r_fail − β_n so that AFTER fatigue (predicted_actual_rtf =
    # fresh_rtf + β_n) the athlete actually achieves target_r_fail.
    next_set_index = len(session_sets) + 1
    beta_n = _beta_for_set(next_set_index)
    fresh_target_rtf = max(1.0, target_r_fail - beta_n)
    ew = solve_weight(fresh_target_rtf, fit, readiness_beta=readiness_beta)
    entered = entered_weight_for_effective_weight(
        exercise, effective_weight_lb=ew, bodyweight_lb=bodyweight_lb
    )

    # Ensure we go heavier than the last set
    last_entered = last_set["weight"]
    if entered is not None and entered <= last_entered:
        entered = last_entered * 1.1
    if entered is not None and max_entered_weight is not None:
        entered = min(entered, max_entered_weight * 1.25)
    if entered is not None:
        ew = _entered_to_effective(exercise, entered, bodyweight_lb)
        fresh_rtf_at_ew = predict_reps(ew, fit)
        target_r_fail = max(1.0, fresh_rtf_at_ew + beta_n)
        target_actual = _rtf_to_reps_done(target_r_fail, rir)

    return InflectionResult(
        inflecting=False,
        estimated_1rm=None,
        suggested_set4=SetPrescription(
            set_number=len(session_sets) + 1,
            effective_weight=round(ew, 1),
            entered_weight=round(entered, 1) if entered is not None else None,
            target_reps=target_actual,
            target_rpe=target_rpe,
            r_fail=round(target_r_fail, 1),
            acceptable_rep_min=max(1, target_actual - 2),
            acceptable_rep_max=target_actual + 2,
        ),
    )


def prescribe_next_set(
    exercise_id: int,
    session: Session,
    prior_sets: list[dict],
    bodyweight_lb: float,
    actual_weight: float | None = None,
    training_mode: str = "volume",
) -> dict:
    """Prescribe the next set based on completed prior sets.

    prior_sets: [{"weight": float, "reps": int, "rpe": float}]
    training_mode: "heavy" or "volume" (controls scheme + stopping)
    Returns dict with: has_curve, next_set, exercise_complete, inflection_detected, estimated_1rm, training_mode
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return {"has_curve": False, "error": "Exercise not found"}

    metric = _metric_for(exercise)
    is_bw = (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES
    if is_bw:
        suggestion = get_bodyweight_suggestion(exercise_id, session)
        n_done = len(prior_sets)
        target_sets = suggestion.get("sets", 3)
        target_value = suggestion.get(
            "endurance_per_set", suggestion.get("reps_per_set", 15)
        )
        return {
            "has_curve": False,
            "is_bodyweight": True,
            "metric_kind": metric.kind,
            "display_unit": metric.display_unit,
            "suggestion": suggestion,
            "exercise_complete": n_done >= target_sets,
            "next_set": None if n_done >= target_sets else {
                "set_number": n_done + 1,
                "proposed_weight": 0,
                "target_reps": target_value,
                "target_endurance": target_value,
                "target_rir": None,
                "metric_kind": metric.kind,
                "display_unit": metric.display_unit,
            },
        }

    # Duration-mode exercises (e.g., weighted plank) are non-progressive:
    # the curve doesn't reflect the user's actual practice (they hold a
    # constant weight for a constant time). Prescribe the median historical
    # (weight, seconds) pair so the recommendation matches the lifestyle.
    if metric.kind == "duration":
        suggestion = get_duration_suggestion(exercise_id, session)
        n_done = len(prior_sets)
        target_sets = suggestion.get("sets", 3)
        target_secs = suggestion.get("endurance_per_set", 30)
        target_weight = suggestion.get("weight", 0.0)
        target_rir = suggestion.get("rir_target", 2)
        return {
            "has_curve": False,
            "mode": "duration_fixed",
            "metric_kind": metric.kind,
            "display_unit": metric.display_unit,
            "suggestion": suggestion,
            "exercise_complete": n_done >= target_sets,
            "next_set": None if n_done >= target_sets else {
                "set_number": n_done + 1,
                "proposed_weight": target_weight,
                "proposed_entered_weight_lb": target_weight,
                "target_reps": target_secs,
                "target_endurance": target_secs,
                "target_rir": target_rir,
                "metric_kind": metric.kind,
                "display_unit": metric.display_unit,
            },
        }

    # Burnout mode: a single AMRAP set at ~½ recent max to anchor the light
    # side of the strength curve. Independent of curve fit / bootstrap state
    # — the whole point is "do one easy-weight set to failure to inform the
    # left side of the curve". Skipped for bodyweight exercises (handled
    # above) and exercises with no recent history (no max to halve).
    if training_mode == "burnout":
        n_done = len(prior_sets)
        if n_done >= 1:
            return {
                "has_curve": False,
                "mode": "burnout",
                "exercise_complete": True,
                "next_set": None,
                "training_mode": training_mode,
                "observations": _build_observations(exercise_id, session),
            }
        max_w = get_max_recent_entered_weight(exercise_id, session)
        if max_w is None or max_w <= 0:
            # No history at all — fall through to the normal planner.
            pass
        else:
            BURNOUT_TARGET_REPS = 30.0
            target_entered: float | None = None
            fit = fit_curve(exercise_id, session)
            if fit is not None:
                eff_at_target = solve_weight(BURNOUT_TARGET_REPS, fit)
                entered_from_curve = _effective_to_entered(
                    exercise, eff_at_target, bodyweight_lb
                )
                if entered_from_curve is not None and entered_from_curve > 0:
                    target_entered = entered_from_curve
            if target_entered is None:
                target_entered = max_w / 2.0
            target_entered = min(target_entered, max_w / 2.0)
            target_entered = max(0.0, _snap_to_grid(target_entered))
            target_effective = _entered_to_effective(
                exercise, target_entered, bodyweight_lb
            )
            # Predicted RTF at the chosen weight; with target_rir=0 the target
            # reps_done equals RTF. Used to render the spark on the curve.
            if fit is not None and target_effective > 0:
                predicted_rtf = float(predict_reps(target_effective, fit))
            else:
                predicted_rtf = BURNOUT_TARGET_REPS
            target_reps_done = max(1, int(round(predicted_rtf)))
            curve_block = (
                _curve_dict(fit, exercise, bodyweight_lb) if fit is not None else None
            )
            prior_fit = fit_curve(exercise_id, session, exclude_today=True)
            curve_prior_block = (
                _curve_dict(prior_fit, exercise, bodyweight_lb)
                if prior_fit is not None
                else None
            )
            return {
                "has_curve": fit is not None,
                "mode": "burnout",
                "exercise_complete": False,
                "training_mode": training_mode,
                "next_set": {
                    "set_number": 1,
                    "proposed_weight": round(target_entered, 1),
                    "effective_weight": round(target_effective, 1),
                    "target_reps": target_reps_done,
                    "target_rpe": 10.0,
                    "target_rir": 0,
                    "r_fail": round(predicted_rtf, 1),
                    "acceptable_rep_min": 1,
                    "acceptable_rep_max": None,
                    "proposed_entered_weight_lb": round(target_entered, 1),
                    "effective_weight_lb": round(target_effective, 1),
                    "target_reps_done": target_reps_done,
                    "r_fail_rtf": round(predicted_rtf, 1),
                    "amrap": True,
                    "instructions": (
                        "Burnout: do as many reps as you can until RIR=0."
                    ),
                },
                "curve": curve_block,
                "curve_prior": curve_prior_block,
                "observations": _build_observations(exercise_id, session),
            }

    # Bootstrap mode: insufficient recent RPE history for a curve fit. Guide
    # the athlete through anchor + probing sets using the universal prior
    # curve. Once they've logged enough sets across enough distinct weights,
    # is_bootstrap() returns False and the normal planner takes over.
    if is_bootstrap(exercise_id, session):
        n_done = len(prior_sets)
        # Cap at 3 bootstrap sets per session; beyond that the exercise
        # should have data for a real fit next session (or at the next
        # planner call within this session after the 3rd set is logged).
        if n_done >= 3:
            return {
                "has_curve": False,
                "mode": "bootstrap",
                "exercise_complete": True,
                "next_set": None,
                "bootstrap": {
                    "stage": 3, "total_stages": 3,
                    "explanation": "Calibration complete.",
                },
                "observations": _build_observations(exercise_id, session),
            }
        return bootstrap_prescription(
            exercise_id, session, prior_sets, bodyweight_lb,
        )

    # Fit or refit curve — allow_heavy keyed off exercise capability (not mode)
    # so volume mode on heavy exercises still gets a trained curve
    allow_heavy = exercise.allow_heavy_loading
    if prior_sets:
        fit = refit_with_observations(exercise_id, session, prior_sets,
                                      allow_heavy=allow_heavy)
    else:
        fit = fit_curve(exercise_id, session, allow_heavy=allow_heavy)

    if fit is None:
        last_weight = _starting_weight_from_history(exercise_id, session)
        return {
            "has_curve": False,
            "fallback_weight": last_weight,
            "message": "Insufficient RPE data for curve fit.",
            "training_mode": training_mode,
            "scheme": _bootstrap_scheme_dict(exercise, training_mode, len(prior_sets)),
            "observations": _build_observations(exercise_id, session),
        }

    n_done = len(prior_sets)
    curve_block = _curve_dict(fit, exercise, bodyweight_lb)
    observations = _build_observations(exercise_id, session)
    # Prior-day curve (excludes today's sets) — for the post-complete display.
    prior_fit = fit_curve(exercise_id, session, allow_heavy=allow_heavy, exclude_today=True)
    curve_prior_block = (
        _curve_dict(prior_fit, exercise, bodyweight_lb)
        if prior_fit is not None
        else None
    )

    def _with_curve(d: dict) -> dict:
        d["curve"] = curve_block
        d["curve_prior"] = curve_prior_block
        d["observations"] = observations
        return d

    is_heavy_mode = training_mode == "heavy" and exercise.allow_heavy_loading
    if is_heavy_mode:
        scheme = HEAVY_SCHEME
    elif exercise.allow_heavy_loading:
        scheme = VOLUME_SCHEME
    else:
        scheme = LIGHT_SCHEME
    max_weight = get_max_recent_entered_weight(exercise_id, session)

    # After 3+ sets: only heavy mode gets inflection checks + set 4+
    if n_done >= 3:
        if is_heavy_mode:
            # Stop when the most recently logged set's measured RTF (reps + RIR)
            # is at or near failure on a heavy load — there's no point prescribing
            # another heavier set when capacity has clearly been reached.
            last_set = prior_sets[-1]
            last_rir = _rpe_to_rir(last_set.get("rpe") or 7.0)
            last_endurance = _obs_endurance(last_set) or 0
            last_rtf = float(last_endurance) + last_rir
            if last_rtf <= HEAVY_LOW_RTF_STOP:
                return _with_curve({
                    "has_curve": True,
                    "exercise_complete": True,
                    "inflection_detected": False,
                    "estimated_1rm": round(fit.M, 1),
                    "next_set": None,
                    "training_mode": training_mode,
                })
            # Hard cap: never prescribe more than HEAVY_MAX_SETS sets even if the
            # curve inflection criterion hasn't triggered. Protects against
            # runaway escalation when the athlete's curve is unusually flat.
            if n_done >= HEAVY_MAX_SETS:
                return _with_curve({
                    "has_curve": True,
                    "exercise_complete": True,
                    "inflection_detected": False,
                    "estimated_1rm": round(fit.M, 1),
                    "next_set": None,
                    "training_mode": training_mode,
                })
            inflection = detect_inflection(
                fit, prior_sets, exercise, bodyweight_lb, max_weight,
            )
            result = {
                "has_curve": True,
                "fit_tier": fit.fit_tier,
                "n_obs": fit.n_obs,
                "inflection_detected": inflection.inflecting,
                "estimated_1rm": inflection.estimated_1rm,
                "training_mode": training_mode,
            }
            if inflection.inflecting:
                result["exercise_complete"] = True
                result["next_set"] = None
            elif inflection.suggested_set4:
                result["exercise_complete"] = False
                result["next_set"] = _set_prescription_dict(inflection.suggested_set4)
            else:
                result["exercise_complete"] = True
                result["next_set"] = None
            return _with_curve(result)
        else:
            # Volume/light mode: hard stop at 3 sets
            return _with_curve({
                "has_curve": True,
                "exercise_complete": True,
                "inflection_detected": None,
                "estimated_1rm": round(fit.M, 1),
                "next_set": None,
                "training_mode": training_mode,
            })

    # Sets 1-3: prescribe from scheme
    if n_done >= len(scheme):
        return _with_curve({
            "has_curve": True,
            "exercise_complete": True,
            "inflection_detected": None,
            "estimated_1rm": round(fit.M, 1),
            "next_set": None,
            "training_mode": training_mode,
        })

    set_idx = n_done  # 0-indexed
    r_fail_target, rir, target_rpe, expected_reps, rep_min, rep_max = scheme[set_idx]

    # If user provided actual weight, adjust for that
    if actual_weight is not None:
        prescription = adjust_prescription(
            fit, exercise, actual_weight, bodyweight_lb,
            set_idx + 1, exercise.allow_heavy_loading,
        )
        return _with_curve({
            "has_curve": True,
            "exercise_complete": False,
            "inflection_detected": None,
            "estimated_1rm": None,
            "next_set": _set_prescription_dict(prescription),
            "training_mode": training_mode,
        })

    # Standard prescription
    # NOTE: v4 readiness β is intentionally NOT applied here. The session
    # curve refit (via refit_with_observations) already absorbs today's
    # strong/weak signal into M/k/γ. Multiplying again by exp(β) would
    # double-count and risk self-fulfilling drift. β is purely a display
    # signal in v4.0; revisit if shadow-validation shows it should drive
    # prescription.
    ew = solve_weight(r_fail_target, fit)
    entered = entered_weight_for_effective_weight(
        exercise, effective_weight_lb=ew, bodyweight_lb=bodyweight_lb
    )

    # Soft cap: allow up to 125% of the heaviest weight completed *in this
    # session*. This protects against runaway escalation on set 2+ if the
    # curve happens to prescribe much heavier than the athlete has already
    # handled today. For set 1, we trust the curve: clipping to 1.25× of
    # some stale 90-day max underweighted exercises whose curve now says a
    # heavier load is appropriate (e.g. athlete was historically warming
    # up an exercise they're now ready to push).
    if prior_sets:
        session_max = max(
            (s["weight"] for s in prior_sets if s.get("weight")),
            default=0,
        )
        weight_ceiling = session_max * 1.25 if session_max > 0 else None
    else:
        weight_ceiling = None

    if entered is not None and weight_ceiling is not None:
        entered = min(entered, weight_ceiling)

    if entered is not None:
        ew = _entered_to_effective(exercise, entered, bodyweight_lb)
        r_fail_target = predict_reps(ew, fit)
        expected_reps = max(1, round(r_fail_target - rir))

        # Monotonicity guard: a later set must not prescribe more reps
        # at the same-or-lower weight with a higher target RPE than the
        # previous completed set.
        if prior_sets:
            last = prior_sets[-1]
            last_rtf = last["reps"] + (10.0 - last["rpe"])
            if (entered <= last["weight"]
                    and r_fail_target >= last_rtf
                    and target_rpe >= last.get("rpe", 0)):
                # Clipping created a nonsensical prescription; bump
                # weight above the last set instead.
                entered = last["weight"] * 1.1
                ew = _entered_to_effective(exercise, entered, bodyweight_lb)
                r_fail_target = predict_reps(ew, fit)
                expected_reps = max(1, round(r_fail_target - rir))

        rep_min = max(1, expected_reps - 3)
        rep_max = expected_reps + 3

    prescription = SetPrescription(
        set_number=set_idx + 1,
        effective_weight=round(ew, 1),
        entered_weight=round(entered, 1) if entered is not None else None,
        target_reps=expected_reps,
        target_rpe=target_rpe,
        r_fail=round(r_fail_target, 1),
        acceptable_rep_min=rep_min,
        acceptable_rep_max=rep_max,
    )

    return _with_curve({
        "has_curve": True,
        "exercise_complete": False,
        "inflection_detected": None,
        "estimated_1rm": None,
        "next_set": _set_prescription_dict(prescription),
        "training_mode": training_mode,
    })


def _set_prescription_dict(p: SetPrescription) -> dict:
    """Convert SetPrescription to API-friendly dict.

    Emits BOTH legacy field names and new space-explicit names so existing
    consumers keep working while new consumers can prefer the explicit names.
    New names:

    - ``proposed_entered_weight_lb`` — entered weight to display to athlete
    - ``effective_weight_lb`` — effective-space weight (body load)
    - ``target_reps_done`` — reps the athlete should perform
    - ``r_fail_rtf`` — reps-to-failure prediction in rtf space
    """
    target_rir = round(10.0 - p.target_rpe)
    return {
        "set_number": p.set_number,
        # Legacy names (unchanged).
        "proposed_weight": p.entered_weight,
        "effective_weight": p.effective_weight,
        "target_reps": p.target_reps,
        "target_rpe": p.target_rpe,
        "target_rir": target_rir,
        "r_fail": p.r_fail,
        "acceptable_rep_min": p.acceptable_rep_min,
        "acceptable_rep_max": p.acceptable_rep_max,
        # New space-explicit names (additive; consumers may prefer these).
        "proposed_entered_weight_lb": p.entered_weight,
        "effective_weight_lb": p.effective_weight,
        "target_reps_done": p.target_reps,
        "r_fail_rtf": p.r_fail,
    }


def _curve_dict(fit: CurveFit, exercise: Exercise | None = None,
                bodyweight_lb: float = 0.0) -> dict:
    """Expose fit parameters for the frontend curve chart.

    The curve is fit in *effective*-weight space and we ship it that way:
    ``(M, k, γ)`` stay in effective space; we add ``bw_offset`` and
    ``ext_mult`` so the frontend can convert entered→effective before
    evaluating. This is mathematically exact (no reprojection distortion)
    for all load modes, including ``mixed`` where a pure-parametric shift
    of ``M`` into entered space would warp the curve's shape because
    ``k·(M/W − 1)^γ`` is not invariant under an affine offset of W.

    ``max_observed_weight`` stays in **entered** space because the chart's
    X axis is entered-weight. ``M`` is in **effective** space and is NOT
    directly comparable to entered-space quantities — use the frontend
    helpers or convert via ``W_eff = W_ent * ext_mult + bw_offset``.
    """
    mult = 1.0
    bw_offset = 0.0
    max_ew_entered = fit.max_observed_weight
    metric = _metric_for(exercise) if exercise is not None else _METRIC_REPS
    if exercise is not None:
        m = exercise.external_load_multiplier or 1.0
        if m > 0:
            mult = float(m)
        bw_offset = float(bodyweight_lb) * float(exercise.bodyweight_fraction or 0.0)
        # max_observed_weight on the fit lives in effective space; convert
        # back to entered-space so the chart X-domain stays correct.
        max_ew_entered = max(0.0, (fit.max_observed_weight - bw_offset) / mult)
    return {
        "M": round(float(fit.M), 2),
        "k": round(float(fit.k), 4),
        "gamma": round(float(fit.gamma), 4),
        "delta": round(float(fit.delta), 4),
        "fit_tier": fit.fit_tier,
        "n_obs": fit.n_obs,
        # Entered-space: for chart X-axis domain.
        "max_observed_weight": round(float(max_ew_entered), 2),
        # M/k/γ live in effective space; evaluator must convert entered→eff.
        "weight_space": "effective",
        "x_axis_space": "entered",
        "bw_offset": round(float(bw_offset), 4),
        "ext_mult": round(float(mult), 4),
        # Y-axis units (reps / s / steps). The fit math is unit-agnostic;
        # this lets the frontend label the chart and format prescriptions.
        "metric_kind": metric.kind,
        "display_unit": metric.display_unit,
    }


def _build_observations(
    exercise_id: int, session: Session, *, days: int = 30, limit: int = 40,
    as_of: date | None = None, exclude_on_date: bool = False,
) -> list[dict]:
    """Return recent RPE observations for the curve chart's gray dots.

    When ``as_of`` is set, the window anchors on that date (for historical
    snapshots). When ``exclude_on_date`` is also True, sets on the anchor
    date are omitted so the caller can draw them separately (e.g. as the
    colored per-set sparks in the completed view).
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days, as_of=as_of)
    if exercise is None:
        return []
    anchor = as_of if as_of is not None else user_today()
    metric = _metric_for(exercise)
    out: list[dict] = []
    for ws, ws_date in set_rows[:limit]:
        endurance = _set_endurance(ws)
        if ws.weight is None or endurance is None or ws.rpe is None:
            continue
        if exclude_on_date and ws_date == anchor:
            continue
        # Cast to int for int-valued metrics so JSON stays clean for the
        # frontend's existing reps-as-int code path.
        endurance_out = int(endurance) if metric.int_valued else round(endurance, 2)
        out.append({
            "weight": round(float(ws.weight), 2),
            "reps": endurance_out,
            "endurance_value": endurance_out,
            "rir": round(10.0 - float(ws.rpe)),
            "age_days": max(0, (anchor - ws_date).days),
        })
    return out


def curve_snapshot_for_date(
    exercise_id: int, session: Session, on_date: date,
    *, bodyweight_lb: float = 0.0,
) -> dict:
    """Fit the strength curve as of ``on_date`` for the completed-exercise view.

    Returns a dict with ``has_curve``, ``curve`` (fit up to & including on_date),
    ``curve_prior`` (fit strictly before on_date), and ``observations`` (RPE
    sets in the 30-day window before on_date, excluding the anchor day).
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return {"has_curve": False, "observations": []}
    is_bw = (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES
    if is_bw:
        return {"has_curve": False, "is_bodyweight": True, "observations": []}

    allow_heavy = exercise.allow_heavy_loading
    fit = fit_curve(exercise_id, session, allow_heavy=allow_heavy, as_of=on_date)
    prior_fit = fit_curve(
        exercise_id, session, allow_heavy=allow_heavy,
        as_of=on_date, exclude_today=True,
    )
    observations = _build_observations(
        exercise_id, session, as_of=on_date, exclude_on_date=True,
    )
    return {
        "has_curve": fit is not None,
        "curve": _curve_dict(fit, exercise, bodyweight_lb) if fit is not None else None,
        "curve_prior": (
            _curve_dict(prior_fit, exercise, bodyweight_lb)
            if prior_fit is not None
            else None
        ),
        "observations": observations,
    }


# ── Bootstrap mode (cold-start per-exercise guided calibration) ──
#
# An exercise enters bootstrap when recent RPE history is below the fitting
# threshold: fewer than MIN_SETS_TIER2 RPE sets, or fewer than
# MIN_DISTINCT_WEIGHTS_TIER1 distinct weights. The athlete is guided through
# an anchor set (free entry) and then 1–2 probing sets computed from a shared
# universal curve, and the exercise exits bootstrap once the threshold is met.

# Universal "prior" curve used by the bootstrap to invert observations into an
# M estimate and back into a prescribed weight. Keeping the same (k, γ) for
# both directions is critical — mixing Brzycki-in / curve-out causes the
# prescribed weight to bias upward by ~30% for high-rep (light) scenarios.
BOOT_K = 20.0
BOOT_GAMMA = 0.9

# Target rtf (reps-to-failure) ladder for bootstrap probes. Absolute values,
# not geometric step-downs — after ~3 points we have enough to fit, and
# perpetual geometric decay marches toward failure far too fast.
_BOOT_TARGETS_HEAVY = (18, 11, 6)  # ~15/RIR3, ~9/RIR2, ~4/RIR2
_BOOT_TARGETS_LIGHT = (23, 14, 10)  # ~20/RIR3, ~12/RIR2, ~8/RIR2
_BOOT_RIR = (3, 2, 1)

# Safety clamp on W_next / W_prev. State-dependent: severe overshoot (the
# athlete hit failure on a guess far too heavy) allows a larger drop so the
# system can recover in one step rather than chasing a bad anchor.
_BOOT_CLAMP_NORMAL = (0.80, 1.35)
_BOOT_CLAMP_SEVERE_OVER = (0.50, 1.35)
# When the previous set was way too light (lots of reps at low RPE), trust the
# M-guess and let the prescription jump substantially. The universal curve's
# inversion from (rtf, weight) is reasonably accurate, and refusing to step
# up more than 1.55× means we stall on drastically underloaded anchor sets
# (e.g. a 7 lb dumbbell done for 20 reps at RPE 5 should jump to ~17.5 lb,
# not be clipped to ~10 lb).
_BOOT_CLAMP_SEVERE_UNDER = (0.80, 3.00)

# How many reps short of target RTF before we treat the prev set as a "miss".
# A miss means the next bootstrap prescription will not step up; it caps at
# the previous weight so the athlete can re-attempt at the same load.
_BOOT_MISS_THRESHOLD = 2.0

# rtf cap — Brzycki-style 1RM explodes as rtf → 37, and the universal curve
# gets twitchy at very high rtf too. Cap the inferred rtf for M estimation.
_BOOT_RTF_CAP = 30.0

# How many days to look back for prior bootstrap observations.
_BOOT_HISTORY_DAYS = 90


def _invert_universal_curve(rtf: float, W: float) -> float:
    """Solve M from a single (W, rtf) observation assuming bootstrap priors.

    From rtf = k * (M/W - 1)^γ  →  M = W * (1 + (rtf/k)^(1/γ))
    """
    if rtf <= 0 or W <= 0:
        return W * 1.5
    rtf_capped = min(rtf, _BOOT_RTF_CAP)
    ratio = (rtf_capped / BOOT_K) ** (1.0 / BOOT_GAMMA)
    return W * (1.0 + ratio)


def _prescribe_universal_curve(M: float, target_rtf: float) -> float:
    """Invert the universal curve for W given an M estimate and target rtf."""
    if M <= 0 or target_rtf <= 0:
        return 0.0
    ratio = (target_rtf / BOOT_K) ** (1.0 / BOOT_GAMMA)
    return M / (1.0 + ratio)


# Bootstrap accepts a lower RPE floor than the curve fit — a too-easy set
# (RPE 5-6, athlete still has 4-5 in the tank) is precisely what we need
# for severe-undershoot correction.
_BOOT_MIN_RPE = 5.0


def _load_bootstrap_observations(
    exercise_id: int, session: Session, *, days: int = _BOOT_HISTORY_DAYS,
) -> list[dict]:
    """Load RPE-qualifying sets across the trailing window for an exercise.

    Includes both historical sessions and today's session. Only sets with
    weight > 0, reps > 0, and RPE >= _BOOT_MIN_RPE survive.
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days)
    if exercise is None:
        return []
    metric = _metric_for(exercise)
    bw_lookup = _load_bodyweight_lookup(session)
    out: list[dict] = []
    for ws, ws_date in set_rows:
        endurance = _set_endurance(ws)
        if ws.rpe is None or endurance is None or endurance <= 0:
            continue
        if ws.rpe < _BOOT_MIN_RPE or ws.rpe > 10.0:
            continue
        if not supports_strength_estimate(exercise, ws):
            continue
        ew = effective_weight(exercise, ws, bw_lookup, ws_date)
        if ew <= 0:
            continue
        rir = 10.0 - float(ws.rpe)
        endurance_out = int(endurance) if metric.int_valued else round(endurance, 2)
        out.append({
            "weight": round(float(ws.weight), 2),
            "effective_weight": round(ew, 2),
            "reps": endurance_out,
            "endurance_value": endurance_out,
            "rpe": round(float(ws.rpe), 1),
            "rtf": round(endurance + rir, 2),
            "date": ws_date,
            "set_order": int(ws.set_order),
        })
    return out


def is_bootstrap(
    exercise_id: int, session: Session, *, days: int = _BOOT_HISTORY_DAYS,
) -> bool:
    """True when the exercise has insufficient RPE history for a fit."""
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return False
    is_bw = (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES
    if is_bw:
        return False
    obs = _load_bootstrap_observations(exercise_id, session, days=days)
    n_sets = len(obs)
    # Exit threshold still uses the curve-fit RPE floor: only RPE>=MIN_RPE_FOR_FIT
    # sets count as "real" training observations toward tier2.
    strong_obs = [o for o in obs if o["rpe"] >= MIN_RPE_FOR_FIT]
    n_strong = len(strong_obs)
    n_strong_weights = len({round(o["effective_weight"], 1) for o in strong_obs})
    return (n_strong < MIN_SETS_TIER2
            or n_strong_weights < MIN_DISTINCT_WEIGHTS_TIER1
            or n_sets < MIN_SETS_TIER2)


def _bootstrap_scheme(exercise: Exercise) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if exercise.allow_heavy_loading:
        return _BOOT_TARGETS_HEAVY, _BOOT_RIR
    return _BOOT_TARGETS_LIGHT, _BOOT_RIR


def _severity_flags(
    prev_obs: dict, prev_target_rtf: float, prev_target_reps: int,
) -> tuple[bool, bool]:
    """Detect severe overshoot / undershoot to widen safety clamps."""
    # Overshoot = weight was too heavy → athlete hit near-failure early.
    severe_over = (
        prev_obs["rpe"] >= 9.0
        and prev_obs["reps"] < max(1, prev_target_reps - 4)
    )
    # Undershoot = weight was too light → many more reps than expected at low RPE.
    severe_under = (
        prev_obs["rpe"] <= 6.5
        and prev_obs["reps"] > prev_target_reps + 3
    )
    return severe_over, severe_under


def _round_to_increment(weight: float, increment: float = 5.0) -> float:
    if increment <= 0:
        return weight
    return round(weight / increment) * increment


def _snap_to_grid(weight: float) -> float:
    """Snap to the same dynamic weight grid the frontend uses.

    Bands:
      0 ≤ w < 15  → integer lbs plus {2.5, 7.5, 12.5}
      15 ≤ w < 100 → 2.5 lb
      w ≥ 100     → 5 lb
    """
    if weight <= 0:
        return 0.0
    if weight < 15:
        candidates = [
            0, 1, 2, 2.5, 3, 4, 5, 6, 7, 7.5, 8, 9, 10, 11, 12, 12.5, 13, 14,
        ]
        return min(candidates, key=lambda c: abs(c - weight))
    if weight < 100:
        return round(weight / 2.5) * 2.5
    return round(weight / 5.0) * 5.0


def _grid_step(weight: float) -> float:
    if weight < 15:
        return 1.0
    if weight < 100:
        return 2.5
    return 5.0


def bootstrap_prescription(
    exercise_id: int,
    session: Session,
    prior_sets: list[dict],
    bodyweight_lb: float,
) -> dict:
    """Return the next bootstrap prescription (mode='bootstrap').

    Shape mirrors ``prescribe_next_set``: a dict with ``has_curve`` (False in
    bootstrap), ``mode``, ``next_set``, ``exercise_complete``, and a
    ``bootstrap`` sub-dict carrying the anchor prompt and explanation.
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return {"has_curve": False, "mode": "bootstrap", "error": "Exercise not found"}

    targets, rirs = _bootstrap_scheme(exercise)
    all_obs = _load_bootstrap_observations(exercise_id, session)
    # Overlay prior_sets from the live session — same shape minus bookkeeping.
    for s in prior_sets:
        w = s.get("weight")
        reps = s.get("reps")
        rpe = s.get("rpe")
        if w is None or reps is None or rpe is None:
            continue
        if rpe < _BOOT_MIN_RPE or rpe > 10.0 or reps <= 0:
            continue
        ew = _entered_to_effective(exercise, float(w), bodyweight_lb)
        if ew <= 0:
            continue
        all_obs.append({
            "weight": float(w), "effective_weight": ew,
            "reps": int(reps), "rpe": float(rpe),
            "rtf": float(reps) + (10.0 - float(rpe)),
            "set_order": s.get("set_order") or (len(all_obs) + 1),
        })

    n_done_session = len(prior_sets)
    set_idx = n_done_session  # 0-based index for the next set
    stage = min(set_idx, len(targets) - 1)
    target_rtf = float(targets[stage])
    rir = int(rirs[stage])
    target_reps = _rtf_to_reps_done(target_rtf, rir)
    target_rpe = 10.0 - rir

    bootstrap_block: dict = {
        "stage": stage,
        "total_stages": len(targets),
        "target_rtf": target_rtf,
        "heavy_capable": bool(exercise.allow_heavy_loading),
    }

    # ── Stage 0: anchor — ask athlete to pick W_1 themselves ──
    if not all_obs:
        # Seed the proposal with the historical mean so the user has a starting
        # point instead of a jarring 0/None or static high default.
        anchor_entered = _starting_weight_from_history(exercise_id, session)
        anchor_effective = (
            _entered_to_effective(exercise, anchor_entered, bodyweight_lb)
            if anchor_entered > 0
            else None
        )
        bootstrap_block["prompt"] = (
            f"Pick a weight you could do for about {target_reps} reps with "
            f"{rir} reps in the tank (RPE {target_rpe:.0f})."
        )
        bootstrap_block["explanation"] = (
            "We're calibrating this exercise. Log 3 sets across ≥2 distinct "
            "weights and we'll take over from here."
        )
        return {
            "has_curve": False,
            "mode": "bootstrap",
            "exercise_complete": False,
            "bootstrap": bootstrap_block,
            "next_set": {
                "set_number": set_idx + 1,
                "proposed_weight": anchor_entered or None,
                "effective_weight": anchor_effective,
                "target_reps": target_reps,
                "target_rpe": target_rpe,
                "target_rir": rir,
                "r_fail": target_rtf,
                "acceptable_rep_min": max(1, target_reps - 3),
                "acceptable_rep_max": target_reps + 3,
                "proposed_entered_weight_lb": anchor_entered or None,
                "effective_weight_lb": anchor_effective,
                "target_reps_done": target_reps,
                "r_fail_rtf": target_rtf,
            },
            "scheme": {
                "set_number": set_idx + 1,
                "target_reps": target_reps,
                "target_rir": rir,
                "r_fail": target_rtf,
                "acceptable_rep_min": max(1, target_reps - 3),
                "acceptable_rep_max": target_reps + 3,
            },
            "observations": _build_observations(exercise_id, session),
        }

    # ── Stages 1+: compute W from accumulated observations ──
    M_estimates = [
        _invert_universal_curve(o["rtf"], o["effective_weight"])
        for o in all_obs
    ]
    # Discard pathological estimates (negative or below max observed weight).
    max_ew = max(o["effective_weight"] for o in all_obs)
    sane = [m for m in M_estimates if m > max_ew]
    M_guess = float(np.mean(sane)) if sane else max_ew * 1.3

    target_ew = _prescribe_universal_curve(M_guess, target_rtf)

    prev = all_obs[-1]
    prev_w = prev["weight"]

    # Translate target effective weight → entered weight for display.
    entered = entered_weight_for_effective_weight(
        exercise, effective_weight_lb=target_ew, bodyweight_lb=bodyweight_lb,
    )
    if entered is None:
        entered = target_ew

    # Safety clamp based on severity of the previous set's outcome.
    prev_stage = max(0, stage - 1)
    prev_target_rtf = float(targets[prev_stage])
    prev_target_reps = _rtf_to_reps_done(prev_target_rtf, rirs[prev_stage])
    severe_over, severe_under = _severity_flags(
        prev, prev_target_rtf, prev_target_reps,
    )
    if severe_over:
        lo, hi = _BOOT_CLAMP_SEVERE_OVER
    elif severe_under:
        lo, hi = _BOOT_CLAMP_SEVERE_UNDER
    else:
        lo, hi = _BOOT_CLAMP_NORMAL

    clamp_floor = prev_w * lo
    clamp_ceil = prev_w * hi
    entered_clamped = float(np.clip(entered, clamp_floor, clamp_ceil))

    # Beginner-friendly miss handling: if the previous set fell short of its
    # target RTF by ``_BOOT_MISS_THRESHOLD`` reps, never step UP — cap at the
    # prev weight so the next stage retries at the same load (or lighter).
    prev_actual_rtf = float(prev["rtf"])
    if prev_actual_rtf < prev_target_rtf - _BOOT_MISS_THRESHOLD:
        entered_clamped = min(entered_clamped, prev_w)
        clamp_ceil = min(clamp_ceil, prev_w)

    # Round to the dynamic frontend grid (integers + {2.5, 7.5, 12.5} below
    # 15 lb, 2.5 lb band to 100 lb, 5 lb above). Avoid prescribing the same
    # grid point as the previous set — that yields a third stacked dot with
    # no new information and stalls exit from bootstrap.
    entered_rounded = _snap_to_grid(entered_clamped)
    step = _grid_step(prev_w)
    if abs(entered_rounded - prev_w) < step / 2:
        bump = step if entered_clamped >= prev_w else -step
        entered_rounded = _snap_to_grid(prev_w + bump)
    # Re-clamp after rounding/bumping so the hard safety rail always holds.
    entered_rounded = float(np.clip(entered_rounded, clamp_floor, clamp_ceil))
    entered_rounded = _snap_to_grid(entered_rounded)

    # Recompute the effective weight and forward-predict rtf from the final
    # entered weight so the UI's "expected reps" matches what we'll prescribe.
    final_ew = _entered_to_effective(exercise, entered_rounded, bodyweight_lb)
    if final_ew <= 0:
        final_ew = max(1.0, entered_rounded)
    predicted_rtf = float(fresh_curve(final_ew, M_guess, BOOT_K, BOOT_GAMMA))
    expected_reps = _rtf_to_reps_done(predicted_rtf, rir)

    reason = "standard step"
    if severe_over:
        reason = "previous set was too heavy — stepping down"
    elif severe_under:
        reason = "previous set was too light — stepping up"

    bootstrap_block.update({
        "M_guess": round(M_guess, 1),
        "M_samples": len(sane),
        "severe_over": severe_over,
        "severe_under": severe_under,
        "reason": reason,
        "prior_weight": prev_w,
        "clamp": [lo, hi],
    })

    return {
        "has_curve": False,
        "mode": "bootstrap",
        "exercise_complete": False,
        "bootstrap": bootstrap_block,
        "next_set": {
            "set_number": set_idx + 1,
            "proposed_weight": round(entered_rounded, 1),
            "effective_weight": round(final_ew, 2),
            "target_reps": expected_reps,
            "target_rpe": target_rpe,
            "target_rir": rir,
            "r_fail": round(predicted_rtf, 1),
            "acceptable_rep_min": max(1, expected_reps - 3),
            "acceptable_rep_max": expected_reps + 3,
            # Space-explicit aliases (see _set_prescription_dict).
            "proposed_entered_weight_lb": round(entered_rounded, 1),
            "effective_weight_lb": round(final_ew, 2),
            "target_reps_done": expected_reps,
            "r_fail_rtf": round(predicted_rtf, 1),
        },
        "scheme": {
            "set_number": set_idx + 1,
            "target_reps": expected_reps,
            "target_rir": rir,
            "r_fail": round(predicted_rtf, 1),
            "acceptable_rep_min": max(1, expected_reps - 3),
            "acceptable_rep_max": expected_reps + 3,
        },
        "observations": _build_observations(exercise_id, session),
    }


# ── Fatigue profile (β_{e,s} per-set residual decomposition) ──
#
# Global fallback for β_s when there is insufficient per-(exercise, set_index)
# history. Derived from the model-exploration refresh (~300 sessions across all
# exercises): on average the second set loses ~1.2 reps and the third loses
# ~2.4 reps vs. the fresh-curve prediction. See
# ``tools/model_exploration/analysis_summary.md``.
GLOBAL_BETA_PER_SET: tuple[float, ...] = (0.0, -1.2, -2.4)

# Minimum historical sessions observing a given set index before we trust the
# learned β_s over the global fallback.
MIN_HISTORY_FOR_LEARNED_BETA = 2


def _global_beta(set_index: int) -> float:
    """Look up the global β_s fallback. Clamp past length to the final value."""
    if set_index <= 0:
        return 0.0
    idx = min(set_index, len(GLOBAL_BETA_PER_SET)) - 1
    return GLOBAL_BETA_PER_SET[idx]


def _beta_for_set(
    set_index: int, beta_per_set: list[float] | None = None,
) -> float:
    """Per-set fatigue residual β_n with synthesis for set 4+.

    For set_index ≤ len(series), use the provided learned series (or the
    global fallback). For set_index ≥ 4, synthesize via β_n = β_3 + β_(n−3):
    set 4 = β_3 + β_1, set 5 = β_3 + β_2, etc. Without this, prescriptions
    for set 4+ would use the fresh-curve weight (β=0) and overshoot.
    """
    if set_index <= 0:
        return 0.0
    series = beta_per_set if beta_per_set else list(GLOBAL_BETA_PER_SET)
    if set_index <= len(series):
        return float(series[set_index - 1])
    beta3 = float(series[2]) if len(series) >= 3 else _global_beta(3)
    return beta3 + _beta_for_set(set_index - 3, series)


def fatigue_profile(
    exercise_id: int,
    session: Session,
    *,
    days: int = 30,
    session_date: date | None = None,
) -> dict:
    """Return a reps-by-set-index fatigue decomposition for one exercise.

    Produces enough data for the UI companion "fatigue pane" that complements
    the weight-reps curve. Useful for fatigue-dominated exercises (holds,
    isolation movements) where three sets at a single weight degenerate to
    stacked dots on the curve view but tell a story when indexed by set.

    Algorithm:
      * Fit the current fresh curve (same `fit_curve` the prescription uses).
      * Walk the trailing ``days`` window of RPE-qualifying sets grouped by
        session. The anchor session (``session_date`` if provided, else the
        most recent) becomes ``session_observations``.
      * For each remaining session, bucket sets by ``set_order`` and compute
        the residual ``rtf - r_fresh(W)`` per bucket.
      * Average residuals per set index → ``beta_per_set``. When a set index
        has fewer than ``MIN_HISTORY_FOR_LEARNED_BETA`` history observations,
        fall back to the exploration-derived global series.

    The response is always keyed by 1-based set_index. ``has_data`` is False
    for bodyweight/non-strength exercises or when the curve cannot be fit
    (the UI should then render the global fallback as "typical decay").
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return {"exercise_id": exercise_id, "has_data": False,
                "message": "Exercise not found"}

    is_bw = (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES
    if is_bw:
        return {
            "exercise_id": exercise_id,
            "has_data": False,
            "is_bodyweight": True,
            "session_observations": [],
            "model_prediction": [],
            "beta_per_set": list(GLOBAL_BETA_PER_SET),
            "beta_source": "fallback",
            "n_history_sessions": 0,
        }

    fit = fit_curve(
        exercise_id, session, days=days,
        allow_heavy=exercise.allow_heavy_loading,
    )

    _, set_rows = _load_recent_sets(exercise_id, session, days)
    bw_lookup = _load_bodyweight_lookup(session)
    bodyweight_lb_now = latest_bodyweight(bw_lookup, user_today()) or 0.0

    # Group by session_date in reverse-chronological order. ``_load_recent_sets``
    # already orders by date desc, set_order asc.
    sessions_by_date: dict[date, list[tuple[WorkoutSet, date]]] = defaultdict(list)
    for ws, ws_date in set_rows:
        endurance = _set_endurance(ws)
        if ws.rpe is None or endurance is None or endurance <= 0:
            continue
        if ws.rpe < MIN_RPE_FOR_FIT or ws.rpe > 10.0:
            continue
        sessions_by_date[ws_date].append((ws, ws_date))

    ordered_dates = sorted(sessions_by_date.keys(), reverse=True)

    # Anchor session → session_observations. If the caller passed a specific
    # session_date, use that; otherwise the most-recent session. Historical
    # residuals (β) always exclude the anchor.
    anchor_date: date | None = None
    if session_date is not None and session_date in sessions_by_date:
        anchor_date = session_date
    elif ordered_dates:
        anchor_date = ordered_dates[0]

    session_observations: list[dict] = []
    if anchor_date is not None:
        metric_anchor = _metric_for(exercise)
        for ws, ws_date in sessions_by_date[anchor_date]:
            ew = effective_weight(exercise, ws, bw_lookup, ws_date)
            if ew <= 0:
                continue
            endurance = _set_endurance(ws)
            if endurance is None or endurance <= 0:
                continue
            rir = 10.0 - float(ws.rpe)
            endurance_out = (
                int(endurance) if metric_anchor.int_valued else round(endurance, 2)
            )
            session_observations.append({
                "set_index": int(ws.set_order),
                "weight": round(float(ws.weight or 0), 2),
                "effective_weight": round(ew, 2),
                "reps": endurance_out,
                "endurance_value": endurance_out,
                "rpe": round(float(ws.rpe), 1),
                "rtf": round(endurance + rir, 2),
                "session_date": ws_date.isoformat(),
            })

    # History = all sessions other than the anchor.
    history_by_set: dict[int, list[float]] = defaultdict(list)
    history_session_dates: set[date] = set()
    if fit is not None:
        for older_date in ordered_dates:
            if older_date == anchor_date:
                continue
            for ws, ws_date in sessions_by_date[older_date]:
                ew = effective_weight(exercise, ws, bw_lookup, ws_date)
                if ew <= 0:
                    continue
                endurance = _set_endurance(ws)
                if endurance is None or endurance <= 0:
                    continue
                rir = 10.0 - float(ws.rpe)
                rtf_obs = endurance + rir
                predicted_fresh = float(fresh_curve(ew, fit.M, fit.k, fit.gamma, fit.delta))
                residual = rtf_obs - predicted_fresh
                history_by_set[int(ws.set_order)].append(residual)
                history_session_dates.add(ws_date)

    # Build beta_per_set up to the largest set index we care about (at least
    # the length of the global fallback, and any set index observed today/in
    # history).
    observed_indices = (
        {o["set_index"] for o in session_observations}
        | set(history_by_set.keys())
    )
    max_idx = max(
        [len(GLOBAL_BETA_PER_SET)] + list(observed_indices) + [3]
    )

    beta_per_set: list[float] = []
    beta_learned_flags: list[bool] = []
    for s in range(1, max_idx + 1):
        residuals = history_by_set.get(s, [])
        if len(residuals) >= MIN_HISTORY_FOR_LEARNED_BETA and fit is not None:
            beta_per_set.append(round(float(np.mean(residuals)), 3))
            beta_learned_flags.append(True)
        else:
            beta_per_set.append(_global_beta(s))
            beta_learned_flags.append(False)

    beta_source = "learned" if any(beta_learned_flags) else "fallback"

    # Model prediction for each observed set: r_fresh(W_s) + β_s, clamped ≥ 0.
    model_prediction: list[dict] = []
    if fit is not None:
        for obs in session_observations:
            s = obs["set_index"]
            ew = obs["effective_weight"]
            beta_s = beta_per_set[s - 1] if 1 <= s <= len(beta_per_set) else 0.0
            predicted = float(fresh_curve(ew, fit.M, fit.k, fit.gamma, fit.delta)) + beta_s
            model_prediction.append({
                "set_index": s,
                "weight": obs["weight"],
                "effective_weight": ew,
                "predicted_rtf": round(max(predicted, 0.0), 2),
                "beta_used": beta_s,
                "beta_learned": beta_learned_flags[s - 1]
                    if 1 <= s <= len(beta_learned_flags) else False,
            })

    return {
        "exercise_id": exercise_id,
        "has_data": fit is not None,
        "session_observations": session_observations,
        "model_prediction": model_prediction,
        "beta_per_set": beta_per_set,
        "beta_learned_flags": beta_learned_flags,
        "beta_source": beta_source,
        "n_history_sessions": len(history_session_dates),
        "curve": _curve_dict(fit, exercise, bodyweight_lb_now) if fit is not None else None,
    }


def _scheme_for(exercise: Exercise, training_mode: str) -> list[tuple]:
    is_heavy_mode = training_mode == "heavy" and exercise.allow_heavy_loading
    if is_heavy_mode:
        return HEAVY_SCHEME
    if exercise.allow_heavy_loading:
        return VOLUME_SCHEME
    return LIGHT_SCHEME


def _bootstrap_scheme_dict(exercise: Exercise, training_mode: str, n_done: int) -> dict | None:
    """Scheme hint for the bootstrap flat-line UI when has_curve is False."""
    scheme = _scheme_for(exercise, training_mode)
    idx = min(n_done, len(scheme) - 1)
    r_fail_target, rir, _target_rpe, expected_reps, rep_min, rep_max = scheme[idx]
    return {
        "set_number": n_done + 1,
        "target_reps": int(expected_reps),
        "target_rir": int(rir),
        "r_fail": int(r_fail_target),
        "acceptable_rep_min": int(rep_min),
        "acceptable_rep_max": int(rep_max),
    }


def refit_with_observations(
    exercise_id: int,
    session: Session,
    new_obs: list[dict],
    *,
    days: int = 30,
    allow_heavy: bool = True,
) -> CurveFit | None:
    """Refit the curve incorporating in-session observations.

    new_obs: list of {"weight": float, "reps": int, "rpe": float}
    where weight is the entered weight (will be converted to effective).
    When allow_heavy is False, forces tier2 (fixed gamma).
    """
    exercise, set_rows = _load_recent_sets(exercise_id, session, days)
    if exercise is None:
        return None

    bw_lookup = _load_bodyweight_lookup(session)
    bodyweight_lb = latest_bodyweight(bw_lookup, user_today())
    today = user_today()

    # Build observations from DB
    eff_weights: list[float] = []
    reps_to_failure: list[float] = []
    confidences: list[float] = []
    ages_days: list[float] = []

    for ws, ws_date in set_rows:
        if not supports_strength_estimate(exercise, ws):
            continue
        if ws.rpe is None or ws.rpe < MIN_RPE_FOR_FIT or ws.rpe > 10.0:
            continue

        ew = effective_weight(exercise, ws, bw_lookup, ws_date)
        if ew <= 0:
            continue

        endurance = _set_endurance(ws)
        if endurance is None or endurance <= 0:
            continue

        rir = _rpe_to_rir(ws.rpe)
        eff_weights.append(ew)
        reps_to_failure.append(_reps_done_to_rtf(endurance, rir))
        confidences.append(_rpe_confidence(ws.rpe))
        ages_days.append((today - ws_date).days)

    # Add new in-session observations (age=0) BEFORE filtering so the
    # t-test uses current session performance as the anchor.  This drops
    # historical sessions whose strength level is statistically different
    # from what we're doing right now (injury recovery, technique change,
    # detraining, etc.).  After only 1 session set the t-test can't run
    # (needs ≥2 in the anchor), so historical filtering still applies.
    n_session = 0
    for obs in new_obs:
        endurance_obs = _obs_endurance(obs)
        if obs.get("rpe") is None or endurance_obs is None or endurance_obs <= 0:
            continue
        ew = _entered_to_effective(exercise, obs["weight"], bodyweight_lb)
        if ew <= 0:
            continue
        rir = 10.0 - obs["rpe"]
        eff_weights.append(ew)
        reps_to_failure.append(endurance_obs + rir)
        confidences.append(_rpe_confidence(obs["rpe"]))
        ages_days.append(0.0)  # just happened
        n_session += 1

    # Filter stale sessions — with session data present (age=0) it becomes
    # the anchor, so historical sessions that don't match current performance
    # are dropped.  Without session data this falls back to historical-only
    # filtering (most recent historical session as anchor).
    eff_weights, reps_to_failure, confidences, ages_days, n_sessions_kept = (
        _filter_stale_sessions(
            eff_weights, reps_to_failure, confidences, ages_days,
        )
    )

    n_obs = len(eff_weights)
    if n_obs < MIN_SETS_TIER2:
        return None

    W = np.array(eff_weights)
    r = np.array(reps_to_failure)
    conf = np.array(confidences)
    recency = _recency_weights(ages_days)
    fit_w = conf * recency

    # Session boost: scale same-day weights so they contribute TARGET share.
    # Session data (anchor) is always kept by the filter, so n_session is
    # unchanged.  n_prior may have shrunk if historical sessions were dropped.
    n_prior = n_obs - n_session
    if n_session > 0 and n_prior > 0:
        prior_total = float(np.sum(fit_w[:n_prior]))
        session_total = float(np.sum(fit_w[n_prior:]))
        if session_total > 0 and prior_total > 0:
            target = SESSION_TARGET_SHARE
            boost = (target * prior_total) / ((1 - target) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior:] *= boost

    # Determine tier — non-heavy exercises always tier2; heavy need sufficient data
    distinct_w = len(set(round(w, 1) for w in eff_weights))
    tier = (
        "tier1"
        if (allow_heavy
            and n_obs >= MIN_SETS_TIER1
            and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1
            and n_sessions_kept >= 2)
        else "tier2"
    )

    M_lower, M_upper, M_prior = _estimate_M_bounds(eff_weights, reps_to_failure)
    ident = _identifiability_score(eff_weights, reps_to_failure)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, delta_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, M_REG_LAMBDA, fixed_gamma
    )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit, delta_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit,
        k=k_fit,
        gamma=gamma_fit,
        delta=delta_fit,
        n_obs=n_obs,
        rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier,
        identifiability=ident,
    )


# ── Heavy availability checker ──

# Limits for heavy-mode frequency
HEAVY_PER_REGION_PER_SESSION = 1
HEAVY_PER_REGION_PER_WEEK = 2
HEAVY_EXERCISE_COOLDOWN_DAYS = 10

# Heavy-mode session ceiling: never prescribe more than this many sets in a
# single heavy exercise within one session.
HEAVY_MAX_SETS = 5

# Heavy-mode early stop: when the most recently logged set hit this RTF (reps
# + RIR) or below, treat capacity as reached and stop the exercise regardless
# of curve inflection state.
HEAVY_LOW_RTF_STOP = 3.0


def _get_exercise_regions(exercise_id: int, session: Session) -> list[str]:
    """Return primary regions for an exercise via ExerciseTissue → Tissue."""
    stmt = (
        select(Tissue.region)
        .join(ExerciseTissue, ExerciseTissue.tissue_id == Tissue.id)
        .where(ExerciseTissue.exercise_id == exercise_id)
    )
    return list(set(session.exec(stmt).all()))


def check_burnout_availability(
    exercise_id: int,
    session: Session,
) -> dict:
    """Check if an exercise can run in burnout mode.

    Burnout mode prescribes a single AMRAP set at ~½ recent max to anchor
    the left side of the strength curve. Available iff there is at least
    one recent entered weight to halve.

    Returns {available: bool, reason: str|None}.
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        return {"available": False, "reason": "Exercise not found"}
    is_bw = (exercise.load_input_mode or "external_weight") in BODYWEIGHT_MODES
    if is_bw:
        return {"available": False, "reason": "Bodyweight — no max weight to halve"}
    max_w = get_max_recent_entered_weight(exercise_id, session)
    if max_w is None or max_w <= 0:
        return {"available": False, "reason": "No recent history to anchor burnout"}
    return {"available": True, "reason": None}


def check_heavy_availability(
    exercise_id: int,
    session: Session,
    current_session_id: int | None = None,
) -> dict:
    """Check if an exercise can run in heavy mode.

    Rules:
    1. Exercise must have allow_heavy_loading=True
    2. ≤1 heavy exercise per region in current session
    3. ≤2 heavy exercises per region in past 7 days
    4. >10 days since this exercise was last done in heavy mode

    Returns {available: bool, reason: str|None, regions: list[str]}
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None or not exercise.allow_heavy_loading:
        return {"available": False, "reason": "Exercise does not allow heavy loading", "regions": []}

    regions = _get_exercise_regions(exercise_id, session)
    if not regions:
        return {"available": False, "reason": "No tissue regions mapped", "regions": []}

    today = user_today()

    # Rule 4: exercise-specific cooldown (>10 days since last heavy)
    cooldown_cutoff = today - timedelta(days=HEAVY_EXERCISE_COOLDOWN_DAYS)
    last_heavy_stmt = (
        select(WorkoutSession.date)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.training_mode == "heavy",
            WorkoutSession.date >= cooldown_cutoff,
        )
        .order_by(WorkoutSession.date.desc())
        .limit(1)
    )
    last_heavy_date = session.exec(last_heavy_stmt).first()
    if last_heavy_date is not None:
        days_since = (today - last_heavy_date).days
        return {
            "available": False,
            "reason": f"Heavy {days_since}d ago (need {HEAVY_EXERCISE_COOLDOWN_DAYS}d)",
            "regions": regions,
        }

    # Rule 2: ≤2 heavy per region per week (count distinct exercise exposures)
    week_cutoff = today - timedelta(days=7)
    for region in regions:
        # Get exercise IDs that target this region
        region_exercise_ids_stmt = (
            select(ExerciseTissue.exercise_id)
            .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
            .where(Tissue.region == region)
        )
        region_ex_ids = list(session.exec(region_exercise_ids_stmt).all())

        # Count distinct (session_id, exercise_id) with heavy mode in past week
        weekly_stmt = (
            select(WorkoutSet.session_id, WorkoutSet.exercise_id)
            .join(WorkoutSession, WorkoutSession.id == WorkoutSet.session_id)
            .where(
                WorkoutSet.exercise_id.in_(region_ex_ids),
                WorkoutSet.training_mode == "heavy",
                WorkoutSession.date >= week_cutoff,
            )
            .distinct()
        )
        weekly_heavy = len(session.exec(weekly_stmt).all())
        if weekly_heavy >= HEAVY_PER_REGION_PER_WEEK:
            return {
                "available": False,
                "reason": f"{region}: {weekly_heavy} heavy this week (max {HEAVY_PER_REGION_PER_WEEK})",
                "regions": regions,
            }

    # Rule 1: ≤1 heavy per *exercise group* (Push/Pull/Legs/Shoulders/Arms/Core)
    # in the current session. Per-tissue-region was too narrow — leg curl (hams)
    # and glute drive (glutes) would both count as fresh against each other
    # even though they're both Legs heavy work.
    if current_session_id is not None:
        my_group = get_exercise_group(exercise_id, session)
        if my_group != "Uncategorized":
            session_heavy_stmt = (
                select(WorkoutSet.exercise_id)
                .where(
                    WorkoutSet.session_id == current_session_id,
                    WorkoutSet.training_mode == "heavy",
                )
                .distinct()
            )
            session_heavy_ex_ids = list(session.exec(session_heavy_stmt).all())
            same_group_heavy = sum(
                1 for ex_id in session_heavy_ex_ids
                if get_exercise_group(ex_id, session) == my_group
            )
            if same_group_heavy >= HEAVY_PER_REGION_PER_SESSION:
                return {
                    "available": False,
                    "reason": (
                        f"{my_group}: already {same_group_heavy} heavy in "
                        f"session (max {HEAVY_PER_REGION_PER_SESSION})"
                    ),
                    "regions": regions,
                }

    return {"available": True, "reason": None, "regions": regions}


# ── Exercise menu helpers ──


def get_exercise_freshness(
    session: Session,
) -> list[dict]:
    """Return all exercises ordered by days since last trained.

    Each entry: {exercise_id, name, days_since_trained, allow_heavy_loading,
    load_input_mode, is_bodyweight}
    """
    exercises = session.exec(select(Exercise)).all()
    today = user_today()
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
            "set_metric_mode": ex.set_metric_mode or "reps",
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
    """Get a fixed-quantity suggestion for an exercise tracked as tier 3.

    Used for pure-bodyweight exercises (and any other exercise routed to
    median-quantity instead of curve fitting). Reads ``endurance_value``
    so duration/distance bodyweight exercises (Weighted Plank, etc.) get
    suggestions in their native unit.
    """
    exercise = session.get(Exercise, exercise_id)
    metric = _metric_for(exercise) if exercise is not None else _metric_for(
        Exercise(name="", set_metric_mode="reps")
    )
    cutoff = user_today() - timedelta(days=30)
    stmt = (
        select(WorkoutSet.endurance_value)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.endurance_value.is_not(None),
            WorkoutSet.endurance_value > 0,
            WorkoutSession.date >= cutoff,
        )
        .order_by(WorkoutSession.date.desc())
        .limit(20)
    )
    rows = session.exec(stmt).all()
    values = [float(ev) for ev in rows if ev is not None]

    default = {"reps": 15, "duration": 30, "distance": 50}[metric.kind]
    if values:
        median_value = float(np.median(values))
        if metric.int_valued:
            median_value = int(median_value)
    else:
        median_value = default

    return {
        "sets": 3,
        "reps_per_set": median_value,
        "endurance_per_set": median_value,
        "metric_kind": metric.kind,
        "display_unit": metric.display_unit,
        "notes": "Non-progressive: fixed " + metric.label + " target",
    }


def get_duration_suggestion(
    exercise_id: int, session: Session
) -> dict:
    """Non-progressive prescription for duration-mode exercises.

    Weighted timed exercises (weighted plank, etc.) don't follow a strength
    curve in the same way as reps — the user holds a fixed weight for a
    fixed time. We suggest the median historical (weight, seconds) pair so
    the prescription matches the user's actual practice instead of letting
    the curve invert into a low-time / high-weight extrapolation.
    """
    exercise = session.get(Exercise, exercise_id)
    metric = _metric_for(exercise) if exercise is not None else _METRIC_REPS
    cutoff = user_today() - timedelta(days=90)
    stmt = (
        select(WorkoutSet.endurance_value, WorkoutSet.weight)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.endurance_value.is_not(None),
            WorkoutSet.endurance_value > 0,
            WorkoutSession.date >= cutoff,
        )
        .order_by(WorkoutSession.date.desc(), WorkoutSet.set_order.desc())
        .limit(20)
    )
    rows = session.exec(stmt).all()
    secs_values = [float(ev) for (ev, _w) in rows if ev is not None]
    weight_values = [
        float(w) for (_ev, w) in rows if w is not None and w > 0
    ]

    if secs_values:
        target_secs = float(np.median(secs_values))
        if metric.int_valued:
            target_secs = int(round(target_secs))
    else:
        target_secs = 30

    target_weight = float(np.median(weight_values)) if weight_values else 0.0
    target_weight = round(target_weight, 1)

    return {
        "sets": 3,
        "endurance_per_set": target_secs,
        "weight": target_weight,
        "metric_kind": metric.kind,
        "display_unit": metric.display_unit,
        "rir_target": 2,
        "notes": "Non-progressive: median historical weight x duration",
        "samples": len(secs_values),
    }


# ── Internal helpers ──


def get_max_recent_entered_weight(
    exercise_id: int, session: Session, days: int = 90
) -> float | None:
    """Get the maximum entered weight used for this exercise recently."""
    cutoff = user_today() - timedelta(days=days)
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


def get_mean_recent_entered_weight(
    exercise_id: int, session: Session, days: int = 90
) -> float | None:
    """Mean of recent entered weights for this exercise (None if no history).

    Used for bootstrap-stage-0 anchors and curve-fit fallbacks so we don't
    propose a static high default like 70 lb on a never-trained exercise.
    """
    cutoff = user_today() - timedelta(days=days)
    stmt = (
        select(WorkoutSet.weight)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.weight.is_not(None),
            WorkoutSet.weight > 0,
            WorkoutSession.date >= cutoff,
        )
    )
    rows = list(session.exec(stmt).all())
    if not rows:
        return None
    return float(np.mean([float(r) for r in rows]))


def _starting_weight_from_history(
    exercise_id: int, session: Session, days: int = 90
) -> float:
    """Sensible starting-weight prior: mean of recent entered weights, else 0."""
    mean_w = get_mean_recent_entered_weight(exercise_id, session, days=days)
    return float(mean_w) if mean_w is not None else 0.0
