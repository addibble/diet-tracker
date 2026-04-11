"""Phase 2: Fresh-set strength curve fitting.

Implements r_fresh(W) = k * (M/W - 1)^gamma and fits M, k, gamma per exercise
from production data. Uses RPE-based pseudo-observations and optional ordinal
likelihood from completion labels.
"""

import math
import numpy as np
from scipy.optimize import minimize, least_squares
from dataclasses import dataclass, field
from collections import defaultdict
from data_loader import (
    get_connection,
    load_all_sets,
    load_bodyweight_history,
    effective_weight,
    nearest_bodyweight,
    SetRecord,
)


@dataclass
class Observation:
    """A single observation for curve fitting."""
    exercise_id: int
    exercise_name: str
    effective_weight: float
    reps_to_failure: float  # estimated from RPE
    rpe: float | None
    rep_completion: str | None  # 'full', 'partial', 'failed'
    reps_performed: int
    session_date: str
    set_order: int
    session_id: int
    observation_type: str  # 'rpe' or 'ordinal'
    confidence: float  # 0-1 weight for fitting


@dataclass
class CurveFitResult:
    """Result of fitting r_fresh(W) = k * (M/W - 1)^gamma."""
    exercise_id: int
    exercise_name: str
    M: float           # strength ceiling (max weight for 1 rep)
    k: float           # endurance scaling
    gamma: float       # curve shape
    tier: str
    n_observations: int
    n_rpe_observations: int
    residual_rmse: float
    residual_mae: float
    max_observed_weight: float
    min_observed_weight: float = 0.0
    brzycki_M: float = 0.0         # Brzycki-estimated 1RM (median)
    identifiability: float = 1.0   # 0-1 score for how well M is identified
    observations: list[Observation] = field(default_factory=list)
    success: bool = True
    message: str = ""


BODYWEIGHT_MODES = {"bodyweight", "assisted_bodyweight"}

# Exercises to always exclude (bodyweight-only with no meaningful load variation)
EXCLUDE_EXERCISES = {
    "Dead Bug", "Flutter Kicks", "Dips", "Hanging Leg Raises",
    "Hanging Knee Raises", "Incline Push-Up", "Push-ups", "Box Jumps",
    "Laying Down Crunches", "Reverse Crunch + isometric crunch",
    "Weighted Plank",
}


def _rpe_confidence(rpe: float) -> float:
    """Higher confidence for sets closer to failure.

    RPE 10 (0 RIR) = exact failure boundary -> 1.0
    RPE 9 (1 RIR) = very close -> 0.9
    RPE 8 (2 RIR) = good signal -> 0.75
    RPE 7 (3 RIR) = moderate -> 0.55
    RPE 6 (4 RIR) = rough estimate -> 0.35
    RPE 5 (5 RIR) = low precision -> 0.2
    """
    rir = 10.0 - rpe
    # Exponential decay: confidence drops as RIR increases
    return max(0.2, math.exp(-0.25 * rir))


def build_observations(
    sets: list[SetRecord],
    bw_history: dict[str, float],
    rpe_only: bool = True,
    exclude_bodyweight: bool = True,
    min_rpe_sets: int = 3,
) -> dict[int, list[Observation]]:
    """Build observations from workout sets.

    Default mode: RPE-only sets from non-bodyweight exercises with >=3 RPE sets.
    Each individual set is its own observation (no per-session aggregation).
    Confidence is based on RPE proximity to failure.
    """
    # First pass: collect all RPE observations
    raw_obs: dict[int, list[Observation]] = defaultdict(list)

    for s in sets:
        if s.reps is None or s.reps <= 0:
            continue

        # Filter bodyweight exercises
        if exclude_bodyweight:
            if s.load_input_mode in BODYWEIGHT_MODES:
                continue
            if s.exercise_name in EXCLUDE_EXERCISES:
                continue

        bw = nearest_bodyweight(bw_history, s.session_date)
        ew = effective_weight(s, bw)
        if ew is None or ew <= 0:
            continue

        if s.rpe is not None and 5.0 <= s.rpe <= 10.0:
            rir = 10.0 - s.rpe
            r_fail = s.reps + rir
            obs = Observation(
                exercise_id=s.exercise_id,
                exercise_name=s.exercise_name,
                effective_weight=ew,
                reps_to_failure=r_fail,
                rpe=s.rpe,
                rep_completion=s.rep_completion,
                reps_performed=s.reps,
                session_date=s.session_date,
                set_order=s.set_order,
                session_id=s.session_id,
                observation_type="rpe",
                confidence=_rpe_confidence(s.rpe),
            )
            raw_obs[s.exercise_id].append(obs)
        elif not rpe_only and s.rep_completion in ("full", "partial", "failed"):
            r_fail_est = s.reps
            if s.rep_completion == "full":
                r_fail_est = s.reps + 2
            elif s.rep_completion == "failed":
                r_fail_est = max(1, s.reps - 1)
            obs = Observation(
                exercise_id=s.exercise_id,
                exercise_name=s.exercise_name,
                effective_weight=ew,
                reps_to_failure=r_fail_est,
                rpe=None,
                rep_completion=s.rep_completion,
                reps_performed=s.reps,
                session_date=s.session_date,
                set_order=s.set_order,
                session_id=s.session_id,
                observation_type="ordinal",
                confidence=0.3,
            )
            raw_obs[s.exercise_id].append(obs)

    # Filter: require minimum RPE sets per exercise
    obs_by_exercise: dict[int, list[Observation]] = {}
    for ex_id, obs_list in raw_obs.items():
        rpe_count = sum(1 for o in obs_list if o.observation_type == "rpe")
        if rpe_count >= min_rpe_sets:
            obs_by_exercise[ex_id] = obs_list

    return obs_by_exercise


def fresh_curve(W: float | np.ndarray, M: float, k: float, gamma: float) -> float | np.ndarray:
    """r_fresh(W) = k * (M/W - 1)^gamma.
    Returns 0 for W >= M.
    """
    ratio = M / W - 1.0
    if isinstance(ratio, np.ndarray):
        result = np.where(ratio > 0, k * np.power(ratio, gamma), 0.0)
    else:
        result = k * (ratio ** gamma) if ratio > 0 else 0.0
    return result


def _recency_weights(dates: list[str], half_life_days: float = 60.0) -> np.ndarray:
    """Exponential recency weighting: recent sets count more."""
    if not dates:
        return np.array([])
    max_date = max(dates)
    days_ago = []
    for d in dates:
        # Simple ordinal diff (ISO date strings sort correctly)
        from datetime import date as dtdate
        d1 = dtdate.fromisoformat(d)
        d2 = dtdate.fromisoformat(max_date)
        days_ago.append((d2 - d1).days)
    days_ago = np.array(days_ago, dtype=float)
    weights = np.exp(-np.log(2) * days_ago / half_life_days)
    return weights


def _brzycki_1rm(weight: float, reps: float) -> float:
    """Brzycki 1RM estimate: W * 36 / (37 - r). Capped at 30 reps."""
    if reps >= 37:
        return weight * 2.5  # rough extrapolation beyond Brzycki range
    return weight * 36.0 / (37.0 - min(reps, 36))


def _estimate_M_bounds(observations: list[Observation]) -> tuple[float, float, float]:
    """Estimate M bounds using Brzycki 1RM cross-checks.

    Returns (lower_bound, upper_bound, M_prior) for M.
    M_prior is the median Brzycki 1RM estimate (best guess of true M).
    """
    brzycki_estimates = []
    for o in observations:
        if o.observation_type == "rpe" and o.reps_to_failure > 0:
            est = _brzycki_1rm(o.effective_weight, o.reps_to_failure)
            brzycki_estimates.append(est)

    max_w = max(o.effective_weight for o in observations)

    if not brzycki_estimates:
        # No RPE data: use max_weight * moderate multiplier
        M_prior = max_w * 1.3
        lower = max_w * 1.01
        upper = max_w * 2.0
        return (lower, upper, M_prior)

    median_1rm = float(np.median(brzycki_estimates))
    max_1rm = float(np.max(brzycki_estimates))

    M_prior = median_1rm
    lower = max(max_w * 1.01, median_1rm * 0.8)
    # Moderate upper bound: don't let M run away but don't over-constrain
    upper = max(max_1rm * 1.5, max_w * 2.0)
    return (lower, upper, M_prior)


def _identifiability_score(observations: list[Observation]) -> float:
    """Score 0-1 for how well the data can identify M.

    Low score = data is clustered/flat, M is poorly constrained.
    High score = data spans wide weight range with clear rep-weight slope.
    """
    weights = [o.effective_weight for o in observations]
    reps = [o.reps_to_failure for o in observations]
    if len(weights) < 3:
        return 0.0

    min_w, max_w = min(weights), max(weights)
    distinct_w = len(set(round(w, 1) for w in weights))

    # Factor 1: Weight range ratio (1.0 = no range, 2.0+ = good range)
    range_ratio = max_w / max(min_w, 1.0)
    range_score = min(1.0, (range_ratio - 1.0) / 1.0)  # 0 at ratio=1, 1 at ratio>=2

    # Factor 2: Number of distinct weights (3+ is good)
    weight_variety = min(1.0, (distinct_w - 1) / 4.0)  # 0 at 1 weight, 1 at 5+ weights

    # Factor 3: Observed rep-weight slope (flat = bad)
    if max_w > min_w:
        w_arr = np.array(weights)
        r_arr = np.array(reps)
        # Simple correlation between weight and reps
        corr = abs(np.corrcoef(w_arr, r_arr)[0, 1]) if len(w_arr) > 2 else 0.0
        if np.isnan(corr):
            corr = 0.0
        slope_score = corr  # higher |correlation| = better identification
    else:
        slope_score = 0.0

    # Combine: geometric-ish mean, each factor matters
    score = (range_score * 0.4 + weight_variety * 0.3 + slope_score * 0.3)
    return float(np.clip(score, 0.0, 1.0))


def fit_single_exercise(
    observations: list[Observation],
    tier: str = "tier1",
    fixed_gamma: float | None = None,
    recency_half_life: float = 30.0,
) -> CurveFitResult:
    """Fit the fresh-set strength curve for one exercise.

    Uses ALL individual RPE sets (not just first-per-session).
    Recency half-life defaults to 30 days to emphasize recent training.
    """
    if not observations:
        return CurveFitResult(
            exercise_id=0, exercise_name="", M=0, k=0, gamma=0,
            tier=tier, n_observations=0, n_rpe_observations=0,
            residual_rmse=float("inf"), residual_mae=float("inf"),
            max_observed_weight=0, success=False, message="No observations",
        )

    ex_id = observations[0].exercise_id
    ex_name = observations[0].exercise_name

    # Use ALL RPE observations individually (no first-set-per-session filter)
    fit_obs = observations

    weights_arr = np.array([o.effective_weight for o in fit_obs])
    reps_arr = np.array([o.reps_to_failure for o in fit_obs])
    conf_arr = np.array([o.confidence for o in fit_obs])
    dates = [o.session_date for o in fit_obs]
    recency = _recency_weights(dates, recency_half_life)
    fit_weights = conf_arr * recency

    max_w = float(np.max(weights_arr))
    min_w = float(np.min(weights_arr))
    rpe_count = sum(1 for o in fit_obs if o.observation_type == "rpe")

    # Compute Brzycki bounds and prior M
    M_lower, M_upper, M_prior = _estimate_M_bounds(fit_obs)
    ident = _identifiability_score(fit_obs)

    # Regularization strength: stronger when data poorly identifies M
    # Base lambda=10, doubled when identifiability is low
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    if tier == "tier2" and fixed_gamma is not None:
        result = _fit_M_k(weights_arr, reps_arr, fit_weights, fixed_gamma,
                          max_w, ex_id, ex_name, rpe_count, fit_obs,
                          M_lower, M_upper, M_prior, lambda_M)
        result.tier = "tier2"
    else:
        result = _fit_full(weights_arr, reps_arr, fit_weights, max_w,
                           ex_id, ex_name, rpe_count, fit_obs,
                           M_lower, M_upper, M_prior, lambda_M)

    result.min_observed_weight = min_w
    result.brzycki_M = M_prior
    result.identifiability = ident
    return result


def _get_first_sets_per_session(observations: list[Observation]) -> list[Observation]:
    """Get only the first set per session (freshest, least fatigued)."""
    by_session: dict[int, list[Observation]] = defaultdict(list)
    for o in observations:
        by_session[o.session_id].append(o)
    result = []
    for session_obs in by_session.values():
        first = min(session_obs, key=lambda x: x.set_order)
        result.append(first)
    return result


def _fit_full(
    W: np.ndarray, r: np.ndarray, fit_w: np.ndarray, max_W: float,
    ex_id: int, ex_name: str, rpe_count: int, observations: list[Observation],
    M_lower: float = None, M_upper: float = None,
    M_prior: float = None, lambda_M: float = 10.0,
) -> CurveFitResult:
    """Full 3-parameter fit: M, k, gamma."""
    if M_lower is None:
        M_lower = max_W * 1.01
    if M_upper is None:
        M_upper = max_W * 5.0
    if M_prior is None:
        M_prior = max_W * 1.3

    best_result = None
    best_loss = float("inf")

    for M_init_factor in [1.1, 1.3, 1.5, 2.0]:
        for gamma_init in [0.15, 0.5, 1.0]:
            M_init = np.clip(max_W * M_init_factor, M_lower, M_upper)
            k_init = float(np.median(r))

            try:
                res = minimize(
                    _curve_loss,
                    x0=[M_init, k_init, gamma_init],
                    args=(W, r, fit_w, False, None, M_prior, lambda_M),
                    method="L-BFGS-B",
                    bounds=[
                        (M_lower, M_upper),    # M bounded by Brzycki estimates
                        (0.5, 200.0),           # k > 0
                        (0.05, 3.0),            # gamma
                    ],
                )
                if res.fun < best_loss:
                    best_loss = res.fun
                    best_result = res
            except Exception:
                continue

    if best_result is None or not best_result.success:
        fallback_M = np.clip(max_W * 1.1, M_lower, M_upper)
        return CurveFitResult(
            exercise_id=ex_id, exercise_name=ex_name,
            M=fallback_M, k=float(np.median(r)), gamma=1.0,
            tier="tier1", n_observations=len(W), n_rpe_observations=rpe_count,
            residual_rmse=float("inf"), residual_mae=float("inf"),
            max_observed_weight=max_W, observations=observations,
            success=False, message="Optimization failed",
        )

    M_fit, k_fit, gamma_fit = best_result.x
    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit)
    residuals = r - predicted
    weighted_residuals = residuals * fit_w
    rmse = float(np.sqrt(np.mean(weighted_residuals ** 2)))
    mae = float(np.mean(np.abs(weighted_residuals)))

    return CurveFitResult(
        exercise_id=ex_id, exercise_name=ex_name,
        M=M_fit, k=k_fit, gamma=gamma_fit,
        tier="tier1", n_observations=len(W), n_rpe_observations=rpe_count,
        residual_rmse=rmse, residual_mae=mae,
        max_observed_weight=max_W, observations=observations,
    )


def _fit_M_k(
    W: np.ndarray, r: np.ndarray, fit_w: np.ndarray,
    fixed_gamma: float, max_W: float,
    ex_id: int, ex_name: str, rpe_count: int, observations: list[Observation],
    M_lower: float = None, M_upper: float = None,
    M_prior: float = None, lambda_M: float = 10.0,
) -> CurveFitResult:
    """2-parameter fit with gamma fixed."""
    if M_lower is None:
        M_lower = max_W * 1.01
    if M_upper is None:
        M_upper = max_W * 5.0
    if M_prior is None:
        M_prior = max_W * 1.3

    best_result = None
    best_loss = float("inf")

    for M_init_factor in [1.1, 1.3, 1.5, 2.0]:
        M_init = np.clip(max_W * M_init_factor, M_lower, M_upper)
        k_init = float(np.median(r))

        try:
            res = minimize(
                _curve_loss,
                x0=[M_init, k_init],
                args=(W, r, fit_w, True, fixed_gamma, M_prior, lambda_M),
                method="L-BFGS-B",
                bounds=[
                    (M_lower, M_upper),
                    (0.5, 200.0),
                ],
            )
            if res.fun < best_loss:
                best_loss = res.fun
                best_result = res
        except Exception:
            continue

    if best_result is None:
        fallback_M = np.clip(max_W * 1.1, M_lower, M_upper)
        return CurveFitResult(
            exercise_id=ex_id, exercise_name=ex_name,
            M=fallback_M, k=float(np.median(r)), gamma=fixed_gamma,
            tier="tier2", n_observations=len(W), n_rpe_observations=rpe_count,
            residual_rmse=float("inf"), residual_mae=float("inf"),
            max_observed_weight=max_W, observations=observations,
            success=False, message="Optimization failed",
        )

    M_fit, k_fit = best_result.x
    predicted = fresh_curve(W, M_fit, k_fit, fixed_gamma)
    residuals = r - predicted
    weighted_residuals = residuals * fit_w
    rmse = float(np.sqrt(np.mean(weighted_residuals ** 2)))
    mae = float(np.mean(np.abs(weighted_residuals)))

    return CurveFitResult(
        exercise_id=ex_id, exercise_name=ex_name,
        M=M_fit, k=k_fit, gamma=fixed_gamma,
        tier="tier2", n_observations=len(W), n_rpe_observations=rpe_count,
        residual_rmse=rmse, residual_mae=mae,
        max_observed_weight=max_W, observations=observations,
    )


def _curve_loss(params, W, r, fit_weights, fixed_gamma_mode, fixed_gamma,
                M_prior=None, lambda_M=0.0):
    """Weighted least squares loss with log-space Brzycki prior on M."""
    if fixed_gamma_mode:
        M, k = params
        gamma = fixed_gamma
    else:
        M, k, gamma = params

    predicted = fresh_curve(W, M, k, gamma)
    residuals = r - predicted
    data_loss = float(np.sum(fit_weights * residuals ** 2))

    # Soft prior: penalize M drifting from Brzycki estimate in log-space
    reg_term = 0.0
    if M_prior is not None and M_prior > 0 and lambda_M > 0:
        reg_term = lambda_M * math.log(M / M_prior) ** 2

    return data_loss + reg_term


# ----------------------------------------------------------------
# Batch fitting
# ----------------------------------------------------------------

def compute_class_priors(results: list[CurveFitResult]) -> dict[str, dict]:
    """Compute exercise-class priors from Tier 1 fits.

    Groups by equipment type and computes median k, gamma.
    M is exercise-specific so not included in priors.
    """
    by_equipment: dict[str, list[CurveFitResult]] = defaultdict(list)
    for r in results:
        if r.success and r.tier == "tier1":
            # Get equipment from first observation
            equip = "unknown"
            if r.observations:
                # We need to look up equipment -- pass it through
                equip = "machine"  # default; ideally we'd carry this
            by_equipment[equip].append(r)

    priors = {}
    for equip, fits in by_equipment.items():
        ks = [f.k for f in fits]
        gammas = [f.gamma for f in fits]
        priors[equip] = {
            "k_median": float(np.median(ks)),
            "gamma_median": float(np.median(gammas)),
            "k_std": float(np.std(ks)),
            "gamma_std": float(np.std(gammas)),
            "n_exercises": len(fits),
        }
    return priors


def run_batch_fitting(db_path=None, tier_assignments=None,
                      rpe_only=True, exclude_bodyweight=True, min_rpe_sets=3):
    """Run curve fitting across qualifying exercises.

    Default: RPE-only sets, non-bodyweight, >=3 RPE sets per exercise.
    All individual sets used (not first-per-session).
    """
    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    conn.close()

    obs_by_ex = build_observations(all_sets, bw, rpe_only=rpe_only,
                                   exclude_bodyweight=exclude_bodyweight,
                                   min_rpe_sets=min_rpe_sets)

    # With RPE-only filtering, tier assignment is simpler:
    # >=8 RPE sets AND >=2 distinct weights -> tier1 (full 3-param)
    # else -> tier2 (fix gamma)
    def _assign_tier(obs_list):
        rpe_obs = [o for o in obs_list if o.observation_type == "rpe"]
        distinct_w = len(set(round(o.effective_weight, 1) for o in rpe_obs))
        if len(rpe_obs) >= 5 and distinct_w >= 2:
            return "tier1"
        return "tier2"

    # Phase 1: Fit Tier 1 exercises fully
    tier1_results = []
    print("\n" + "=" * 100)
    print("PHASE 2: FRESH-SET STRENGTH CURVE FITTING (RPE-only, all sets)")
    print("=" * 100)
    print(f"  Qualifying exercises: {len(obs_by_ex)} (>={min_rpe_sets} RPE sets, non-bodyweight)")

    print("\n-- Tier 1: Full 3-parameter fit (M, k, gamma) --")
    for ex_id, obs in sorted(obs_by_ex.items(), key=lambda x: len(x[1]), reverse=True):
        tier = _assign_tier(obs)
        if tier != "tier1":
            continue
        result = fit_single_exercise(obs, tier="tier1")
        tier1_results.append(result)
        status = "v" if result.success else "x"
        print(f"  {status} {result.exercise_name:<40} M={result.M:>7.1f}(brz={result.brzycki_M:.0f})  "
              f"k={result.k:>5.1f}  gamma={result.gamma:.2f}  "
              f"RMSE={result.residual_rmse:.2f}  ident={result.identifiability:.2f}  "
              f"(n={result.n_observations}, RPE={result.n_rpe_observations})")

    # Compute class priors from Tier 1
    global_gamma = float(np.median([r.gamma for r in tier1_results if r.success])) if tier1_results else 1.0
    global_k = float(np.median([r.k for r in tier1_results if r.success])) if tier1_results else 10.0
    print(f"\n  Class priors from Tier 1: gamma_median={global_gamma:.2f}, k_median={global_k:.1f}")

    # Phase 2: Fit Tier 2 with fixed gamma (matching production DEFAULT_GAMMA=0.20)
    fixed_tier2_gamma = 0.20
    tier2_results = []
    print(f"\n-- Tier 2: 2-parameter fit (M, k) with gamma={fixed_tier2_gamma:.2f} (fixed) --")
    for ex_id, obs in sorted(obs_by_ex.items(), key=lambda x: len(x[1]), reverse=True):
        tier = _assign_tier(obs)
        if tier != "tier2":
            continue
        result = fit_single_exercise(obs, tier="tier2", fixed_gamma=fixed_tier2_gamma)
        tier2_results.append(result)
        status = "v" if result.success else "x"
        print(f"  {status} {result.exercise_name:<40} M={result.M:>7.1f}(brz={result.brzycki_M:.0f})  "
              f"k={result.k:>5.1f}  gamma={result.gamma:.2f}(fixed)  "
              f"RMSE={result.residual_rmse:.2f}  ident={result.identifiability:.2f}  "
              f"(n={result.n_observations})")

    all_results = tier1_results + tier2_results

    # Summary
    print(f"\n{'=' * 100}")
    print("FIT SUMMARY")
    print(f"{'=' * 100}")
    successful = [r for r in all_results if r.success]
    failed = [r for r in all_results if not r.success]
    print(f"  Successful fits: {len(successful)}")
    print(f"  Failed fits: {len(failed)}")
    if failed:
        for r in failed:
            print(f"    x {r.exercise_name}: {r.message}")
    if successful:
        rmses = [r.residual_rmse for r in successful if r.residual_rmse < float("inf")]
        if rmses:
            print(f"  Median RMSE: {np.median(rmses):.2f}")
            print(f"  Mean RMSE: {np.mean(rmses):.2f}")

    # Top 10 best fits
    print(f"\n  Top 10 best fits (lowest RMSE):")
    for r in sorted(successful, key=lambda x: x.residual_rmse)[:10]:
        print(f"    {r.exercise_name:<40} RMSE={r.residual_rmse:.3f}  M={r.M:.1f}  k={r.k:.1f}  gamma={r.gamma:.2f}")

    # Top 10 worst fits
    print(f"\n  Top 10 worst fits (highest RMSE):")
    for r in sorted(successful, key=lambda x: x.residual_rmse, reverse=True)[:10]:
        print(f"    {r.exercise_name:<40} RMSE={r.residual_rmse:.3f}  M={r.M:.1f}  k={r.k:.1f}  gamma={r.gamma:.2f}")

    return all_results


if __name__ == "__main__":
    results = run_batch_fitting()
