"""
Test the NEW production model parameters against today's workout data.
Changes: gamma_reg=3.0*avg_fw toward 1.0, DEFAULT_GAMMA=1.0,
         gamma bounds (0.15, 2.5), session boost target 70%.
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
    7:  [(60, 15, 8), (70, 10, 9), (80, 6, 10)],
    25: [(22.5, 20, 5), (27.5, 20, 7), (37.5, 20, 8)],
    67: [(10, 18, 5), (15, 18, 6), (25, 12, 9)],
    11: [(430, 15, 7), (470, 11, 8), (520, 9, 10)],
    81: [(140, 16, 7), (165, 10, 2), (195, 7, 10)],
    69: [(45, 16, 7), (65, 8, 9), (75, 4, 10)],
    41: [(110, 16, 7), (120, 11, 8), (140, 8, 9)],
    80: [(42.5, 15, 8), (47.5, 10, 9), (52.5, 7, 10)],
}


# Old production parameters
OLD_CFG = {
    'gamma_reg': 0.0, 'gamma_prior': 1.0, 'default_gamma': 0.20,
    'gamma_bounds': (0.05, 3.0), 'gamma_inits': [0.15, 0.5, 1.0],
    'session_target_share': None,
}

# New production parameters
NEW_CFG = {
    'gamma_reg': 3.0, 'gamma_prior': 1.0, 'default_gamma': 1.0,
    'gamma_bounds': (0.15, 2.5), 'gamma_inits': [0.15, 0.5, 1.0, 1.5],
    'session_target_share': 0.70,
}


def fit_model(prior_tuples, today_sets_so_far, cfg):
    """Fit using the given config, mimicking production logic."""
    all_obs = list(prior_tuples)
    n_prior = len(all_obs)
    for w, reps, rpe in today_sets_so_far:
        all_obs.append((w, reps, rpe, AS_OF.isoformat()))
    n_session = len(today_sets_so_far)
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

    # Session boost as target share
    if cfg['session_target_share'] and n_session > 0 and n_prior > 0:
        prior_total = float(np.sum(fit_w[:n_prior]))
        session_total = float(np.sum(fit_w[n_prior:]))
        if session_total > 0 and prior_total > 0:
            target = cfg['session_target_share']
            boost = (target * prior_total) / ((1 - target) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior:] *= boost

    brzycki = [brzycki_1rm(w, rf) for w, rf in zip(weights, r_fail) if 0 < rf < 37]
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    unique_w = len(set(weights.tolist()))
    is_tier1 = n_obs >= 5 and unique_w >= 2
    fixed_gamma = None if is_tier1 else cfg['default_gamma']

    gamma_reg = cfg['gamma_reg']
    gamma_prior = cfg['gamma_prior']
    g_bounds = cfg['gamma_bounds']
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
        if fixed_gamma is None and gamma_reg > 0:
            reg_g = gamma_reg * avg_fw * math.log(gamma / gamma_prior) ** 2
        return data_loss + reg_M + reg_g

    best = None
    g_inits = cfg['gamma_inits'] if fixed_gamma is None else [None]
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
                bounds = [(max_W * 1.01, M_prior * 3), (0.5, 200), g_bounds]
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

    configs = {'Old Production': OLD_CFG, 'New (gamma reg + session boost)': NEW_CFG}

    fig, axes = plt.subplots(len(EXERCISES), 2, figsize=(14, len(EXERCISES) * 3.5))

    all_results = {name: {} for name in configs}

    for ex_idx, (eid, ename) in enumerate(EXERCISES):
        prior = get_prior_data(db, eid, AS_OF)
        prior_tuples = [(r[0], r[1], r[2], r[3]) for r in prior]
        today = TODAY_SETS.get(eid, [])

        print(f"\n{ename} ({len(prior_tuples)} prior, {len(today)} today)")

        for cfg_idx, (cfg_name, cfg) in enumerate(configs.items()):
            ax = axes[ex_idx, cfg_idx]

            # Fit on prior only (set 1 prediction)
            fit_prior = fit_model(prior_tuples, [], cfg)

            # Fit on prior + sets 1,2 (set 3 prediction)
            fit_refit = fit_model(prior_tuples, today[:2], cfg) if len(today) >= 2 else None

            # Plot prior data
            if prior_tuples:
                pw = [r[0] for r in prior_tuples]
                pr = [r[1] + (10 - r[2]) for r in prior_tuples]
                ax.scatter(pw, pr, c='gray', alpha=0.3, s=20)

            # Plot today's actual
            tw = [t[0] for t in today]
            tr = [t[1] + (10 - t[2]) for t in today]
            ax.scatter(tw, tr, c='red', s=80, zorder=5, marker='*')
            for i, (w, rf) in enumerate(zip(tw, tr)):
                ax.annotate(f'S{i+1}:{rf:.0f}', (w, rf), fontsize=7,
                           textcoords='offset points', xytext=(5, 5))

            all_w = ([r[0] for r in prior_tuples]) + tw

            # Prior-only curve
            if fit_prior:
                M, k, g, _ = fit_prior
                w_lo = min(all_w) * 0.8
                w_hi = min(M * 0.95, max(all_w) * 1.15)
                w_range = np.linspace(w_lo, w_hi, 200)
                r_curve = [max(0, curve_pred(w, M, k, g)) for w in w_range]
                ax.plot(w_range, r_curve, 'b-', linewidth=1, alpha=0.5,
                       label=f'Prior γ={g:.2f}')

            # Refit curve
            set3_err = None
            if fit_refit:
                M, k, g, _ = fit_refit
                w_lo = min(all_w) * 0.8
                w_hi = min(M * 0.95, max(all_w) * 1.15)
                w_range = np.linspace(w_lo, w_hi, 200)
                r_curve = [max(0, curve_pred(w, M, k, g)) for w in w_range]
                ax.plot(w_range, r_curve, 'r-', linewidth=2,
                       label=f'Refit γ={g:.2f}')

                if len(today) >= 3:
                    w3, r3, rpe3 = today[2]
                    rf3_actual = r3 + (10 - rpe3)
                    rf3_pred = curve_pred(w3, M, k, g)
                    set3_err = rf3_pred - rf3_actual
                    ax.plot(w3, rf3_pred, 'gD', markersize=8, zorder=6)
                    ax.annotate(f'pred={rf3_pred:.1f}\nerr={set3_err:+.1f}',
                               (w3, rf3_pred), fontsize=7, color='green',
                               textcoords='offset points', xytext=(8, -10))

            all_results[cfg_name][ename] = {
                'set3_err': set3_err,
                'prior_gamma': fit_prior[2] if fit_prior else None,
                'refit_gamma': fit_refit[2] if fit_refit else None,
                'refit_M': fit_refit[0] if fit_refit else None,
            }

            if ex_idx == 0:
                ax.set_title(cfg_name, fontsize=10, fontweight='bold')
            if cfg_idx == 0:
                ax.set_ylabel(f'{ename}\nr_fail', fontsize=8)
            ax.set_ylim(bottom=0)
            ax.grid(True, alpha=0.2)
            ax.legend(fontsize=6, loc='upper right')

            err_s = f"Set3 err={set3_err:+.1f}" if set3_err is not None else ""
            g_prior = fit_prior[2] if fit_prior else 0
            g_refit = fit_refit[2] if fit_refit else 0
            print(f"  {cfg_name:35s}: γ_prior={g_prior:.2f} γ_refit={g_refit:.2f} {err_s}")

    plt.tight_layout()
    plt.savefig('plots/new_vs_old_production.png', dpi=130)
    print("\nSaved: plots/new_vs_old_production.png")

    # Summary
    print(f"\n{'='*70}")
    print("SET 3 PREDICTION COMPARISON")
    print(f"{'='*70}")
    print(f"{'Exercise':30s}  {'Old':>8s}  {'New':>8s}  {'Δ':>8s}")
    print("-" * 60)
    old_errs, new_errs = [], []
    for eid, ename in EXERCISES:
        old = all_results['Old Production'].get(ename, {}).get('set3_err')
        new = all_results['New (gamma reg + session boost)'].get(ename, {}).get('set3_err')
        if old is not None and new is not None:
            delta = abs(new) - abs(old)
            print(f"{ename:30s}  {old:>+8.1f}  {new:>+8.1f}  {delta:>+8.1f} {'✓' if delta < 0 else '✗'}")
            old_errs.append(abs(old))
            new_errs.append(abs(new))
    print("-" * 60)
    print(f"{'Mean |error|':30s}  {np.mean(old_errs):>8.1f}  {np.mean(new_errs):>8.1f}")
    print(f"{'Median |error|':30s}  {np.median(old_errs):>8.1f}  {np.median(new_errs):>8.1f}")

    db.close()


if __name__ == '__main__':
    main()
