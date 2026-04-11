"""Phase 4: Long-term strength evolution M(t).

Implements:
  - M_{t+1} = M_t + a * D_session - b * f_long
  - Recovery% = 100 * M(t) / M_peak(t)
  - Injury shock detection
  - Plateau detection

With hard bounds:
  - M > floor (0.1 * initial_M)
  - +/-5% daily change cap
  - Layoff decay toward 85% of M_star over 30+ days
"""

import numpy as np
from scipy.optimize import minimize
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import date as dtdate, timedelta
from data_loader import (
    get_connection,
    load_all_sets,
    load_bodyweight_history,
    load_tissue_conditions,
    effective_weight,
    nearest_bodyweight,
    SetRecord,
)
from strength_curve import CurveFitResult, fresh_curve, run_batch_fitting
from session_fatigue import compute_dose, FatigueParams


@dataclass
class EvolutionParams:
    """Long-term strength evolution parameters."""
    a: float = 0.0001    # training gain per unit dose
    b: float = 0.00005   # fatigue cost per unit long-term fatigue
    f_long_decay: float = 0.9  # daily long-term fatigue decay
    layoff_decay_rate: float = 0.005  # daily M decay during layoff (toward 85% M_star)
    layoff_threshold_days: int = 14   # days without training before layoff kicks in
    daily_cap_pct: float = 0.05  # max 5% daily change
    floor_pct: float = 0.1  # M floor at 10% of initial


@dataclass
class StrengthPoint:
    """A single point in the M(t) time series."""
    date: str
    M: float
    M_peak: float
    recovery_pct: float
    session_dose: float  # dose from training that day (0 if rest day)
    f_long: float        # long-term fatigue state
    delta_M: float       # change from previous day
    days_since_last: int


@dataclass
class EvolutionResult:
    """Result of computing M(t) for one exercise."""
    exercise_id: int
    exercise_name: str
    timeline: list[StrengthPoint]
    initial_M: float
    final_M: float
    peak_M: float
    min_recovery_pct: float
    n_training_days: int
    n_total_days: int
    detected_drops: list[dict]  # [{date, drop_pct, M_before, M_after}]


def compute_session_dose(
    sets: list[SetRecord],
    bw_history: dict[str, float],
    fatigue_params: FatigueParams | None = None,
) -> float:
    """Compute total session dose from a set of sets."""
    params = fatigue_params or FatigueParams()
    total = 0.0
    for s in sets:
        if s.reps is None or s.reps <= 0:
            continue
        bw = nearest_bodyweight(bw_history, s.session_date)
        ew = effective_weight(s, bw)
        if ew is None or ew <= 0:
            continue
        d = compute_dose(ew, s.reps, s.rpe, params)
        total += d
    return total


def compute_strength_evolution(
    exercise_id: int,
    exercise_name: str,
    initial_M: float,
    sessions_by_date: dict[str, list[SetRecord]],
    bw_history: dict[str, float],
    evo_params: EvolutionParams | None = None,
    fatigue_params: FatigueParams | None = None,
) -> EvolutionResult:
    """Compute M(t) time series for one exercise."""
    evo = evo_params or EvolutionParams()
    fat = fatigue_params or FatigueParams()

    if not sessions_by_date:
        return EvolutionResult(
            exercise_id=exercise_id, exercise_name=exercise_name,
            timeline=[], initial_M=initial_M, final_M=initial_M,
            peak_M=initial_M, min_recovery_pct=100.0,
            n_training_days=0, n_total_days=0, detected_drops=[],
        )

    # Get date range
    all_dates = sorted(sessions_by_date.keys())
    start_date = dtdate.fromisoformat(all_dates[0])
    end_date = dtdate.fromisoformat(all_dates[-1])
    n_days = (end_date - start_date).days + 1

    M = initial_M
    M_star = initial_M  # long-term reference
    M_peak = initial_M
    f_long = 0.0
    M_floor = initial_M * evo.floor_pct

    timeline = []
    detected_drops = []
    last_training_date = start_date
    training_days = 0

    for day_offset in range(n_days):
        current_date = start_date + timedelta(days=day_offset)
        date_str = current_date.isoformat()

        days_since_last = (current_date - last_training_date).days

        # Session dose for today
        session_dose = 0.0
        if date_str in sessions_by_date:
            session_dose = compute_session_dose(
                sessions_by_date[date_str], bw_history, fat
            )
            last_training_date = current_date
            training_days += 1

        # M update
        delta_M = evo.a * session_dose - evo.b * f_long

        # Layoff decay
        if days_since_last > evo.layoff_threshold_days:
            target = 0.85 * M_star
            decay = evo.layoff_decay_rate * (M - target)
            delta_M -= decay

        # Daily cap
        max_delta = evo.daily_cap_pct * M
        delta_M = np.clip(delta_M, -max_delta, max_delta)

        M_prev = M
        M = M + delta_M

        # Floor
        M = max(M, M_floor)

        # Update peak and M_star
        M_peak = max(M_peak, M)
        # M_star slowly tracks toward M with EMA
        M_star = 0.99 * M_star + 0.01 * M

        recovery_pct = 100.0 * M / M_peak if M_peak > 0 else 100.0

        # Long-term fatigue decay
        f_long = evo.f_long_decay * f_long + session_dose

        # Detect drops
        if M_prev > 0 and (M - M_prev) / M_prev < -0.03:
            detected_drops.append({
                "date": date_str,
                "drop_pct": 100 * (M - M_prev) / M_prev,
                "M_before": M_prev,
                "M_after": M,
            })

        point = StrengthPoint(
            date=date_str,
            M=M,
            M_peak=M_peak,
            recovery_pct=recovery_pct,
            session_dose=session_dose,
            f_long=f_long,
            delta_M=delta_M,
            days_since_last=days_since_last,
        )
        timeline.append(point)

    min_recovery = min(p.recovery_pct for p in timeline) if timeline else 100.0

    return EvolutionResult(
        exercise_id=exercise_id,
        exercise_name=exercise_name,
        timeline=timeline,
        initial_M=initial_M,
        final_M=timeline[-1].M if timeline else initial_M,
        peak_M=M_peak,
        min_recovery_pct=min_recovery,
        n_training_days=training_days,
        n_total_days=n_days,
        detected_drops=detected_drops,
    )


def run_evolution_analysis(
    curve_results: list[CurveFitResult] | None = None,
    db_path=None,
    fatigue_params: FatigueParams | None = None,
):
    """Run M(t) evolution analysis for all exercises with fitted curves."""
    if curve_results is None:
        curve_results = run_batch_fitting(db_path)

    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw = load_bodyweight_history(conn)
    conditions = load_tissue_conditions(conn)
    conn.close()

    # Group sets by (exercise_id, date)
    sets_by_ex_date: dict[int, dict[str, list[SetRecord]]] = defaultdict(lambda: defaultdict(list))
    for s in all_sets:
        sets_by_ex_date[s.exercise_id][s.session_date].append(s)

    # Collect injury dates for comparison
    injury_dates_by_tissue: dict[str, list[str]] = defaultdict(list)
    for c in conditions:
        if c.get("status") in ("injured", "tender"):
            injury_dates_by_tissue[c["tissue_name"]].append(c.get("updated_at", "")[:10])

    results = []
    curves_by_ex = {r.exercise_id: r for r in curve_results if r.success}

    print("\n" + "=" * 100)
    print("PHASE 4: LONG-TERM STRENGTH EVOLUTION M(t)")
    print("=" * 100)

    # Estimate initial M from early data using Brzycki 1RM on first 30% of sessions
    for ex_id, curve in sorted(curves_by_ex.items(), key=lambda x: len(sets_by_ex_date.get(x[0], {})), reverse=True):
        sessions = sets_by_ex_date.get(ex_id, {})
        if not sessions:
            continue

        # Compute initial M from earliest sessions using Brzycki
        sorted_dates = sorted(sessions.keys())
        cutoff_idx = max(1, len(sorted_dates) * 30 // 100)
        early_dates = sorted_dates[:cutoff_idx]

        early_1rms = []
        for d in early_dates:
            for s in sessions[d]:
                if s.reps and s.reps > 0 and s.rpe and 5.0 <= s.rpe <= 10.0:
                    body_w = nearest_bodyweight(bw, s.session_date)
                    ew = effective_weight(s, body_w)
                    if ew and ew > 0:
                        rir = 10.0 - s.rpe
                        rtf = s.reps + rir
                        if rtf < 37:
                            brz = ew * 36.0 / (37.0 - rtf)
                            early_1rms.append(brz)

        # Use early Brzycki median if available, otherwise curve.M
        initial_M = float(np.median(early_1rms)) if early_1rms else curve.M

        evo_result = compute_strength_evolution(
            exercise_id=ex_id,
            exercise_name=curve.exercise_name,
            initial_M=initial_M,
            sessions_by_date=dict(sessions),
            bw_history=bw,
            fatigue_params=fatigue_params,
        )
        results.append(evo_result)

    # Print summary
    print(f"\n  Computed M(t) for {len(results)} exercises")

    # Top exercises by data span
    results.sort(key=lambda x: x.n_total_days, reverse=True)
    print(f"\n  {'Exercise':<40} {'Days':>6} {'Train':>6} {'M_init':>8} {'M_final':>8} {'M_peak':>8} {'Min Rec%':>9} {'Drops':>6}")
    print(f"  {'-' * 95}")
    for r in results[:20]:
        print(f"  {r.exercise_name:<40} {r.n_total_days:>6} {r.n_training_days:>6} "
              f"{r.initial_M:>8.1f} {r.final_M:>8.1f} {r.peak_M:>8.1f} "
              f"{r.min_recovery_pct:>8.1f}% {len(r.detected_drops):>6}")

    # Detected drops
    all_drops = []
    for r in results:
        for d in r.detected_drops:
            all_drops.append({**d, "exercise": r.exercise_name})

    if all_drops:
        print(f"\n  Detected performance drops (>3%): {len(all_drops)}")
        for d in sorted(all_drops, key=lambda x: x["drop_pct"])[:10]:
            print(f"    {d['exercise']:<35} {d['date']}  drop={d['drop_pct']:.1f}%  "
                  f"M: {d['M_before']:.1f} -> {d['M_after']:.1f}")

    return results


if __name__ == "__main__":
    results = run_evolution_analysis()
