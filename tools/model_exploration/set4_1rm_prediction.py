"""
Predict a theoretical 4th-set 1RM from sets 1-3 + prior data.
Uses the new production parameters (gamma reg + session boost).
Shows the full curve including downward inflection toward M.
"""
import sqlite3
import sys
import os
import math
import datetime
import numpy as np
from scipy.optimize import minimize as sp_minimize

sys.path.insert(0, os.path.dirname(__file__))
from targeted_improvements import (
    DB_PATH, EXERCISES, AS_OF, get_prior_data,
    rpe_confidence, recency_weight, brzycki_1rm, curve_pred,
)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TODAY_SETS = {
    7:  [(60, 15, 8), (70, 10, 9), (80, 6, 10)],       # Barbell Curl
    25: [(22.5, 20, 5), (27.5, 20, 7), (37.5, 20, 8)],  # Face Pulls
    67: [(10, 18, 5), (15, 18, 6), (25, 12, 9)],         # Incline Hammer Curl
    11: [(430, 15, 7), (470, 11, 8), (520, 9, 10)],      # Leg Press
    81: [(140, 16, 7), (165, 10, 2), (195, 7, 10)],      # Neutral Grip Lat Pulldown
    69: [(45, 16, 7), (65, 8, 9), (75, 4, 10)],          # Preacher Curl
    41: [(110, 16, 7), (120, 11, 8), (140, 8, 9)],       # Seated Cable Row V-grip
    80: [(42.5, 15, 8), (47.5, 10, 9), (52.5, 7, 10)],   # Straight-Bar Cable Curl
}

# New production parameters
GAMMA_REG = 3.0
GAMMA_PRIOR = 1.0
DEFAULT_GAMMA = 1.0
GAMMA_BOUNDS = (0.15, 2.5)
GAMMA_INITS = [0.15, 0.5, 1.0, 1.5]
SESSION_TARGET_SHARE = 0.70


def fit_model(prior_tuples, today_sets):
    """Fit using new production params on prior + all provided today sets."""
    all_obs = list(prior_tuples)
    n_prior = len(all_obs)
    for w, reps, rpe in today_sets:
        all_obs.append((w, reps, rpe, AS_OF.isoformat()))
    n_session = len(today_sets)
    n_obs = len(all_obs)

    if n_obs < 3:
        return None

    weights = np.array([r[0] for r in all_obs])
    reps_arr = np.array([r[1] for r in all_obs])
    rpes = np.array([r[2] for r in all_obs])
    r_fail = reps_arr + (10 - rpes)

    ages = []
    for r in all_obs:
        ts = r[3] if len(r) > 3 else AS_OF.isoformat()
        d = datetime.date.fromisoformat(ts[:10])
        ages.append(max(0, (AS_OF - d).days))

    conf = np.array([rpe_confidence(rpe) for rpe in rpes])
    rec = np.array([recency_weight(a) for a in ages])
    fit_w = conf * rec

    # Session boost
    if n_session > 0 and n_prior > 0:
        prior_total = float(np.sum(fit_w[:n_prior]))
        session_total = float(np.sum(fit_w[n_prior:]))
        if session_total > 0 and prior_total > 0:
            target = SESSION_TARGET_SHARE
            boost = (target * prior_total) / ((1 - target) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior:] *= boost

    brzycki = [brzycki_1rm(w, rf) for w, rf in zip(weights, r_fail) if 0 < rf < 37]
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    unique_w = len(set(weights.tolist()))
    is_tier1 = n_obs >= 5 and unique_w >= 2
    fixed_gamma = None if is_tier1 else DEFAULT_GAMMA

    avg_fw = float(np.mean(fit_w))

    def loss(params):
        if fixed_gamma is not None:
            M, k = params
            gamma = fixed_gamma
        else:
            M, k, gamma = params
        if M <= 0 or k <= 0 or gamma < 0.01:
            return 1e12
        pred = np.array([curve_pred(w, M, k, gamma) for w in weights])
        resid = (r_fail - pred) ** 2
        data_loss = float(np.sum(fit_w * resid))

        reg_M = 10.0 * avg_fw * math.log(M / M_prior) ** 2
        reg_g = 0.0
        if fixed_gamma is None and GAMMA_REG > 0:
            reg_g = GAMMA_REG * avg_fw * math.log(gamma / GAMMA_PRIOR) ** 2
        return data_loss + reg_M + reg_g

    best = None
    g_inits = GAMMA_INITS if fixed_gamma is None else [None]
    M_factors = [1.1, 1.3, 1.5, 2.0]
    max_W = float(weights.max())

    for mf in M_factors:
        for gi in g_inits:
            M_init = M_prior * mf
            k_init = float(np.median(r_fail))
            if fixed_gamma is not None:
                x0 = [M_init, k_init]
                bounds = [(max_W * 1.01, M_prior * 3), (0.5, 200)]
            else:
                x0 = [M_init, k_init, gi]
                bounds = [(max_W * 1.01, M_prior * 3), (0.5, 200), GAMMA_BOUNDS]
            try:
                res = sp_minimize(loss, x0, method='L-BFGS-B', bounds=bounds)
                if best is None or res.fun < best[3]:
                    if fixed_gamma is not None:
                        best = (abs(res.x[0]), abs(res.x[1]), fixed_gamma, res.fun)
                    else:
                        best = (abs(res.x[0]), abs(res.x[1]), abs(res.x[2]), res.fun)
            except Exception:
                pass

    return best


def main():
    db = sqlite3.connect(DB_PATH)

    fig, axes = plt.subplots(4, 2, figsize=(16, 20))
    axes = axes.flatten()

    print("=" * 70)
    print("SET 4 / 1RM PREDICTION (fit on prior + all 3 sets)")
    print("=" * 70)

    results = []

    for idx, (eid, ename) in enumerate(EXERCISES):
        prior = get_prior_data(db, eid, AS_OF)
        prior_tuples = [(r[0], r[1], r[2], r[3]) for r in prior]
        today = TODAY_SETS.get(eid, [])

        # Fit on prior + all 3 sets
        fit = fit_model(prior_tuples, today)
        if fit is None:
            print(f"\n{ename}: insufficient data")
            continue

        M, k, gamma, loss_val = fit

        # Predict theoretical 4th set targets
        # 1RM attempt: weight where predicted r_fail = 1 (RPE 10, 1 rep)
        # Heavy single: weight where r_fail = 2 (RPE 9, 1 rep)
        # Heavy triple: weight where r_fail = 3 (RPE 10, 3 reps)
        from scipy.optimize import brentq

        def r_at_w(w):
            return curve_pred(w, M, k, gamma)

        # Find weight for various r_fail targets
        targets = [
            ("1RM (r_fail=1)", 1),
            ("Heavy single (r_fail=2)", 2),
            ("Heavy triple (r_fail=4)", 4),
            ("Heavy 5 (r_fail=6)", 6),
        ]

        print(f"\n{'=' * 60}")
        print(f"{ename} (prior={len(prior_tuples)}, today={len(today)})")
        print(f"  Fitted: M={M:.1f}, k={k:.1f}, γ={gamma:.2f}")
        print(f"  Today's sets (actual):")
        for i, (w, reps, rpe) in enumerate(today):
            rf = reps + (10 - rpe)
            pred_rf = curve_pred(w, M, k, gamma)
            print(f"    Set {i+1}: {w}lb × {reps} @ RPE {rpe} "
                  f"(r_fail={rf}) pred={pred_rf:.1f}")

        print(f"  Predicted 4th set options:")
        set4_preds = {}
        for label, target_rf in targets:
            # Search for weight where r_at_w = target_rf
            try:
                w_lo = max(today[-1][0] if today else 0, 1)
                w_hi = M * 0.999
                if r_at_w(w_lo) < target_rf:
                    print(f"    {label}: can't reach (curve too low at max set weight)")
                    continue
                if r_at_w(w_hi) > target_rf:
                    w_pred = w_hi
                else:
                    w_pred = brentq(lambda w: r_at_w(w) - target_rf, w_lo, w_hi)
                pred_rf_check = r_at_w(w_pred)
                print(f"    {label}: {w_pred:.1f}lb (pred r_fail={pred_rf_check:.1f})")
                set4_preds[label] = (w_pred, target_rf)
            except Exception as e:
                print(f"    {label}: error ({e})")

        results.append({
            'name': ename, 'M': M, 'k': k, 'gamma': gamma,
            'prior': prior_tuples, 'today': today, 'set4': set4_preds,
        })

        # Plot
        ax = axes[idx]
        all_w = [r[0] for r in prior_tuples] + [t[0] for t in today]
        w_min = min(all_w) * 0.7 if all_w else 10
        # Extend curve all the way to M (the 1RM) to show downward inflection
        w_max = M * 0.999
        w_range = np.linspace(w_min, w_max, 300)
        r_curve = [max(0, curve_pred(w, M, k, gamma)) for w in w_range]

        # Curve
        ax.plot(w_range, r_curve, 'b-', linewidth=2, label=f'Curve (γ={gamma:.2f}, M={M:.0f})')

        # Prior data
        if prior_tuples:
            pw = [r[0] for r in prior_tuples]
            pr = [r[1] + (10 - r[2]) for r in prior_tuples]
            ax.scatter(pw, pr, c='gray', alpha=0.3, s=25, label='Prior sets')

        # Today's sets
        tw = [t[0] for t in today]
        tr = [t[1] + (10 - t[2]) for t in today]
        ax.scatter(tw, tr, c='red', s=100, zorder=5, marker='*', label='Today (actual)')
        for i, (w, rf) in enumerate(zip(tw, tr)):
            ax.annotate(f'S{i+1}: {rf:.0f}r', (w, rf), fontsize=8,
                       textcoords='offset points', xytext=(6, 6), fontweight='bold')

        # Mark set-4 predictions
        colors = ['green', 'orange', 'purple', 'brown']
        for ci, (label, (w_pred, target_rf)) in enumerate(set4_preds.items()):
            ax.plot(w_pred, target_rf, 'D', color=colors[ci % len(colors)],
                   markersize=10, zorder=6)
            ax.annotate(f'{w_pred:.0f}lb\n{label.split("(")[0].strip()}',
                       (w_pred, target_rf), fontsize=7, color=colors[ci % len(colors)],
                       textcoords='offset points', xytext=(8, -12))

        # Mark M (theoretical 0-rep max)
        ax.axvline(M, color='red', linestyle='--', alpha=0.4, linewidth=1)
        ax.annotate(f'M={M:.0f}', (M, max(r_curve) * 0.9), fontsize=8,
                   color='red', alpha=0.6, ha='right')

        ax.set_title(ename, fontsize=11, fontweight='bold')
        ax.set_xlabel('Weight (lb)')
        ax.set_ylabel('r_fail (reps to failure)')
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.2)
        ax.legend(fontsize=7, loc='upper right')

    plt.suptitle('Set 4 / 1RM Prediction (fit on prior + sets 1-3)',
                fontsize=14, fontweight='bold', y=1.01)
    plt.tight_layout()
    plt.savefig('plots/set4_1rm_prediction.png', dpi=130, bbox_inches='tight')
    print(f"\nSaved: plots/set4_1rm_prediction.png")

    # Summary table
    print(f"\n{'=' * 70}")
    print("SUMMARY: Predicted 1RM and Heavy Single weights")
    print(f"{'=' * 70}")
    print(f"{'Exercise':30s}  {'M':>6s}  {'γ':>5s}  {'1RM':>8s}  {'Single':>8s}  {'Triple':>8s}  {'5-rep':>8s}")
    print("-" * 85)
    for r in results:
        s4 = r['set4']
        w_1rm = f"{s4['1RM (r_fail=1)'][0]:.0f}lb" if '1RM (r_fail=1)' in s4 else "—"
        w_single = f"{s4['Heavy single (r_fail=2)'][0]:.0f}lb" if 'Heavy single (r_fail=2)' in s4 else "—"
        w_triple = f"{s4['Heavy triple (r_fail=4)'][0]:.0f}lb" if 'Heavy triple (r_fail=4)' in s4 else "—"
        w_five = f"{s4['Heavy 5 (r_fail=6)'][0]:.0f}lb" if 'Heavy 5 (r_fail=6)' in s4 else "—"
        print(f"{r['name']:30s}  {r['M']:6.0f}  {r['gamma']:5.2f}  {w_1rm:>8s}  {w_single:>8s}  {w_triple:>8s}  {w_five:>8s}")

    db.close()


if __name__ == '__main__':
    main()
