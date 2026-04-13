"""Plot strength curves for today's workout (Session 389, Apr 12) with progressive refit.

For each weighted exercise, shows:
  - Historical fit (before any sets logged)
  - After set 1, after set 2, after set 3 refits
  - Actual sets as scatter points
  - Model coefficients (M, k, γ, tier) annotated on each subplot
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import sqlite3
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from datetime import date, timedelta

from app.strength_model import (
    CurveFit,
    fresh_curve,
    DEFAULT_GAMMA,
    MIN_RPE_FOR_FIT,
    MIN_SETS_TIER1,
    MIN_SETS_TIER2,
    MIN_DISTINCT_WEIGHTS_TIER1,
    SESSION_TARGET_SHARE,
    GAMMA_DEMOTE_THRESHOLD,
    _rpe_confidence,
    _recency_weights,
    _filter_stale_sessions,
    _estimate_M_bounds,
    _identifiability_score,
    _fit_params,
)


DB_PATH = Path(__file__).resolve().parent.parent / "production_backup_2026-04-12_170723.db"
SESSION_ID = 389
SESSION_DATE = date(2026, 4, 12)


def load_session_exercises(conn):
    """Load exercises and their sets from today's session."""
    c = conn.cursor()
    rows = c.execute("""
        SELECT e.id, e.name, e.allow_heavy_loading,
               e.load_input_mode, e.laterality
        FROM workout_sets wk
        JOIN exercises e ON e.id = wk.exercise_id
        WHERE wk.session_id = ?
          AND wk.weight > 0
        GROUP BY e.id, e.name, e.allow_heavy_loading, e.load_input_mode, e.laterality
        ORDER BY MIN(wk.set_order)
    """, (SESSION_ID,)).fetchall()

    exercises = []
    for eid, name, heavy, load_mode, lat in rows:
        sets = c.execute("""
            SELECT wk.weight, wk.reps, wk.rpe, wk.set_order
            FROM workout_sets wk
            WHERE wk.session_id = ? AND wk.exercise_id = ?
              AND wk.weight > 0
            ORDER BY wk.set_order
        """, (SESSION_ID, eid)).fetchall()
        exercises.append({
            "id": eid,
            "name": name,
            "allow_heavy_loading": heavy,
            "load_input_mode": load_mode or "external_weight",
            "sets": [{"weight": w, "reps": r, "rpe": rpe} for w, r, rpe, _ in sets],
        })
    return exercises


def load_historical_sets(conn, exercise_id, days=30):
    """Load RPE-qualifying sets from the last N days (excluding today's session)."""
    c = conn.cursor()
    cutoff = SESSION_DATE - timedelta(days=days)

    rows = c.execute("""
        SELECT wk.weight, wk.reps, wk.rpe, ws.date, ws.id as session_id
        FROM workout_sets wk
        JOIN workout_sessions ws ON wk.session_id = ws.id
        WHERE wk.exercise_id = ?
          AND ws.date >= ?
          AND ws.date <= ?
          AND wk.rpe IS NOT NULL
          AND wk.rpe >= ?
          AND wk.reps IS NOT NULL
          AND wk.weight IS NOT NULL
        ORDER BY ws.date, wk.set_order
    """, (exercise_id, cutoff.isoformat(), SESSION_DATE.isoformat(), MIN_RPE_FOR_FIT)).fetchall()

    historical = []
    for w, r, rpe, d, sid in rows:
        if sid != SESSION_ID:
            age = (SESSION_DATE - date.fromisoformat(d)).days
            historical.append({"weight": w, "reps": r, "rpe": rpe, "age_days": age})
    return historical


def fit_from_observations(weights, reps_fail, confidences, ages):
    """Run our fitting pipeline on arrays of observations."""
    if len(weights) < MIN_SETS_TIER2:
        return None

    W = np.array(weights)
    r = np.array(reps_fail)
    conf = np.array(confidences)
    recency = _recency_weights(ages)
    fit_w = conf * recency

    distinct_w = len(set(round(w, 1) for w in weights))
    n_obs = len(weights)
    tier = "tier1" if n_obs >= MIN_SETS_TIER1 and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1 else "tier2"

    M_lower, M_upper, M_prior = _estimate_M_bounds(weights, reps_fail)
    ident = _identifiability_score(weights, reps_fail)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, fixed_gamma
    )

    if tier == "tier1" and gamma_fit < GAMMA_DEMOTE_THRESHOLD:
        tier = "tier2"
        M_fit, k_fit, gamma_fit, success = _fit_params(
            W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, DEFAULT_GAMMA
        )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit, k=k_fit, gamma=gamma_fit,
        n_obs=n_obs, rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier, identifiability=ident,
    )


def refit_with_session_boost(hist_w, hist_r, hist_c, hist_a,
                              new_w, new_r, new_c):
    """Refit combining historical + new session sets with session boost.

    Runs t-test with session data as anchor so historical sessions that
    don't match current performance are dropped.
    """
    # Combine historical + session, then filter with session as anchor
    all_w = hist_w + new_w
    all_r = hist_r + new_r
    all_c = hist_c + new_c
    all_a = hist_a + [0.0] * len(new_w)

    all_w, all_r, all_c, all_a = _filter_stale_sessions(
        all_w, all_r, all_c, all_a
    )

    if len(all_w) < MIN_SETS_TIER2:
        return None

    W = np.array(all_w)
    r = np.array(all_r)
    conf = np.array(all_c)
    recency = _recency_weights(all_a)
    fit_w = conf * recency

    # Session boost — session data (age=0) always kept as anchor
    n_session = len(new_w)
    n_prior = len(all_w) - n_session
    if n_session > 0 and n_prior > 0:
        prior_total = float(np.sum(fit_w[:n_prior]))
        session_total = float(np.sum(fit_w[n_prior:]))
        if session_total > 0 and prior_total > 0:
            target = SESSION_TARGET_SHARE
            boost = (target * prior_total) / ((1 - target) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior:] *= boost

    n_obs = len(all_w)
    distinct_w = len(set(round(w, 1) for w in all_w))
    tier = "tier1" if n_obs >= MIN_SETS_TIER1 and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1 else "tier2"

    M_lower, M_upper, M_prior_est = _estimate_M_bounds(all_w, all_r)
    ident = _identifiability_score(all_w, all_r)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior_est, lambda_M, fixed_gamma
    )

    if tier == "tier1" and gamma_fit < GAMMA_DEMOTE_THRESHOLD:
        tier = "tier2"
        M_fit, k_fit, gamma_fit, success = _fit_params(
            W, r, fit_w, M_lower, M_upper, M_prior_est, lambda_M, DEFAULT_GAMMA
        )

    predicted = fresh_curve(W, M_fit, k_fit, gamma_fit)
    residuals = r - predicted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    return CurveFit(
        M=M_fit, k=k_fit, gamma=gamma_fit,
        n_obs=n_obs, rmse=rmse,
        max_observed_weight=float(np.max(W)),
        fit_tier=tier, identifiability=ident,
    )


def plot_exercise(ax, ex_data, conn):
    """Plot curves for one exercise on a given axes."""
    name = ex_data["name"]
    sets = ex_data["sets"]

    # Load all historical data (unfiltered)
    hist = load_historical_sets(conn, ex_data["id"])
    raw_hist_w = [h["weight"] for h in hist]
    raw_hist_r = [h["reps"] + (10.0 - h["rpe"]) for h in hist]
    raw_hist_c = [_rpe_confidence(h["rpe"]) for h in hist]
    raw_hist_a = [float(h["age_days"]) for h in hist]

    # Filter historical for the historical-only fit (no session anchor)
    hist_w, hist_r, hist_c, hist_a = _filter_stale_sessions(
        raw_hist_w[:], raw_hist_r[:], raw_hist_c[:], raw_hist_a[:]
    )

    # Determine weight range for plotting
    all_weights = raw_hist_w + [s["weight"] for s in sets]
    if not all_weights:
        ax.set_title(name, fontsize=9, fontweight="bold")
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
        return

    w_min = min(all_weights) * 0.7
    w_max = max(all_weights) * 1.3
    w_range = np.linspace(w_min, w_max, 200)

    colors = ["#6B7280", "#3B82F6", "#F59E0B", "#EF4444", "#8B5CF6"]
    fits = []

    # Historical fit (no session sets, uses historical-only filter)
    hist_fit = fit_from_observations(hist_w, hist_r, hist_c, hist_a)
    fits.append(("Historical", hist_fit, colors[0]))

    # Progressive refits after each set — pass RAW historical so the
    # refit function can re-run the t-test with session data as anchor
    session_w, session_r, session_c = [], [], []
    for i, s in enumerate(sets):
        rpe = s["rpe"] or 7.0
        rir = 10.0 - rpe
        session_w.append(s["weight"])
        session_r.append(s["reps"] + rir)
        session_c.append(_rpe_confidence(rpe))

        refit = refit_with_session_boost(
            raw_hist_w[:], raw_hist_r[:], raw_hist_c[:], raw_hist_a[:],
            session_w[:], session_r[:], session_c[:]
        )
        fits.append((f"After Set {i+1}", refit, colors[min(i+1, len(colors)-1)]))

    # Plot curves
    for label, fit, color in fits:
        if fit is None:
            continue
        r_pred = fresh_curve(w_range, fit.M, fit.k, fit.gamma)
        r_pred = np.clip(r_pred, 0, 50)
        style = "--" if label == "Historical" else "-"
        alpha = 0.5 if label == "Historical" else 0.9
        ax.plot(w_range, r_pred, style, color=color, alpha=alpha, linewidth=1.5)

    # Plot actual sets
    for i, s in enumerate(sets):
        rpe = s["rpe"] or 7.0
        rir = 10.0 - rpe
        r_fail = s["reps"] + rir
        ax.scatter(s["weight"], r_fail, s=60, zorder=5,
                   color=colors[min(i+1, len(colors)-1)], edgecolors="white", linewidth=0.8)
        ax.annotate(f"S{i+1}: {s['weight']}×{s['reps']}\nRPE {rpe:.0f}",
                    (s["weight"], r_fail),
                    textcoords="offset points", xytext=(6, 4),
                    fontsize=5.5, color=colors[min(i+1, len(colors)-1)])

    # Plot historical points (faded)
    if hist_w:
        ax.scatter(hist_w, hist_r, s=20, alpha=0.3, color="#9CA3AF",
                   edgecolors="white", linewidth=0.5, zorder=2)

    # Coefficient annotation box
    coeff_lines = []
    for label, fit, color in fits:
        if fit is None:
            coeff_lines.append(f"{label}: no fit")
            continue
        coeff_lines.append(
            f"{label}: M={fit.M:.0f}  k={fit.k:.1f}  γ={fit.gamma:.2f}  "
            f"[{fit.fit_tier}, n={fit.n_obs}]"
        )
    coeff_text = "\n".join(coeff_lines)
    ax.text(0.02, 0.98, coeff_text, transform=ax.transAxes,
            fontsize=5.5, fontfamily="monospace",
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="#D1D5DB", alpha=0.9))

    ax.set_title(name, fontsize=9, fontweight="bold", pad=4)
    ax.set_xlabel("Weight (lb)", fontsize=7)
    ax.set_ylabel("Reps to failure", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.15)


def main():
    conn = sqlite3.connect(str(DB_PATH))

    exercises = load_session_exercises(conn)
    print(f"Found {len(exercises)} weighted exercises in session {SESSION_ID}:")
    for ex in exercises:
        print(f"  {ex['name']}: {len(ex['sets'])} sets")

    n = len(exercises)
    cols = 2
    rows = (n + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(14, 4.2 * rows))
    fig.suptitle(
        f"Saturday 4/12 Workout (Session {SESSION_ID}) — Strength Curves with Progressive Refit\n"
        "r_fresh(W) = k · (M/W − 1)^γ   |   T-test filter + auto tier demotion",
        fontsize=11, fontweight="bold", y=0.995
    )

    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for i, ex in enumerate(exercises):
        print(f"  Plotting {ex['name']}...")
        plot_exercise(axes_flat[i], ex, conn)

    for j in range(n, len(axes_flat)):
        axes_flat[j].set_visible(False)

    legend_elements = [
        Line2D([0], [0], color="#6B7280", linestyle="--", alpha=0.5, label="Historical fit"),
        Line2D([0], [0], color="#3B82F6", label="After set 1"),
        Line2D([0], [0], color="#F59E0B", label="After set 2"),
        Line2D([0], [0], color="#EF4444", label="After set 3"),
    ]
    fig.legend(handles=legend_elements, loc="lower center", ncol=4,
               fontsize=7, frameon=False, bbox_to_anchor=(0.5, 0.001))

    plt.tight_layout(rect=[0, 0.025, 1, 0.97])
    out = Path(__file__).parent / "today_curves.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    print(f"\nSaved to {out}")

    conn.close()


if __name__ == "__main__":
    main()
