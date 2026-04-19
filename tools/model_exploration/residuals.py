"""Residuals: actual reps-to-failure minus fresh-curve prediction.

For every RPE-tagged set we compute:
  predicted_rtf = fresh_curve(ew, M, k, gamma)
where (M, k, gamma) is fit from this exercise's data STRICTLY BEFORE the
session's date (leakage-free). The residual rtf_actual - predicted_rtf
is the signal we want to decompose into freshness and fatigue drivers.

For efficiency we fit one curve per (exercise, "as-of month") so that we
don't refit for every session — a monthly granularity is fine because the
fresh curve is meant to be slow-moving.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date as dtdate
from pathlib import Path

import numpy as np

from data_loader import get_connection, load_all_sets, load_bodyweight_history
from strength_curve import (
    build_observations,
    fresh_curve,
    fit_single_exercise,
    CurveFitResult,
)
from features import SetFeatures, compute_features_for_rpe_sets


def _month_bucket(date_str: str) -> str:
    """Return the first day of the month for grouping."""
    d = dtdate.fromisoformat(date_str[:10])
    return d.replace(day=1).isoformat()


def _prev_month(date_str: str) -> str:
    """As-of cutoff: last day of the previous month (strict)."""
    d = dtdate.fromisoformat(date_str[:10]).replace(day=1)
    # Step back to previous month's first day
    if d.month == 1:
        prev = d.replace(year=d.year - 1, month=12)
    else:
        prev = d.replace(month=d.month - 1)
    return prev.isoformat()


@dataclass
class ResidualRow:
    """One RPE set's prediction, residual, and features."""
    session_id: int
    session_date: str
    exercise_id: int
    exercise_name: str
    set_order: int
    set_index_in_exercise: int
    reps: int
    effective_weight: float
    rpe: float
    rtf_actual: float
    rtf_predicted: float
    residual: float   # actual - predicted (positive = model underpredicts)
    # curve-fit diagnostics
    curve_M: float
    curve_k: float
    curve_gamma: float
    curve_tier: str
    curve_n_obs: int
    # freshness + within-session features
    days_since_any_session: float | None
    days_since_same_exercise: float | None
    days_since_same_primary_tissue: float | None
    days_since_same_group: float | None
    acute_load_7d_same_tissue: float
    prior_session_volume: float
    prior_exercise_volume: float
    prior_dose_ar1: float
    exercise_order_in_session: int
    n_primary_tissues: int
    isolation_flag: int
    primary_group: str | None


def _fit_curves_as_of(as_of_date: str, min_rpe_sets: int = 3) -> dict[int, CurveFitResult]:
    """Fit fresh curve per exercise using only data strictly before `as_of_date`.

    Returns {exercise_id: CurveFitResult}. Only successful Tier-1 fits are
    kept (n_rpe_observations >= min_rpe_sets).
    """
    conn = get_connection()
    all_sets = load_all_sets(conn)
    bw_history = load_bodyweight_history(conn)
    conn.close()
    # Filter to sets strictly before as_of_date
    filtered = [s for s in all_sets if s.session_date < as_of_date]
    obs_by_ex = build_observations(filtered, bw_history, rpe_only=True, min_rpe_sets=min_rpe_sets)
    fits: dict[int, CurveFitResult] = {}
    for ex_id, obs_list in obs_by_ex.items():
        r = fit_single_exercise(obs_list, tier="tier1")
        if r.success:
            fits[ex_id] = r
    return fits


def build_residual_table(min_rpe_sets: int = 3) -> list[ResidualRow]:
    """Build leakage-free residuals for every RPE set.

    Cutoff granularity: one fit per distinct session date. The fit uses all
    RPE data with session_date STRICTLY LESS than the target session date.
    """
    feats = compute_features_for_rpe_sets()
    # Group feats by session_date for cache-friendly curve fits
    by_date: dict[str, list[SetFeatures]] = defaultdict(list)
    for f in feats:
        by_date[f.session_date].append(f)

    print(f"[residuals] {len(feats)} RPE sets across {len(by_date)} distinct dates")

    # Pre-load everything once
    conn = get_connection()
    all_sets = load_all_sets(conn)
    bw_history = load_bodyweight_history(conn)
    conn.close()

    rows: list[ResidualRow] = []
    for target_date in sorted(by_date):
        prior_sets = [s for s in all_sets if s.session_date < target_date]
        obs_by_ex = build_observations(
            prior_sets, bw_history, rpe_only=True, min_rpe_sets=min_rpe_sets
        )
        fits: dict[int, CurveFitResult] = {}
        for ex_id, obs_list in obs_by_ex.items():
            # Only fit if the exercise has data for the exercises we need
            r = fit_single_exercise(obs_list, tier="tier1")
            if r.success:
                fits[ex_id] = r

        for f in by_date[target_date]:
            if f.rtf_actual is None:
                continue
            fit = fits.get(f.exercise_id)
            if fit is None:
                continue  # cold start for this exercise
            predicted = float(fresh_curve(f.effective_weight, fit.M, fit.k, fit.gamma))
            rows.append(ResidualRow(
                session_id=f.session_id,
                session_date=f.session_date,
                exercise_id=f.exercise_id,
                exercise_name=f.exercise_name,
                set_order=f.set_order,
                set_index_in_exercise=f.set_index_in_exercise,
                reps=f.reps,
                effective_weight=f.effective_weight,
                rpe=f.rpe,
                rtf_actual=f.rtf_actual,
                rtf_predicted=predicted,
                residual=f.rtf_actual - predicted,
                curve_M=fit.M,
                curve_k=fit.k,
                curve_gamma=fit.gamma,
                curve_tier=fit.tier,
                curve_n_obs=fit.n_rpe_observations,
                days_since_any_session=f.days_since_any_session,
                days_since_same_exercise=f.days_since_same_exercise,
                days_since_same_primary_tissue=f.days_since_same_primary_tissue,
                days_since_same_group=f.days_since_same_group,
                acute_load_7d_same_tissue=f.acute_load_7d_same_tissue,
                prior_session_volume=f.prior_session_volume,
                prior_exercise_volume=f.prior_exercise_volume,
                prior_dose_ar1=f.prior_dose_ar1,
                exercise_order_in_session=f.exercise_order_in_session,
                n_primary_tissues=f.n_primary_tissues,
                isolation_flag=f.isolation_flag,
                primary_group=f.primary_group,
            ))
    return rows


if __name__ == "__main__":
    rows = build_residual_table()
    print(f"\nBuilt {len(rows)} residual rows")
    if rows:
        r = np.array([row.residual for row in rows])
        print(f"Residual stats: mean={r.mean():+.2f} median={np.median(r):+.2f} "
              f"std={r.std():.2f} rmse={np.sqrt((r**2).mean()):.2f}")
        # Per-set breakdown
        for idx in sorted(set(row.set_index_in_exercise for row in rows)):
            sub = np.array([row.residual for row in rows if row.set_index_in_exercise == idx])
            print(f"  Set #{idx}: n={len(sub):3d}  mean={sub.mean():+5.2f}  "
                  f"median={np.median(sub):+5.2f}  rmse={np.sqrt((sub**2).mean()):.2f}")
