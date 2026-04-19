"""Set-1 freshness analysis.

For every first-set-per-(session, exercise) with RPE, regress residual
(rtf_actual - rtf_predicted_fresh) on freshness features:
  - days_since_any_session
  - days_since_same_exercise
  - days_since_same_primary_tissue
  - days_since_same_group
  - acute_load_7d_same_tissue (7-day rolling volume on same primary tissues)
  - exercise_order_in_session
  - isolation_flag

Outputs:
  - Scatter plots of residual vs each feature
  - Stratified RMSE tables (freshness buckets)
  - Fixed-effects regression (per-exercise intercept) to isolate freshness
    signal from exercise-specific baseline bias

Saves plots under plots/set1_freshness/
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from residuals import build_residual_table, ResidualRow


PLOT_DIR = Path(__file__).parent / "plots" / "set1_freshness"
PLOT_DIR.mkdir(parents=True, exist_ok=True)


def _rmse(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else float("nan")


def _bias_table(residuals: np.ndarray, buckets: list[tuple[str, np.ndarray]]) -> list[tuple]:
    """For each bucket mask, return (label, n, mean_resid, rmse)."""
    out = []
    for label, mask in buckets:
        sub = residuals[mask]
        out.append((label, int(mask.sum()), float(sub.mean()) if len(sub) else float("nan"), _rmse(sub)))
    return out


def _scatter(ax, x: np.ndarray, y: np.ndarray, xlabel: str, color: str = "steelblue"):
    """Scatter plot with linear regression line."""
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 3:
        ax.text(0.5, 0.5, f"n={len(x)} too few", transform=ax.transAxes,
                ha="center", va="center")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("residual")
        return None
    ax.scatter(x, y, s=18, alpha=0.5, color=color)
    coef = np.polyfit(x, y, 1)
    xs = np.linspace(x.min(), x.max(), 50)
    ax.plot(xs, np.polyval(coef, xs), color="crimson", lw=1.5, label=f"slope={coef[0]:+.3f}")
    ax.axhline(0, color="black", lw=0.5, alpha=0.4)
    # pearson r
    if x.std() > 0 and y.std() > 0:
        r = float(np.corrcoef(x, y)[0, 1])
    else:
        r = float("nan")
    ax.set_title(f"{xlabel}   n={len(x)}  r={r:+.2f}")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("residual (actual - predicted rtf)")
    ax.legend(loc="best", fontsize=8)
    return coef


def analyze_set1(rows: list[ResidualRow]) -> None:
    set1 = [r for r in rows if r.set_index_in_exercise == 1]
    print(f"\n=== SET-1 FRESHNESS ANALYSIS ===  n={len(set1)}\n")
    if not set1:
        return

    # Arrays
    resid = np.array([r.residual for r in set1])

    def getf(attr: str) -> np.ndarray:
        return np.array([getattr(r, attr) if getattr(r, attr) is not None else np.nan
                         for r in set1], dtype=float)

    features = {
        "days_since_any_session": getf("days_since_any_session"),
        "days_since_same_exercise": getf("days_since_same_exercise"),
        "days_since_same_primary_tissue": getf("days_since_same_primary_tissue"),
        "days_since_same_group": getf("days_since_same_group"),
        "acute_load_7d_same_tissue": getf("acute_load_7d_same_tissue"),
        "exercise_order_in_session": getf("exercise_order_in_session"),
    }

    print(f"Overall Set-1 residual: mean={resid.mean():+.2f}  median={np.median(resid):+.2f}  "
          f"std={resid.std():.2f}  rmse={_rmse(resid):.2f}\n")

    # Stratified buckets
    print("STRATIFIED BIAS TABLES")
    print("-" * 72)
    for feat_name, vals in features.items():
        ok = ~np.isnan(vals)
        v = vals[ok]
        r = resid[ok]
        if len(v) < 6:
            continue
        # Quartile-based buckets
        qs = np.quantile(v, [0.25, 0.5, 0.75])
        buckets = [
            (f"  {feat_name:<36} <= {qs[0]:.2f}", v <= qs[0]),
            (f"  {feat_name:<36} ({qs[0]:.2f}, {qs[1]:.2f}]", (v > qs[0]) & (v <= qs[1])),
            (f"  {feat_name:<36} ({qs[1]:.2f}, {qs[2]:.2f}]", (v > qs[1]) & (v <= qs[2])),
            (f"  {feat_name:<36} > {qs[2]:.2f}", v > qs[2]),
        ]
        print(f"\n{feat_name}")
        print(f"  {'bucket':<48} {'n':>4} {'mean_resid':>11} {'rmse':>8}")
        for label, mask in buckets:
            sub = r[mask]
            print(f"  {label:<48} {int(mask.sum()):>4} {sub.mean():>+11.2f} {_rmse(sub):>8.2f}")

    # Scatter grid
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    ax_list = axes.flatten()
    colors = ["steelblue", "teal", "darkorange", "purple", "crimson", "olive"]
    slopes = {}
    for ax, (name, vals), col in zip(ax_list, features.items(), colors):
        coef = _scatter(ax, vals, resid, name, color=col)
        if coef is not None:
            slopes[name] = coef[0]
    fig.suptitle(f"Set-1 residuals vs freshness features (n={len(set1)} first-RPE-sets)")
    fig.tight_layout()
    out = PLOT_DIR / "scatter_grid.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print(f"\nSaved {out}")

    # Grouped by group (Push/Pull/Legs/Shoulders/Core)
    by_group: dict[str, list[ResidualRow]] = {}
    for r in set1:
        g = r.primary_group or "Other"
        by_group.setdefault(g, []).append(r)
    print("\nBY PRIMARY GROUP")
    print(f"  {'group':<10} {'n':>4} {'mean_resid':>12} {'rmse':>8}")
    for g, rlist in sorted(by_group.items()):
        rr = np.array([x.residual for x in rlist])
        print(f"  {g:<10} {len(rr):>4} {rr.mean():>+12.2f} {_rmse(rr):>8.2f}")

    # By isolation vs compound
    iso = np.array([r.residual for r in set1 if r.isolation_flag == 1])
    cmp = np.array([r.residual for r in set1 if r.isolation_flag == 0])
    print("\nBY ISOLATION FLAG")
    if len(iso):
        print(f"  isolation  n={len(iso):>4}  mean={iso.mean():+.2f}  rmse={_rmse(iso):.2f}")
    if len(cmp):
        print(f"  compound   n={len(cmp):>4}  mean={cmp.mean():+.2f}  rmse={_rmse(cmp):.2f}")

    # Simple fixed-effects: de-mean residual by exercise, then regress on features
    print("\nEXERCISE-DEMEANED REGRESSION (removes per-exercise bias)")
    by_ex: dict[int, list[int]] = {}
    for i, r in enumerate(set1):
        by_ex.setdefault(r.exercise_id, []).append(i)
    demeaned = resid.copy()
    for idxs in by_ex.values():
        demeaned[idxs] -= demeaned[idxs].mean()

    print(f"  {'feature':<36} {'slope':>10} {'r(pearson)':>12} {'n':>6}")
    for fname, vals in features.items():
        ok = ~np.isnan(vals)
        x = vals[ok]
        y = demeaned[ok]
        if len(x) < 5 or x.std() == 0 or y.std() == 0:
            continue
        slope = float(np.polyfit(x, y, 1)[0])
        r = float(np.corrcoef(x, y)[0, 1])
        print(f"  {fname:<36} {slope:>+10.4f} {r:>+12.3f} {len(x):>6d}")

    # Joint multi-feature OLS on demeaned residuals
    print("\nJOINT OLS (exercise-demeaned; standardised features)")
    feat_names = list(features.keys())
    X_parts = []
    ok_mask = np.ones(len(set1), dtype=bool)
    for fn in feat_names:
        ok_mask &= ~np.isnan(features[fn])
    if ok_mask.sum() > len(feat_names) + 3:
        X = np.column_stack([features[fn][ok_mask] for fn in feat_names])
        # Standardize
        X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)
        y = demeaned[ok_mask]
        X_aug = np.column_stack([np.ones(len(y)), X])
        beta, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
        yhat = X_aug @ beta
        ss_res = ((y - yhat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        print(f"  intercept = {beta[0]:+.3f}   n={ok_mask.sum()}   R^2(demeaned)={r2:.3f}")
        print(f"  {'feature':<36} {'coef (std-units)':>18}")
        for fn, b in zip(feat_names, beta[1:]):
            print(f"  {fn:<36} {b:>+18.3f}")
    else:
        print("  Not enough complete rows for joint OLS")


if __name__ == "__main__":
    rows = build_residual_table()
    analyze_set1(rows)
