"""
Set-3 prediction from sets 1+2: analytic approach.

Instead of fitting 3 parameters with weighted loss, use today's 2 points
to DIRECTLY solve the curve given M from prior data, then predict set 3.

With 2 points and known M, gamma and k are analytically determined.
"""
import sqlite3
import sys
import os
import math
import datetime
import numpy as np

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


def get_M_prior(prior_tuples, today_sets):
    """Estimate 1RM from all available data, preferring high-RPE recent data."""
    brzycki = []
    # Prior data
    for w, reps, rpe, *_ in prior_tuples:
        rf = reps + (10 - rpe)
        if 0 < rf < 37:
            brzycki.append(brzycki_1rm(w, rf))
    # Today's data (high weight = better 1RM estimate)
    for w, reps, rpe in today_sets:
        rf = reps + (10 - rpe)
        if 0 < rf < 37:
            brzycki.append(brzycki_1rm(w, rf))
    if brzycki:
        return float(np.median(brzycki))
    all_w = [r[0] for r in prior_tuples] + [t[0] for t in today_sets]
    return max(all_w) * 1.3 if all_w else 100.0


def analytic_solve(w1, rf1, w2, rf2, M):
    """Given two (weight, r_fail) points and M, solve for gamma and k.

    r = k * (M/W - 1)^gamma
    rf1/rf2 = ((M/w1 - 1) / (M/w2 - 1))^gamma
    gamma = ln(rf1/rf2) / ln((M/w1-1)/(M/w2-1))
    k = rf1 / (M/w1-1)^gamma
    """
    if w1 >= M or w2 >= M or rf1 <= 0 or rf2 <= 0:
        return None, None
    ratio_r = rf1 / rf2
    a1 = M / w1 - 1
    a2 = M / w2 - 1
    if a1 <= 0 or a2 <= 0 or abs(a1 - a2) < 1e-10:
        return None, None
    ratio_a = a1 / a2
    if ratio_a <= 0 or ratio_r <= 0:
        return None, None
    gamma = math.log(ratio_r) / math.log(ratio_a)
    gamma = max(0.1, min(3.0, gamma))  # clamp
    k = rf1 / (a1 ** gamma)
    return gamma, k


def solve_with_M_search(w1, rf1, w2, rf2, M_prior, w3=None):
    """Try a range of M values near M_prior, pick the one that gives best fit.
    If w3 is provided, also check how well it predicts there (optional)."""
    best = None
    for M_mult in np.linspace(0.8, 1.5, 50):
        M = M_prior * M_mult
        if M <= max(w1, w2) * 1.05:
            continue
        gamma, k = analytic_solve(w1, rf1, w2, rf2, M)
        if gamma is None:
            continue
        # Score: how well does this fit the 2 points + gamma plausibility
        pred1 = curve_pred(w1, M, k, gamma)
        pred2 = curve_pred(w2, M, k, gamma)
        fit_err = (pred1 - rf1) ** 2 + (pred2 - rf2) ** 2
        gamma_penalty = 5.0 * (math.log(gamma / 1.0)) ** 2
        M_penalty = 5.0 * (math.log(M / M_prior)) ** 2
        score = fit_err + gamma_penalty + M_penalty
        if best is None or score < best[4]:
            best = (M, k, gamma, M_mult, score)
    return best


def full_optimization(prior_tuples, today_sets_so_far, session_boost, gamma_reg):
    """Standard optimization with configurable boost and gamma reg."""
    from scipy.optimize import minimize as sp_minimize

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
    for i, r in enumerate(all_data):
        ts = r[3] if len(r) > 3 else AS_OF.isoformat()
        d = datetime.date.fromisoformat(ts[:10])
        age = max(0, (AS_OF - d).days)
        conf = rpe_confidence(rpes[i])
        rec = recency_weight(age)
        w_val = conf * rec
        if age == 0:
            w_val *= session_boost
        fw.append(w_val)
    fw = np.array(fw)

    brzycki = [brzycki_1rm(w, rf) for w, rf in zip(weights, r_fail) if 0 < rf < 37]
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    def loss(params):
        M, k, gamma = params
        if M <= 0 or k <= 0 or gamma < 0.05:
            return 1e12
        pred = np.array([curve_pred(w, M, k, gamma) for w in weights])
        resid = (r_fail - pred) ** 2
        M_reg = 10.0 * math.log(M / M_prior) ** 2
        g_reg = gamma_reg * (math.log(gamma / 1.0)) ** 2
        return float(np.sum(fw * resid) + M_reg + g_reg)

    best = None
    for g_init in [0.3, 0.7, 1.0, 1.5]:
        try:
            res = sp_minimize(loss, [M_prior, 30.0, g_init], method='Nelder-Mead',
                             options={'maxiter': 5000})
            M, k, gamma = abs(res.x[0]), abs(res.x[1]), abs(res.x[2])
            if best is None or res.fun < best[3]:
                best = (M, k, gamma, res.fun)
        except Exception:
            pass
    return best


def main():
    db = sqlite3.connect(DB_PATH)

    configs = {
        'A: Production\n(optimizer, no reg)': 'optimizer',
        'B: Optimizer\nγ-reg + 10x boost': 'optimizer_reg',
        'C: Optimizer\nγ-reg + 100x boost': 'optimizer_heavy',
        'D: Analytic\n(2-point solve)': 'analytic',
        'E: Analytic\n(M-search)': 'analytic_search',
    }

    n_ex = len(FOCUS_EXERCISES)
    n_cfg = len(configs)
    fig, axes = plt.subplots(n_ex, n_cfg, figsize=(n_cfg * 5, n_ex * 4))

    set3_errors = {name: [] for name in configs}

    for ex_idx, (eid, ename) in enumerate(FOCUS_EXERCISES):
        prior = get_prior_data(db, eid, AS_OF)
        prior_tuples = [(r[0], r[1], r[2], r[3]) for r in prior]
        today = TODAY_SETS.get(eid, [])

        print(f"\n{'='*70}")
        print(f"{ename}: {len(prior_tuples)} prior, {len(today)} today")

        # Today's sets 1 and 2 as r_fail
        if len(today) >= 2:
            w1, r1, rpe1 = today[0]
            rf1 = r1 + (10 - rpe1)
            w2, r2, rpe2 = today[1]
            rf2 = r2 + (10 - rpe2)
        else:
            continue

        M_prior = get_M_prior(prior_tuples, today[:2])
        print(f"  M_prior = {M_prior:.1f}")

        if len(today) >= 3:
            w3, r3, rpe3 = today[2]
            rf3_actual = r3 + (10 - rpe3)
        else:
            rf3_actual = None

        for cfg_idx, (cfg_name, method) in enumerate(configs.items()):
            ax = axes[ex_idx, cfg_idx]

            if method == 'optimizer':
                result = full_optimization(prior_tuples, today[:2], 1.0, 0.0)
            elif method == 'optimizer_reg':
                result = full_optimization(prior_tuples, today[:2], 10.0, 20.0)
            elif method == 'optimizer_heavy':
                result = full_optimization(prior_tuples, today[:2], 100.0, 20.0)
            elif method == 'analytic':
                gamma, k = analytic_solve(w1, rf1, w2, rf2, M_prior)
                result = (M_prior, k, gamma, 0) if gamma is not None else None
            elif method == 'analytic_search':
                res = solve_with_M_search(w1, rf1, w2, rf2, M_prior)
                result = (res[0], res[1], res[2], res[4]) if res else None

            if result is None:
                continue

            M, k, gamma, _ = result

            # Predict set 3
            set3_err = None
            if rf3_actual is not None:
                rf3_pred = curve_pred(w3, M, k, gamma)
                set3_err = rf3_pred - rf3_actual
                set3_errors[cfg_name].append((ename, set3_err))

            # ── Plot ──
            if prior_tuples:
                pw = [r[0] for r in prior_tuples]
                pr = [r[1] + (10 - r[2]) for r in prior_tuples]
                ax.scatter(pw, pr, c='gray', alpha=0.3, s=20, zorder=2)

            tw = [t[0] for t in today]
            tr = [t[1] + (10 - t[2]) for t in today]
            ax.scatter(tw, tr, c='red', s=80, zorder=5, marker='*')
            for i, (w, rf) in enumerate(zip(tw, tr)):
                ax.annotate(f'S{i+1}:{rf:.0f}', (w, rf), fontsize=7,
                           textcoords='offset points', xytext=(5, 5))

            # Curve
            all_w = ([r[0] for r in prior_tuples]) + tw
            w_lo = min(all_w) * 0.8
            w_hi = min(M * 0.98, max(all_w) * 1.2)
            w_range = np.linspace(w_lo, w_hi, 200)
            r_curve = [max(0, curve_pred(w, M, k, gamma)) for w in w_range]
            ax.plot(w_range, r_curve, 'r-', linewidth=2,
                   label=f'γ={gamma:.2f} M={M:.0f}')

            # Set 3 prediction marker
            if set3_err is not None:
                rf3_pred = curve_pred(w3, M, k, gamma)
                ax.plot(w3, rf3_pred, 'gD', markersize=8, zorder=6)
                ax.annotate(f'pred={rf3_pred:.1f}\nerr={set3_err:+.1f}',
                           (w3, rf3_pred), fontsize=7, color='green',
                           textcoords='offset points', xytext=(8, -10))

            if ex_idx == 0:
                ax.set_title(cfg_name.replace('\n', ' '), fontsize=9, fontweight='bold')
            if cfg_idx == 0:
                ax.set_ylabel(f'{ename}\nr_fail', fontsize=8)
            ax.set_ylim(bottom=0)
            ax.grid(True, alpha=0.2)
            ax.legend(fontsize=6, loc='upper right')

            short = cfg_name.split('\n')[0]
            err_s = f"err={set3_err:+.1f}" if set3_err is not None else ""
            print(f"  {short:25s} γ={gamma:.2f} M={M:.0f} k={k:.1f} {err_s}")

    plt.tight_layout()
    plt.savefig('plots/set3_prediction.png', dpi=130)
    print("\nSaved: plots/set3_prediction.png")

    # Summary
    print(f"\n{'='*70}")
    print("SET 3 PREDICTION (from sets 1+2)")
    print(f"{'='*70}")
    header = f"{'Exercise':30s}"
    for name in configs:
        header += f"  {name.split(chr(10))[0]:>12s}"
    print(header + f"  {'Actual':>8s}")
    print("-" * 110)

    for eid, ename in FOCUS_EXERCISES:
        today = TODAY_SETS.get(eid, [])
        if len(today) < 3:
            continue
        rf_actual = today[2][1] + (10 - today[2][2])
        line = f"{ename:30s}"
        for name in configs:
            errs = [e for en, e in set3_errors[name] if en == ename]
            if errs:
                line += f"  {errs[0]:>+12.1f}"
            else:
                line += f"  {'N/A':>12s}"
        line += f"  {rf_actual:>8.0f}"
        print(line)

    print("-" * 110)
    line = f"{'Mean |error|':30s}"
    for name in configs:
        errs = [abs(e) for _, e in set3_errors[name]]
        line += f"  {np.mean(errs):>12.1f}" if errs else f"  {'N/A':>12s}"
    print(line)

    line = f"{'Median |error|':30s}"
    for name in configs:
        errs = [abs(e) for _, e in set3_errors[name]]
        line += f"  {np.median(errs):>12.1f}" if errs else f"  {'N/A':>12s}"
    print(line)

    db.close()


if __name__ == '__main__':
    main()
