"""Two-compartment fatigue decomposition for Sets 2+.

Model: for every RPE set with set_index_in_exercise >= 2, decompose the
residual (actual rtf - fresh prediction) into:

    residual_{j,e,s} = alpha_j + beta_e,s + eps

where:
  alpha_j   = per-session intercept (global "bad day" fatigue)
  beta_e,s  = within-exercise local fatigue: modelled as -theta * prior_dose_ar1
              OR as additive per-set-index (discrete fatigue per set #).

We also include a fresh-curve per-exercise intercept (baseline bias) so
we don't confuse model-bias with session-fatigue.

Four nested models are compared:
  M0: fresh curve only (alpha = beta = 0)
  M1: + per-exercise intercept (removes systematic baseline bias)
  M2: M1 + per-session intercept alpha_j
  M3: M2 + local fatigue beta (AR(1) dose OR discrete set-index dummies)

RMSE is reported on Set-2 and Set-3 subsets separately.
Plots:
  - session_intercepts.png: histogram of fitted alpha_j + ranked bar chart
  - local_fatigue.png: residual (after M2) vs prior_dose_ar1
  - model_comparison_rmse.png: RMSE by model and set index
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from residuals import build_residual_table, ResidualRow


PLOT_DIR = Path(__file__).parent / "plots" / "fatigue"
PLOT_DIR.mkdir(parents=True, exist_ok=True)


def _rmse(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def _fit_group_means(values: np.ndarray, groups: np.ndarray) -> dict:
    """Return mean of `values` per unique group id."""
    out: dict = {}
    for g in np.unique(groups):
        out[g] = float(values[groups == g].mean())
    return out


def analyze_fatigue(rows: list[ResidualRow]) -> None:
    # Use all sets, not just set >= 2, so the per-exercise intercept is
    # estimated from the full RPE dataset (including Set 1). Local-fatigue is
    # still evaluated primarily on Set 2+ via metric stratification.
    print(f"\n=== FATIGUE DECOMPOSITION ===  n={len(rows)}\n")
    if not rows:
        return

    y = np.array([r.residual for r in rows])
    exercise = np.array([r.exercise_id for r in rows])
    session = np.array([r.session_id for r in rows])
    set_idx = np.array([r.set_index_in_exercise for r in rows])
    prior_dose = np.array([r.prior_dose_ar1 for r in rows])
    prior_ex_vol = np.array([r.prior_exercise_volume for r in rows])
    prior_sess_vol = np.array([r.prior_session_volume for r in rows])
    ex_order = np.array([r.exercise_order_in_session for r in rows])

    # ---- M0: fresh curve only ----
    pred_m0 = np.zeros_like(y)
    resid_m0 = y - pred_m0

    # ---- M1: + per-exercise intercept ----
    ex_mean = _fit_group_means(y, exercise)
    pred_m1 = np.array([ex_mean[e] for e in exercise])
    resid_m1 = y - pred_m1

    # ---- M2: M1 + per-session intercept (alternating estimation) ----
    # Iterate: given per-ex intercepts, estimate per-session mean residual;
    # given per-session intercepts, re-estimate per-ex intercept; repeat.
    ex_int = dict(ex_mean)
    sess_int: dict = {s: 0.0 for s in np.unique(session)}
    for _ in range(20):
        # Update session intercepts
        adj = y - np.array([ex_int[e] for e in exercise])
        new_sess = _fit_group_means(adj, session)
        # Update exercise intercepts
        adj2 = y - np.array([new_sess[s] for s in session])
        new_ex = _fit_group_means(adj2, exercise)
        # Convergence check
        d = max(
            max(abs(new_sess[s] - sess_int[s]) for s in sess_int),
            max(abs(new_ex[e] - ex_int[e]) for e in ex_int),
        )
        sess_int = new_sess
        ex_int = new_ex
        if d < 1e-4:
            break
    pred_m2 = np.array([ex_int[e] + sess_int[s] for e, s in zip(exercise, session)])
    resid_m2 = y - pred_m2

    # ---- M3a: M2 + linear local fatigue on prior_dose_ar1 ----
    # Fit theta via OLS on the residual after M2
    # resid_m2 ≈ -theta * prior_dose + eps  (expect negative coefficient)
    X = prior_dose.reshape(-1, 1)
    if X.std() > 0:
        X_c = X - X.mean()
        theta_lin = float((X_c[:, 0] * resid_m2).sum() / (X_c[:, 0] ** 2).sum())
    else:
        theta_lin = 0.0
    pred_m3a_local = theta_lin * (prior_dose - prior_dose.mean())
    resid_m3a = resid_m2 - pred_m3a_local

    # ---- M3b: M2 + additive per-(exercise, set-index) dummies ----
    # Captures exercise-specific fatigue that is NOT captured by prior_dose
    set_fatigue: dict = {}
    for ex_id in np.unique(exercise):
        for s_idx in np.unique(set_idx):
            mask = (exercise == ex_id) & (set_idx == s_idx)
            if mask.sum() >= 2:
                set_fatigue[(ex_id, s_idx)] = float(resid_m2[mask].mean())
    pred_m3b_local = np.array([
        set_fatigue.get((e, si), 0.0) for e, si in zip(exercise, set_idx)
    ])
    resid_m3b = resid_m2 - pred_m3b_local

    # ---- M3c: M2 + global per-set-index fatigue (alpha + beta_s) ----
    set_fat_global: dict = {}
    for s_idx in np.unique(set_idx):
        mask = set_idx == s_idx
        if mask.sum() >= 2:
            set_fat_global[s_idx] = float(resid_m2[mask].mean())
    pred_m3c_local = np.array([set_fat_global.get(si, 0.0) for si in set_idx])
    resid_m3c = resid_m2 - pred_m3c_local

    # ---- RMSE by model and set index ----
    print(f"{'Model':<45} {'overall':>9} {'set1':>9} {'set2':>9} {'set3':>9}")
    print("-" * 82)
    for label, resid in [
        ("M0 fresh curve only", resid_m0),
        ("M1 + per-exercise intercept", resid_m1),
        ("M2 M1 + per-session intercept", resid_m2),
        ("M3a M2 + linear local fatigue (prior_dose)", resid_m3a),
        ("M3b M2 + per-(ex, set_idx) fatigue dummies", resid_m3b),
        ("M3c M2 + global per-set-index fatigue", resid_m3c),
    ]:
        parts = [_rmse(resid)]
        for si in [1, 2, 3]:
            m = set_idx == si
            parts.append(_rmse(resid[m]))
        print(f"{label:<45} {parts[0]:>9.2f} {parts[1]:>9.2f} {parts[2]:>9.2f} {parts[3]:>9.2f}")

    # Per-set mean residual (bias) under each model
    print("\n--- Mean bias (signed) by set index ---")
    print(f"{'Model':<45} {'set1':>9} {'set2':>9} {'set3':>9}")
    for label, resid in [
        ("M0 fresh curve only", resid_m0),
        ("M1 + per-exercise intercept", resid_m1),
        ("M2 M1 + per-session intercept", resid_m2),
        ("M3a + linear local fatigue", resid_m3a),
        ("M3b + per-(ex, set_idx) dummies", resid_m3b),
        ("M3c + global per-set-index", resid_m3c),
    ]:
        parts = []
        for si in [1, 2, 3]:
            m = set_idx == si
            parts.append(resid[m].mean() if m.any() else float("nan"))
        print(f"{label:<45} {parts[0]:>+9.2f} {parts[1]:>+9.2f} {parts[2]:>+9.2f}")

    # Linear local-fatigue coefficient
    print(f"\nLinear local-fatigue coef (theta): {theta_lin:+.4f}  "
          f"(resid_m2 ~= theta * (prior_dose_ar1 - mean))")
    print(f"  prior_dose_ar1 stats: mean={prior_dose.mean():.2f}  std={prior_dose.std():.2f}")

    # Global per-set-index fatigue
    print("\nGlobal per-set-index fatigue (resid_m2 mean by set #):")
    for si in sorted(set_fat_global):
        n = int((set_idx == si).sum())
        print(f"  set #{si}: n={n:3d}  mean_resid_after_M2={set_fat_global[si]:+.3f}")

    # --- Plots ---
    # 1) Session intercepts
    sess_vals = np.array(list(sess_int.values()))
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    axes[0].hist(sess_vals, bins=20, color="steelblue", alpha=0.8)
    axes[0].axvline(0, color="black", lw=0.8)
    axes[0].set_title(f"Session intercepts (n={len(sess_vals)})\nstd={sess_vals.std():.2f}")
    axes[0].set_xlabel("alpha_j (resid contribution)")
    sorted_sess = sorted(sess_int.items(), key=lambda kv: kv[1])
    xs = np.arange(len(sorted_sess))
    axes[1].bar(xs, [v for _, v in sorted_sess], color="teal", alpha=0.7)
    axes[1].axhline(0, color="black", lw=0.8)
    axes[1].set_title("Session intercepts, sorted")
    axes[1].set_xlabel("session rank")
    axes[1].set_ylabel("alpha_j")
    fig.tight_layout()
    fig.savefig(PLOT_DIR / "session_intercepts.png", dpi=110)
    plt.close(fig)

    # 2) Local fatigue scatter
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    _plot_local(axes[0], prior_dose, resid_m2, "prior_dose_ar1")
    _plot_local(axes[1], prior_ex_vol, resid_m2, "prior_exercise_volume")
    _plot_local(axes[2], prior_sess_vol, resid_m2, "prior_session_volume")
    fig.suptitle("Within-session fatigue signal (residual_after_M2 vs prior load)")
    fig.tight_layout()
    fig.savefig(PLOT_DIR / "local_fatigue.png", dpi=110)
    plt.close(fig)

    # 3) Model comparison bars
    labels = ["M0", "M1", "M2", "M3a", "M3b", "M3c"]
    resids_by_model = [resid_m0, resid_m1, resid_m2, resid_m3a, resid_m3b, resid_m3c]
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.22
    xs = np.arange(len(labels))
    for i, si in enumerate([1, 2, 3]):
        rmses = [_rmse(r[set_idx == si]) for r in resids_by_model]
        ax.bar(xs + i * width, rmses, width, label=f"set {si}")
    ax.set_xticks(xs + width)
    ax.set_xticklabels(labels)
    ax.set_ylabel("RMSE (reps)")
    ax.legend()
    ax.set_title("Model RMSE by set index")
    fig.tight_layout()
    fig.savefig(PLOT_DIR / "model_comparison_rmse.png", dpi=110)
    plt.close(fig)

    # 4) Session-intercept correlates (if we have enough sessions)
    if len(sess_vals) >= 10:
        # Build per-session features from the row population
        sess_feats: dict = defaultdict(lambda: {"prior_session_volume_max": 0.0,
                                                "ex_order_max": 0,
                                                "date": None})
        for r in rows:
            ff = sess_feats[r.session_id]
            ff["prior_session_volume_max"] = max(ff["prior_session_volume_max"],
                                                  r.prior_session_volume)
            ff["ex_order_max"] = max(ff["ex_order_max"], r.exercise_order_in_session)
            ff["date"] = r.session_date
        keys = list(sess_int.keys())
        alpha = np.array([sess_int[k] for k in keys])
        volmax = np.array([sess_feats[k]["prior_session_volume_max"] for k in keys])
        exmax = np.array([sess_feats[k]["ex_order_max"] for k in keys])
        print("\nSESSION-INTERCEPT CORRELATES")
        for name, v in [("prior_session_volume_max", volmax), ("ex_order_max", exmax)]:
            if v.std() > 0:
                r_ = float(np.corrcoef(v, alpha)[0, 1])
                print(f"  corr(alpha_j, {name}) = {r_:+.3f}")


def _plot_local(ax, x: np.ndarray, y: np.ndarray, xlabel: str):
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    ax.scatter(x, y, s=14, alpha=0.4, color="darkorange")
    ax.axhline(0, color="black", lw=0.5)
    if len(x) > 3 and x.std() > 0:
        coef = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 40)
        ax.plot(xs, np.polyval(coef, xs), color="crimson", lw=1.5,
                label=f"slope={coef[0]:+.4f}")
        r = float(np.corrcoef(x, y)[0, 1])
        ax.set_title(f"{xlabel}   r={r:+.2f}")
        ax.legend(loc="best", fontsize=8)
    else:
        ax.set_title(xlabel)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("residual_after_M2")


if __name__ == "__main__":
    rows = build_residual_table()
    analyze_fatigue(rows)
