"""
Investigate and fix curve shape problems:
1. Prior data is clustered at similar weights → gamma unconstrained → bad shape
2. Need gamma regularization toward ~1.0 (Brzycki-like)
3. Need much stronger session refit for set-3 prediction
"""
import sqlite3
import sys
import os
import math
import datetime
import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(__file__))
from targeted_improvements import (
    DB_PATH, EXERCISES, AS_OF, get_prior_data,
    rpe_confidence, recency_weight, brzycki_1rm, curve_pred,
)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TODAY_SETS = {
    7:  [(60, 15, 8), (70, 10, 9), (80, 6, 10)],
    25: [(22.5, 20, 5), (27.5, 20, 7), (37.5, 20, 8)],
    67: [(10, 18, 5), (15, 18, 6), (25, 12, 9)],
    11: [(430, 15, 7), (470, 11, 8), (520, 9, 10)],
    81: [(140, 16, 7), (165, 10, 2), (195, 7, 10)],
    69: [(45, 16, 7), (65, 8, 9), (75, 4, 10)],
    41: [(110, 16, 7), (120, 11, 8), (140, 8, 9)],
    80: [(42.5, 15, 8), (47.5, 10, 9), (52.5, 7, 10)],
}

FOCUS_EXERCISES = [
    (11, 'Leg Press'),
    (81, 'Neutral Grip Lat Pulldown'),
    (41, 'Seated Cable Row V-grip'),
    (69, 'Preacher Curl'),
    (7, 'Barbell Curl'),
    (80, 'Straight-Bar Cable Curl'),
    (25, 'Face Pulls'),
    (67, 'Incline Hammer Curl'),
]


def fit_curve(weights, r_fail, fw, M_prior, gamma_reg=0.0, gamma_target=1.0,
              gamma_bounds=(0.05, 5.0), fix_gamma=None):
    """Fit M, k, gamma with optional gamma regularization."""
    def loss(params):
        if fix_gamma is not None:
            M, k = params
            gamma = fix_gamma
        else:
            M, k, gamma = params
        if M <= 0 or k <= 0 or gamma < 0.05:
            return 1e12
        pred = np.array([curve_pred(w, M, k, gamma) for w in weights])
        resid = (r_fail - pred) ** 2
        M_reg = 10.0 * math.log(M / M_prior) ** 2
        g_reg = gamma_reg * (math.log(gamma / gamma_target)) ** 2
        return float(np.sum(fw * resid) + M_reg + g_reg)

    best = None
    for g_init in [0.3, 0.7, 1.0, 1.5]:
        try:
            if fix_gamma is not None:
                x0 = [M_prior, 30.0]
                res = minimize(loss, x0, method='Nelder-Mead',
                              options={'maxiter': 5000, 'xatol': 1e-6})
                M, k = abs(res.x[0]), abs(res.x[1])
                gamma = fix_gamma
            else:
                x0 = [M_prior, 30.0, g_init]
                res = minimize(loss, x0, method='Nelder-Mead',
                              options={'maxiter': 5000, 'xatol': 1e-6})
                M, k, gamma = abs(res.x[0]), abs(res.x[1]), abs(res.x[2])
                gamma = max(gamma_bounds[0], min(gamma_bounds[1], gamma))
            if best is None or res.fun < best[3]:
                best = (M, k, gamma, res.fun)
        except Exception:
            pass
    return best


def prepare_data(prior_tuples, today_sets_so_far, session_boost=1.0):
    """Combine prior + today's completed sets into arrays."""
    all_data = list(prior_tuples)
    for w, reps, rpe in today_sets_so_far:
        all_data.append((w, reps, rpe, AS_OF.isoformat()))

    if len(all_data) < 3:
        return None

    weights = np.array([r[0] for r in all_data])
    reps = np.array([r[1] for r in all_data])
    rpes = np.array([r[2] for r in all_data])
    r_fail = reps + (10 - rpes)

    fw = []
    for r in all_data:
        ts = r[3] if len(r) > 3 else AS_OF.isoformat()
        d = datetime.date.fromisoformat(ts[:10])
        age = max(0, (AS_OF - d).days)
        conf = rpe_confidence(rpes[len(fw)])
        rec = recency_weight(age)
        w_val = conf * rec
        if age == 0:
            w_val *= session_boost
        fw.append(w_val)
    fw = np.array(fw)

    brzycki = [brzycki_1rm(w, rf) for w, rf in zip(weights, r_fail) if 0 < rf < 37]
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    return weights, r_fail, fw, M_prior


def main():
    db = sqlite3.connect(DB_PATH)

    # Configs to test
    configs = {
        'A: Production\n(no gamma reg)': {
            'gamma_reg': 0.0, 'session_boost': 1.0,
        },
        'B: Gamma reg\ntoward 1.0': {
            'gamma_reg': 20.0, 'gamma_target': 1.0, 'session_boost': 1.0,
        },
        'C: Gamma reg\n+ 10x boost': {
            'gamma_reg': 20.0, 'gamma_target': 1.0, 'session_boost': 10.0,
        },
        'D: Gamma reg\n+ 50x boost': {
            'gamma_reg': 20.0, 'gamma_target': 1.0, 'session_boost': 50.0,
        },
        'E: Strong gamma\nreg + 50x boost': {
            'gamma_reg': 50.0, 'gamma_target': 1.0, 'session_boost': 50.0,
        },
    }

    n_ex = len(FOCUS_EXERCISES)
    n_cfg = len(configs)
    fig, axes = plt.subplots(n_ex, n_cfg, figsize=(n_cfg * 5, n_ex * 4))

    # Track set-3 prediction errors
    set3_errors = {name: [] for name in configs}

    for ex_idx, (eid, ename) in enumerate(FOCUS_EXERCISES):
        prior = get_prior_data(db, eid, AS_OF)
        prior_tuples = [(r[0], r[1], r[2], r[3]) for r in prior]
        today = TODAY_SETS.get(eid, [])

        print(f"\n{'='*70}")
        print(f"{ename}: {len(prior_tuples)} prior sets, {len(today)} today")

        # Show prior data weight distribution
        if prior_tuples:
            pws = [r[0] for r in prior_tuples]
            unique_w = sorted(set(pws))
            print(f"  Prior weights: {unique_w}")

        for cfg_idx, (cfg_name, cfg) in enumerate(configs.items()):
            ax = axes[ex_idx, cfg_idx] if n_ex > 1 else axes[cfg_idx]

            gamma_reg = cfg.get('gamma_reg', 0.0)
            gamma_target = cfg.get('gamma_target', 1.0)
            session_boost = cfg.get('session_boost', 1.0)

            # Fit 1: Prior only → predict set 1
            data = prepare_data(prior_tuples, [], session_boost=1.0)
            if data:
                weights, r_fail, fw, M_prior = data
                fit_prior = fit_curve(weights, r_fail, fw, M_prior,
                                     gamma_reg=gamma_reg, gamma_target=gamma_target)
            else:
                fit_prior = None

            # Fit 2: Prior + sets 1,2 → predict set 3
            if len(today) >= 2:
                data2 = prepare_data(prior_tuples, today[:2],
                                    session_boost=session_boost)
                if data2:
                    weights2, r_fail2, fw2, M_prior2 = data2
                    fit_refit = fit_curve(weights2, r_fail2, fw2, M_prior2,
                                         gamma_reg=gamma_reg, gamma_target=gamma_target)
                else:
                    fit_refit = None
            else:
                fit_refit = None

            # Set 3 prediction error
            set3_err = None
            if fit_refit and len(today) >= 3:
                w3, r3, rpe3 = today[2]
                rf3_actual = r3 + (10 - rpe3)
                rf3_pred = curve_pred(w3, fit_refit[0], fit_refit[1], fit_refit[2])
                set3_err = rf3_pred - rf3_actual
                set3_errors[cfg_name].append((ename, set3_err))

            # ── Plot ──
            # Prior data points
            if prior_tuples:
                pw = [r[0] for r in prior_tuples]
                pr = [r[1] + (10 - r[2]) for r in prior_tuples]
                ax.scatter(pw, pr, c='gray', alpha=0.4, s=25, zorder=2)

            # Today's actual
            if today:
                tw = [t[0] for t in today]
                tr = [t[1] + (10 - t[2]) for t in today]
                ax.scatter(tw, tr, c='red', s=80, zorder=5, marker='*')
                for i, (w, rf) in enumerate(zip(tw, tr)):
                    ax.annotate(f'S{i+1}', (w, rf), fontsize=7,
                               textcoords='offset points', xytext=(5, 5))

            # Prior-only curve
            all_w = ([r[0] for r in prior_tuples] if prior_tuples else []) + \
                    ([t[0] for t in today] if today else [])
            if fit_prior and all_w:
                M, k, g, _ = fit_prior
                w_lo = min(all_w) * 0.8
                w_hi = min(M * 0.98, max(all_w) * 1.3)
                w_range = np.linspace(w_lo, w_hi, 200)
                r_curve = [max(0, curve_pred(w, M, k, g)) for w in w_range]
                ax.plot(w_range, r_curve, 'b-', linewidth=1.5, alpha=0.6,
                       label=f'Prior (γ={g:.2f})')

            # Refit curve (after sets 1,2)
            if fit_refit and all_w:
                M, k, g, _ = fit_refit
                w_lo = min(all_w) * 0.8
                w_hi = min(M * 0.98, max(all_w) * 1.3)
                w_range = np.linspace(w_lo, w_hi, 200)
                r_curve = [max(0, curve_pred(w, M, k, g)) for w in w_range]
                ax.plot(w_range, r_curve, 'r-', linewidth=2,
                       label=f'Refit (γ={g:.2f})')

                # Show set 3 prediction
                if set3_err is not None:
                    w3 = today[2][0]
                    rf3_pred = curve_pred(w3, M, k, g)
                    ax.plot(w3, rf3_pred, 'gD', markersize=8, zorder=6)
                    ax.annotate(f'pred={rf3_pred:.1f}\nerr={set3_err:+.1f}',
                               (w3, rf3_pred), fontsize=7,
                               textcoords='offset points', xytext=(8, -10),
                               color='green')

            if ex_idx == 0:
                ax.set_title(cfg_name.replace('\n', ' '), fontsize=9, fontweight='bold')
            if cfg_idx == 0:
                ax.set_ylabel(f'{ename}\nReps to failure', fontsize=8)
            ax.set_ylim(bottom=0)
            ax.grid(True, alpha=0.2)
            ax.legend(fontsize=6, loc='upper right')

            # Print summary
            if fit_refit:
                M, k, g, _ = fit_refit
                err_str = f"err={set3_err:+.1f}" if set3_err is not None else "N/A"
                short_name = cfg_name.split('\n')[0]
                if cfg_idx == 0:
                    print(f"  {'Config':25s} {'γ':>5s} {'M':>6s} {'k':>6s} {'Set3':>8s}")
                print(f"  {short_name:25s} {g:5.2f} {M:6.1f} {k:6.1f} {err_str:>8s}")

    plt.tight_layout()
    plt.savefig('plots/curve_shape_fix.png', dpi=130)
    print("\nSaved: plots/curve_shape_fix.png")

    # Summary table
    print(f"\n{'='*70}")
    print("SET 3 PREDICTION ERRORS (refit from sets 1+2)")
    print(f"{'='*70}")
    print(f"{'Exercise':30s}", end="")
    for name in configs:
        short = name.split('\n')[0]
        print(f"  {short:>10s}", end="")
    print(f"  {'Actual':>8s}")
    print("-" * 100)

    for ex_idx, (eid, ename) in enumerate(FOCUS_EXERCISES):
        today = TODAY_SETS.get(eid, [])
        if len(today) < 3:
            continue
        rf_actual = today[2][1] + (10 - today[2][2])
        print(f"{ename:30s}", end="")
        for name in configs:
            errs = [e for en, e in set3_errors[name] if en == ename]
            if errs:
                print(f"  {errs[0]:>+10.1f}", end="")
            else:
                print(f"  {'N/A':>10s}", end="")
        print(f"  {rf_actual:>8.0f}")

    # Mean absolute error
    print("-" * 100)
    print(f"{'Mean |error|':30s}", end="")
    for name in configs:
        errs = [abs(e) for _, e in set3_errors[name]]
        if errs:
            print(f"  {np.mean(errs):>10.1f}", end="")
        else:
            print(f"  {'N/A':>10s}", end="")
    print()

    db.close()


if __name__ == '__main__':
    main()
