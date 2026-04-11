"""Phase 3: Set dose calculation and session fatigue modeling.

Implements:
  - Dose: d_s = W^alpha * r^beta * exp(-lambda_ * RIR)
  - Scalar fatigue: f_{s+1} = eta * f_s + c * d_s
  - Fatigue modifier: phi_s = exp(-f_s)
  - Available reps: r_avail = phi_s * r_fresh(W)

Replays historical sessions to validate predictions.
"""

import numpy as np
from scipy.optimize import minimize
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
from strength_curve import (
    fresh_curve,
    build_observations,
    fit_single_exercise,
    CurveFitResult,
    run_batch_fitting,
)


@dataclass
class FatigueParams:
    """Session fatigue parameters."""
    alpha: float = 1.2   # weight exponent in dose
    beta: float = 1.0    # rep exponent in dose
    lambda_: float = 0.2  # RIR penalty in dose
    eta: float = 0.85    # fatigue persistence (carry-over between sets)
    c: float = 0.001     # dose-to-fatigue scaling


@dataclass
class SetPrediction:
    """Prediction for a single set within a session."""
    set_order: int
    weight: float
    actual_reps: int
    rpe: float | None
    actual_reps_to_failure: float | None  # reps + (10-RPE) if RPE available
    predicted_reps_fresh: float   # without fatigue (reps to failure)
    predicted_reps_fatigued: float  # with fatigue (reps to failure)
    fatigue_state: float  # f_s before this set
    phi: float            # exp(-f_s), the fatigue modifier
    dose: float           # dose from this set
    has_rpe: bool         # whether this set has RPE data for comparison


@dataclass
class SessionReplay:
    """Result of replaying a session through the fatigue model."""
    session_id: int
    date: str
    exercise_id: int
    exercise_name: str
    n_sets: int
    n_rpe_sets: int
    predictions: list[SetPrediction]
    rmse_fresh: float     # RMSE using fresh curve (rpe sets only)
    rmse_fatigued: float  # RMSE using fatigue model (rpe sets only)
    mae_fresh: float
    mae_fatigued: float


def compute_dose(W: float, r: int, rpe: float | None, params: FatigueParams) -> float:
    """Compute set dose: d = W^alpha * r^beta * exp(-lambda * RIR)."""
    rir = (10.0 - rpe) if rpe is not None else 2.0  # default 2 RIR if no RPE
    rir = max(0, rir)
    d = (W ** params.alpha) * (r ** params.beta) * np.exp(-params.lambda_ * rir)
    return float(d)


def replay_session(
    session_sets: list[SetRecord],
    curve: CurveFitResult,
    bw_history: dict[str, float],
    params: FatigueParams | None = None,
) -> SessionReplay | None:
    """Replay a session through the fatigue model, predicting each set."""
    if not session_sets or not curve.success:
        return None

    params = params or FatigueParams()
    session_id = session_sets[0].session_id
    date = session_sets[0].session_date
    ex_id = session_sets[0].exercise_id
    ex_name = session_sets[0].exercise_name

    predictions = []
    fatigue = 0.0  # initial fatigue state

    for s in sorted(session_sets, key=lambda x: x.set_order):
        if s.reps is None or s.reps <= 0:
            continue

        bw = nearest_bodyweight(bw_history, s.session_date)
        ew = effective_weight(s, bw)
        if ew is None or ew <= 0:
            continue

        # Fresh prediction (no fatigue) -- this predicts reps-to-failure
        r_fresh = fresh_curve(ew, curve.M, curve.k, curve.gamma)

        # Fatigued prediction
        phi = np.exp(-fatigue)
        r_fatigued = phi * r_fresh

        # Actual reps-to-failure (only available with RPE)
        has_rpe = s.rpe is not None and 5.0 <= (s.rpe or 0) <= 10.0
        actual_rtf = None
        if has_rpe:
            rir = 10.0 - s.rpe
            actual_rtf = s.reps + rir

        # Compute dose for this set
        dose = compute_dose(ew, s.reps, s.rpe, params)

        pred = SetPrediction(
            set_order=s.set_order,
            weight=ew,
            actual_reps=s.reps,
            rpe=s.rpe,
            actual_reps_to_failure=actual_rtf,
            predicted_reps_fresh=float(r_fresh),
            predicted_reps_fatigued=float(r_fatigued),
            fatigue_state=fatigue,
            phi=float(phi),
            dose=dose,
            has_rpe=has_rpe,
        )
        predictions.append(pred)

        # Update fatigue for next set
        fatigue = params.eta * fatigue + params.c * dose

    if not predictions:
        return None

    # Compute errors ONLY on RPE-tagged sets (where we know actual reps-to-failure)
    rpe_preds = [p for p in predictions if p.has_rpe and p.actual_reps_to_failure is not None]

    if rpe_preds:
        actual_rtf = np.array([p.actual_reps_to_failure for p in rpe_preds])
        pred_fresh = np.array([p.predicted_reps_fresh for p in rpe_preds])
        pred_fatigue = np.array([p.predicted_reps_fatigued for p in rpe_preds])

        rmse_f = float(np.sqrt(np.mean((actual_rtf - pred_fresh) ** 2)))
        rmse_fat = float(np.sqrt(np.mean((actual_rtf - pred_fatigue) ** 2)))
        mae_f = float(np.mean(np.abs(actual_rtf - pred_fresh)))
        mae_fat = float(np.mean(np.abs(actual_rtf - pred_fatigue)))
    else:
        # Fallback: compare predicted r_fail vs reps_performed as lower bound
        actual = np.array([p.actual_reps for p in predictions])
        pred_fresh = np.array([p.predicted_reps_fresh for p in predictions])
        pred_fatigue = np.array([p.predicted_reps_fatigued for p in predictions])
        rmse_f = float(np.sqrt(np.mean((actual - pred_fresh) ** 2)))
        rmse_fat = float(np.sqrt(np.mean((actual - pred_fatigue) ** 2)))
        mae_f = float(np.mean(np.abs(actual - pred_fresh)))
        mae_fat = float(np.mean(np.abs(actual - pred_fatigue)))

    return SessionReplay(
        session_id=session_id,
        date=date,
        exercise_id=ex_id,
        exercise_name=ex_name,
        n_sets=len(predictions),
        n_rpe_sets=len(rpe_preds),
        predictions=predictions,
        rmse_fresh=rmse_f,
        rmse_fatigued=rmse_fat,
        mae_fresh=mae_f,
        mae_fatigued=mae_fat,
    )


def replay_all_sessions(
    curve_results: list[CurveFitResult],
    db_path=None,
    params: FatigueParams | None = None,
) -> list[SessionReplay]:
    """Replay all historical sessions through the fatigue model."""
    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    conn.close()

    # Index curve results by exercise_id
    curves_by_ex = {r.exercise_id: r for r in curve_results if r.success}

    # Group sets by (session_id, exercise_id)
    session_exercise_sets: dict[tuple[int, int], list[SetRecord]] = defaultdict(list)
    for s in all_sets:
        session_exercise_sets[(s.session_id, s.exercise_id)].append(s)

    replays = []
    for (sid, eid), sets in session_exercise_sets.items():
        if eid not in curves_by_ex:
            continue
        if len(sets) < 2:
            continue  # need at least 2 sets for fatigue to matter

        replay = replay_session(sets, curves_by_ex[eid], bw, params)
        if replay is not None:
            replays.append(replay)

    return replays


def fit_fatigue_params(
    curve_results: list[CurveFitResult],
    db_path=None,
) -> tuple[FatigueParams, dict]:
    """Fit fatigue parameters (eta, c) to minimize session prediction error."""
    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    conn.close()

    curves_by_ex = {r.exercise_id: r for r in curve_results if r.success}

    # Group sets by (session_id, exercise_id)
    session_exercise_sets: dict[tuple[int, int], list[SetRecord]] = defaultdict(list)
    for s in all_sets:
        session_exercise_sets[(s.session_id, s.exercise_id)].append(s)

    # Only use sessions with >=3 sets for fitting
    fitting_sessions = [
        (sets, curves_by_ex[eid])
        for (sid, eid), sets in session_exercise_sets.items()
        if eid in curves_by_ex and len(sets) >= 3
    ]

    if len(fitting_sessions) < 5:
        print("  Too few multi-set sessions for fatigue parameter fitting")
        return FatigueParams(), {"error": "insufficient data"}

    def loss(x):
        params = FatigueParams(
            alpha=x[0], beta=x[1], lambda_=x[2], eta=x[3], c=x[4]
        )
        total_sq_error = 0.0
        n = 0
        for sets, curve in fitting_sessions:
            replay = replay_session(sets, curve, bw, params)
            if replay and replay.predictions:
                for p in replay.predictions:
                    if p.has_rpe and p.actual_reps_to_failure is not None:
                        total_sq_error += (p.actual_reps_to_failure - p.predicted_reps_fatigued) ** 2
                        n += 1
        return total_sq_error / max(n, 1)

    print(f"  Fitting fatigue params on {len(fitting_sessions)} sessions...")
    result = minimize(
        loss,
        x0=[1.2, 1.0, 0.2, 0.85, 0.001],
        method="L-BFGS-B",
        bounds=[
            (0.5, 2.0),    # alpha
            (0.5, 2.0),    # beta
            (0.01, 1.0),   # lambda_
            (0.5, 0.99),   # eta
            (1e-6, 0.01),  # c
        ],
        options={"maxiter": 100},
    )

    fitted = FatigueParams(
        alpha=result.x[0], beta=result.x[1], lambda_=result.x[2],
        eta=result.x[3], c=result.x[4],
    )
    info = {
        "loss": result.fun,
        "success": result.success,
        "n_sessions": len(fitting_sessions),
        "message": result.message if hasattr(result, "message") else "",
    }
    return fitted, info


def run_fatigue_analysis(curve_results: list[CurveFitResult] | None = None, db_path=None):
    """Run the full fatigue analysis: default params -> fit -> compare."""
    if curve_results is None:
        print("Running batch curve fitting first...")
        curve_results = run_batch_fitting(db_path)

    print("\n" + "=" * 100)
    print("PHASE 3: SESSION FATIGUE ANALYSIS")
    print("=" * 100)

    # Step 1: Replay with default params
    print("\n-- Step 1: Replay sessions with default fatigue params --")
    default_params = FatigueParams()
    replays_default = replay_all_sessions(curve_results, db_path, default_params)
    _print_replay_summary("Default params", replays_default)

    # Step 2: Replay with no fatigue (fresh curve only)
    print("\n-- Step 2: Baseline -- fresh curve only (no fatigue) --")
    no_fatigue = FatigueParams(eta=0, c=0)
    replays_fresh = replay_all_sessions(curve_results, db_path, no_fatigue)
    _print_replay_summary("No fatigue", replays_fresh)

    # Step 3: Fit fatigue params
    print("\n-- Step 3: Fitting fatigue parameters --")
    fitted_params, fit_info = fit_fatigue_params(curve_results, db_path)
    print(f"  Fitted: alpha={fitted_params.alpha:.3f}  beta={fitted_params.beta:.3f}  "
          f"lambda={fitted_params.lambda_:.3f}  eta={fitted_params.eta:.3f}  c={fitted_params.c:.6f}")
    print(f"  Loss: {fit_info.get('loss', 'N/A')}")

    # Step 4: Replay with fitted params
    print("\n-- Step 4: Replay sessions with fitted fatigue params --")
    replays_fitted = replay_all_sessions(curve_results, db_path, fitted_params)
    _print_replay_summary("Fitted params", replays_fitted)

    # Step 5: Comparison
    print("\n-- Comparison --")
    print(f"  {'Condition':<20} {'Median RMSE':>12} {'Mean RMSE':>12} {'Median MAE':>12}")
    print(f"  {'-' * 60}")
    for label, replays in [
        ("No fatigue", replays_fresh),
        ("Default params", replays_default),
        ("Fitted params", replays_fitted),
    ]:
        if replays:
            rmses = [r.rmse_fatigued for r in replays]
            maes = [r.mae_fatigued for r in replays]
            print(f"  {label:<20} {np.median(rmses):>12.3f} {np.mean(rmses):>12.3f} {np.median(maes):>12.3f}")

    # Step 6: Sample session visualizations (only sessions with RPE data)
    print("\n-- Sample session replays (RPE-tagged sessions only) --")
    good_replays = [r for r in replays_fitted if r.n_rpe_sets >= 2]
    good_replays.sort(key=lambda x: x.n_rpe_sets, reverse=True)
    for replay in good_replays[:5]:
        print(f"\n  Session {replay.session_id} -- {replay.exercise_name} ({replay.date}, {replay.n_sets} sets, {replay.n_rpe_sets} with RPE)")
        print(f"  {'Set':>4} {'Weight':>8} {'Reps':>5} {'RPE':>4} {'r_fail':>6} {'Fresh':>7} {'Fatigued':>9} {'phi':>6} {'Error':>6}")
        for p in replay.predictions:
            rpe_str = f"{p.rpe:.0f}" if p.rpe is not None else "  -"
            rtf_str = f"{p.actual_reps_to_failure:.0f}" if p.actual_reps_to_failure else "  -"
            err = ""
            if p.has_rpe and p.actual_reps_to_failure is not None:
                err = f"{p.actual_reps_to_failure - p.predicted_reps_fatigued:>+6.1f}"
            print(f"  {p.set_order:>4} {p.weight:>8.1f} {p.actual_reps:>5} {rpe_str:>4} {rtf_str:>6} {p.predicted_reps_fresh:>7.1f} "
                  f"{p.predicted_reps_fatigued:>9.1f} {p.phi:>6.3f} {err}")
        print(f"  RMSE: fresh={replay.rmse_fresh:.2f}, fatigued={replay.rmse_fatigued:.2f}")

    return replays_fitted, fitted_params


def _print_replay_summary(label: str, replays: list[SessionReplay]):
    """Print summary stats for a batch of session replays."""
    if not replays:
        print(f"  {label}: No sessions replayed")
        return

    rmses = [r.rmse_fatigued for r in replays]
    maes = [r.mae_fatigued for r in replays]
    multi = [r for r in replays if r.n_sets >= 3]

    print(f"  {label}: {len(replays)} sessions replayed ({len(multi)} with >=3 sets)")
    print(f"    Median RMSE: {np.median(rmses):.3f}")
    print(f"    Mean RMSE: {np.mean(rmses):.3f}")
    print(f"    Median MAE: {np.median(maes):.3f}")
    if multi:
        rmses_multi = [r.rmse_fatigued for r in multi]
        print(f"    Multi-set Median RMSE: {np.median(rmses_multi):.3f}")


if __name__ == "__main__":
    replays, params = run_fatigue_analysis()
