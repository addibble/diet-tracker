"""
Investigate curve shape issues — why some exercises lack downward inflection.
Focus on predicting set 3 from sets 1+2 (the real use case).
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
pass  # strength_curve import removed — using scipy directly

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


def fit_and_inspect(db, eid, ename, prior_data, extra_obs=None):
    """Fit a curve and return parameters + predictions."""
    all_data = list(prior_data)
    if extra_obs:
        all_data.extend(extra_obs)

    if len(all_data) < 3:
        return None

    weights = np.array([r[0] for r in all_data])
    reps = np.array([r[1] for r in all_data])
    rpes = np.array([r[2] for r in all_data])
    r_fail = reps + (10 - rpes)

    ages = []
    boost_flags = []
    for r in all_data:
        ts = r[3] if len(r) > 3 else AS_OF.isoformat()
        d = datetime.date.fromisoformat(ts[:10])
        age = max(0, (AS_OF - d).days)
        ages.append(age)
        boost_flags.append(age == 0)

    conf = np.array([rpe_confidence(rpe) for rpe in rpes])
    rec = np.array([recency_weight(a) for a in ages])
    fw = conf * rec
    # Boost in-session data
    for i, is_today in enumerate(boost_flags):
        if is_today:
            fw[i] *= 10.0  # strong session boost

    brzycki = [brzycki_1rm(w, rf) for w, rf in zip(weights, r_fail) if 0 < rf < 37]
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    unique_w = len(set(weights.tolist()))
    n = len(all_data)

    # Fit with different gamma values to see effect
    results = {}
    for gamma_init in [0.15, 0.5, 1.0, 1.5]:
        try:
            from scipy.optimize import minimize

            def loss(params):
                M, k, gamma = params
                if M <= 0 or k <= 0 or gamma <= 0.05:
                    return 1e12
                pred = np.array([curve_pred(w, M, k, gamma) for w in weights])
                resid = (r_fail - pred) ** 2
                reg = 10.0 * math.log(M / M_prior) ** 2
                return float(np.sum(fw * resid) + reg)

            if unique_w >= 2 and n >= 5:
                x0 = [M_prior, 30.0, gamma_init]
                res = minimize(loss, x0, method='Nelder-Mead',
                              options={'maxiter': 5000, 'xatol': 1e-6})
                M, k, gamma = res.x
                results[f'free_g{gamma_init}'] = (abs(M), abs(k), abs(gamma), res.fun)
            else:
                # Fixed gamma
                def loss2(params):
                    M, k = params
                    if M <= 0 or k <= 0:
                        return 1e12
                    pred = np.array([curve_pred(w, M, k, gamma_init) for w in weights])
                    resid = (r_fail - pred) ** 2
                    reg = 10.0 * math.log(M / M_prior) ** 2
                    return float(np.sum(fw * resid) + reg)
                x0 = [M_prior, 30.0]
                res = minimize(loss2, x0, method='Nelder-Mead')
                M, k = res.x
                results[f'fixed_g{gamma_init}'] = (abs(M), abs(k), gamma_init, res.fun)
        except Exception as e:
            print(f"  Failed gamma_init={gamma_init}: {e}")

    return {
        'n': n, 'unique_w': unique_w,
        'weights': weights, 'r_fail': r_fail, 'fw': fw,
        'M_prior': M_prior, 'fits': results,
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    problem_exercises = [
        (11, 'Leg Press'),
        (81, 'Neutral Grip Lat Pulldown'),
        (41, 'Seated Cable Row V-grip'),
        (69, 'Preacher Curl'),
        (7, 'Barbell Curl'),
        (80, 'Straight-Bar Cable Curl'),
    ]

    fig, axes = plt.subplots(3, 2, figsize=(18, 20))
    axes = axes.flatten()

    for idx, (eid, ename) in enumerate(problem_exercises):
        ax = axes[idx]
        print(f"\n{'='*60}")
        print(f"{ename} (id={eid})")
        print('='*60)

        prior = get_prior_data(db, eid, AS_OF)
        prior_tuples = [(r[0], r[1], r[2], r[3]) for r in prior]
        today = TODAY_SETS.get(eid, [])

        print(f"  Prior sets: {len(prior_tuples)}")
        print(f"  Today sets: {len(today)}")

        # 1) Fit on prior only (what production would do for set 1)
        result_prior = fit_and_inspect(db, eid, ename, prior_tuples)

        # 2) Fit on prior + sets 1,2 (what production should do for set 3)
        if len(today) >= 2:
            extra = [(today[0][0], today[0][1], today[0][2], AS_OF.isoformat()),
                     (today[1][0], today[1][1], today[1][2], AS_OF.isoformat())]
            result_refit = fit_and_inspect(db, eid, ename, prior_tuples, extra)
        else:
            result_refit = None

        if result_prior:
            print(f"\n  Prior-only fits (M_prior={result_prior['M_prior']:.1f}):")
            for name, (M, k, g, loss) in sorted(result_prior['fits'].items()):
                print(f"    {name:20s}: M={M:.1f}, k={k:.1f}, γ={g:.3f}, loss={loss:.1f}")

                # Predict today's sets
                preds = []
                for w, reps, rpe in today:
                    rf_actual = reps + (10 - rpe)
                    rf_pred = curve_pred(w, M, k, g)
                    preds.append((w, rf_actual, rf_pred))
                pred_str = ", ".join([f"{w}lb: actual={a:.0f} pred={p:.1f}" for w, a, p in preds])
                print(f"      Today: {pred_str}")

        if result_refit:
            print(f"\n  Refit (prior + sets 1,2) fits:")
            for name, (M, k, g, loss) in sorted(result_refit['fits'].items()):
                print(f"    {name:20s}: M={M:.1f}, k={k:.1f}, γ={g:.3f}, loss={loss:.1f}")

                # Predict set 3
                if len(today) >= 3:
                    w3, reps3, rpe3 = today[2]
                    rf3_actual = reps3 + (10 - rpe3)
                    rf3_pred = curve_pred(w3, M, k, g)
                    print(f"      Set 3 pred: {w3}lb actual={rf3_actual:.0f} pred={rf3_pred:.1f} err={rf3_pred-rf3_actual:+.1f}")

        # Plot
        best_prior = None
        best_refit = None
        if result_prior:
            # Pick lowest-loss fit
            best_name = min(result_prior['fits'], key=lambda x: result_prior['fits'][x][3])
            best_prior = result_prior['fits'][best_name]
        if result_refit:
            best_name = min(result_refit['fits'], key=lambda x: result_refit['fits'][x][3])
            best_refit = result_refit['fits'][best_name]

        # Plot prior data
        if prior_tuples:
            pw = [r[0] for r in prior_tuples]
            pr = [r[1] + (10 - r[2]) for r in prior_tuples]
            ax.scatter(pw, pr, c='gray', alpha=0.4, s=30, label='Prior sets')

        # Plot today's actual
        if today:
            tw = [t[0] for t in today]
            tr = [t[1] + (10 - t[2]) for t in today]
            ax.scatter(tw, tr, c='red', s=100, zorder=5, marker='*', label="Today's actual")
            for i, (w, rf) in enumerate(zip(tw, tr)):
                ax.annotate(f'Set {i+1}\n{rf:.0f}r', (w, rf), fontsize=8,
                           textcoords='offset points', xytext=(8, 5))

        # Plot curves
        if best_prior:
            M, k, g, _ = best_prior
            w_range = np.linspace(min(tw + pw) * 0.8 if (tw and pw) else 10,
                                  M * 0.98, 200)
            r_curve = [curve_pred(w, M, k, g) for w in w_range]
            ax.plot(w_range, r_curve, 'b-', linewidth=2,
                   label=f'Prior fit (γ={g:.2f}, M={M:.0f})')

        if best_refit:
            M, k, g, _ = best_refit
            w_range = np.linspace(min(tw + pw) * 0.8 if (tw and pw) else 10,
                                  M * 0.98, 200)
            r_curve = [curve_pred(w, M, k, g) for w in w_range]
            ax.plot(w_range, r_curve, 'r--', linewidth=2,
                   label=f'Refit (γ={g:.2f}, M={M:.0f})')

        ax.set_title(f'{ename}', fontsize=12, fontweight='bold')
        ax.set_xlabel('Weight (lb)')
        ax.set_ylabel('Reps to failure')
        ax.legend(fontsize=8)
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('plots/curve_shape_investigation.png', dpi=150)
    print("\nSaved: plots/curve_shape_investigation.png")

    db.close()


if __name__ == '__main__':
    main()
