"""Phase 5: Full end-to-end model validation.

Puts all three layers together:
  1. M(t) -> current strength ceiling
  2. Fresh curve -> reps at weight
  3. Session fatigue -> within-session degradation

Validates against historical sessions and simulates prescriptions.
"""

import numpy as np
from dataclasses import dataclass
from collections import defaultdict
from data_loader import (
    get_connection,
    load_all_sets,
    load_bodyweight_history,
    effective_weight,
    nearest_bodyweight,
    SetRecord,
)
from strength_curve import CurveFitResult, fresh_curve, run_batch_fitting
from session_fatigue import FatigueParams, compute_dose
from strength_evolution import (
    EvolutionParams,
    EvolutionResult,
    compute_strength_evolution,
    run_evolution_analysis,
)


@dataclass
class FullPrediction:
    """End-to-end prediction for one set."""
    set_order: int
    weight: float
    actual_reps: int
    rpe: float | None
    actual_reps_to_failure: float | None  # reps + (10-RPE) if RPE
    M_t: float            # M(t) on this date
    rho_t: float          # M_t / M_star
    r_fresh: float        # fresh curve prediction (reps to failure)
    phi: float            # fatigue modifier
    r_available: float    # final prediction: phi * r_fresh (reps to failure)
    has_rpe: bool


@dataclass
class FullSessionValidation:
    """Full model validation for one session."""
    session_id: int
    date: str
    exercise_name: str
    n_sets: int
    predictions: list[FullPrediction]
    rmse: float
    mae: float


def validate_session(
    session_sets: list[SetRecord],
    curve: CurveFitResult,
    evo_result: EvolutionResult,
    bw_history: dict[str, float],
    fatigue_params: FatigueParams | None = None,
) -> FullSessionValidation | None:
    """Validate the full model against one historical session."""
    if not session_sets or not curve.success:
        return None

    params = fatigue_params or FatigueParams()
    session_id = session_sets[0].session_id
    date = session_sets[0].session_date

    # Find M(t) for this date
    M_t = curve.M  # fallback
    M_star = curve.M
    for point in evo_result.timeline:
        if point.date == date:
            M_t = point.M
            break
        elif point.date < date:
            M_t = point.M  # use last known

    rho_t = M_t / M_star if M_star > 0 else 1.0

    predictions = []
    fatigue = 0.0

    for s in sorted(session_sets, key=lambda x: x.set_order):
        if s.reps is None or s.reps <= 0:
            continue

        bw = nearest_bodyweight(bw_history, s.session_date)
        ew = effective_weight(s, bw)
        if ew is None or ew <= 0:
            continue

        # Full equation: r_avail = phi * k * (rho_t * M / W - 1)^gamma
        adjusted_M = rho_t * curve.M
        if adjusted_M <= ew:
            r_f = 0.0
        else:
            r_f = fresh_curve(ew, adjusted_M, curve.k, curve.gamma)

        phi = np.exp(-fatigue)
        r_avail = phi * r_f

        has_rpe = s.rpe is not None and 5.0 <= (s.rpe or 0) <= 10.0
        actual_rtf = None
        if has_rpe:
            actual_rtf = s.reps + (10.0 - s.rpe)

        predictions.append(FullPrediction(
            set_order=s.set_order,
            weight=ew,
            actual_reps=s.reps,
            rpe=s.rpe,
            actual_reps_to_failure=actual_rtf,
            M_t=M_t,
            rho_t=rho_t,
            r_fresh=float(r_f),
            phi=float(phi),
            r_available=float(r_avail),
            has_rpe=has_rpe,
        ))

        # Update fatigue
        dose = compute_dose(ew, s.reps, s.rpe, params)
        fatigue = params.eta * fatigue + params.c * dose

    if not predictions:
        return None

    # Compute errors only on RPE-tagged sets
    rpe_preds = [p for p in predictions if p.has_rpe and p.actual_reps_to_failure is not None]
    if rpe_preds:
        actual_rtf = np.array([p.actual_reps_to_failure for p in rpe_preds])
        pred_arr = np.array([p.r_available for p in rpe_preds])
        errors = actual_rtf - pred_arr
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        mae = float(np.mean(np.abs(errors)))
    else:
        actual = np.array([p.actual_reps for p in predictions])
        pred_arr = np.array([p.r_available for p in predictions])
        errors = actual - pred_arr
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        mae = float(np.mean(np.abs(errors)))

    return FullSessionValidation(
        session_id=session_id,
        date=date,
        exercise_name=curve.exercise_name,
        n_sets=len(predictions),
        predictions=predictions,
        rmse=rmse,
        mae=mae,
    )


def run_full_validation(
    curve_results: list[CurveFitResult] | None = None,
    evo_results: list[EvolutionResult] | None = None,
    fatigue_params: FatigueParams | None = None,
    db_path=None,
):
    """Run the full end-to-end validation."""
    if curve_results is None:
        curve_results = run_batch_fitting(db_path)

    if evo_results is None:
        evo_results = run_evolution_analysis(curve_results, db_path, fatigue_params)

    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    conn.close()

    curves_by_ex = {r.exercise_id: r for r in curve_results if r.success}
    evo_by_ex = {r.exercise_id: r for r in evo_results}

    # Group sets by (session_id, exercise_id)
    session_exercise_sets: dict[tuple[int, int], list[SetRecord]] = defaultdict(list)
    for s in all_sets:
        session_exercise_sets[(s.session_id, s.exercise_id)].append(s)

    print("\n" + "=" * 100)
    print("PHASE 5: FULL END-TO-END VALIDATION")
    print("=" * 100)

    validations = []
    for (sid, eid), sets in session_exercise_sets.items():
        if eid not in curves_by_ex or eid not in evo_by_ex:
            continue
        if len(sets) < 1:
            continue

        v = validate_session(
            sets, curves_by_ex[eid], evo_by_ex[eid], bw, fatigue_params
        )
        if v is not None:
            validations.append(v)

    print(f"\n  Validated {len(validations)} session-exercise combinations")

    # Overall metrics
    all_rmses = [v.rmse for v in validations]
    all_maes = [v.mae for v in validations]
    print(f"\n  Overall metrics:")
    print(f"    Median RMSE: {np.median(all_rmses):.3f} reps")
    print(f"    Mean RMSE: {np.mean(all_rmses):.3f} reps")
    print(f"    Median MAE: {np.median(all_maes):.3f} reps")
    print(f"    90th pctl RMSE: {np.percentile(all_rmses, 90):.3f} reps")

    # Per-exercise metrics
    by_exercise: dict[str, list[float]] = defaultdict(list)
    for v in validations:
        by_exercise[v.exercise_name].append(v.rmse)

    print(f"\n  Per-exercise RMSE (top 15 by session count):")
    print(f"  {'Exercise':<40} {'Sessions':>9} {'Median RMSE':>12} {'Mean RMSE':>12}")
    print(f"  {'-' * 75}")
    for name, rmses in sorted(by_exercise.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
        print(f"  {name:<40} {len(rmses):>9} {np.median(rmses):>12.3f} {np.mean(rmses):>12.3f}")

    # Sample detailed predictions (RPE sessions only)
    print(f"\n  -- Sample detailed predictions (RPE-tagged sessions) --")
    rpe_sessions = [v for v in validations if v.n_sets >= 3 and any(p.has_rpe for p in v.predictions)]
    rpe_sessions.sort(key=lambda x: sum(1 for p in x.predictions if p.has_rpe), reverse=True)

    for v in rpe_sessions[:5]:
        print(f"\n  {v.exercise_name} -- {v.date} ({v.n_sets} sets, RMSE={v.rmse:.2f})")
        print(f"  {'Set':>4} {'Weight':>8} {'Reps':>5} {'RPE':>4} {'r_fail':>6} {'Pred':>7} {'M(t)':>8} {'rho':>6} {'phi':>6} {'Err':>6}")
        for p in v.predictions:
            rpe_str = f"{p.rpe:.0f}" if p.rpe is not None else "  -"
            rtf_str = f"{p.actual_reps_to_failure:.0f}" if p.actual_reps_to_failure else "  -"
            err = ""
            if p.has_rpe and p.actual_reps_to_failure is not None:
                err = f"{p.actual_reps_to_failure - p.r_available:>+6.1f}"
            print(f"  {p.set_order:>4} {p.weight:>8.1f} {p.actual_reps:>5} {rpe_str:>4} {rtf_str:>6} {p.r_available:>7.1f} "
                  f"{p.M_t:>8.1f} {p.rho_t:>6.3f} {p.phi:>6.3f} {err}")

    # Prescription simulation
    print(f"\n  -- Prescription simulation (what would the model prescribe?) --")
    for v in rpe_sessions[:3]:
        p0 = v.predictions[0]
        W = p0.weight
        print(f"\n  {v.exercise_name} @ {W:.0f}lb:")
        print(f"    Model says max ~{p0.r_available:.0f} reps to failure (fresh)")
        if p0.r_available > 2:
            target_reps = max(1, int(p0.r_available - 2))
            print(f"    Prescription @ RPE 8 (2 RIR): {target_reps} reps")
            target_reps_6 = max(1, int(p0.r_available - 4))
            print(f"    Prescription @ RPE 6 (4 RIR): {target_reps_6} reps")
        if p0.actual_reps_to_failure:
            print(f"    Actual was: {p0.actual_reps} reps @ RPE {p0.rpe:.0f} (r_fail={p0.actual_reps_to_failure:.0f})")

    return validations


if __name__ == "__main__":
    validations = run_full_validation()
