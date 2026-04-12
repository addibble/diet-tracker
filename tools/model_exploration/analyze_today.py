"""Analyze today's workout vs model predictions and test improvements."""
import sqlite3
import sys
import math
import datetime
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import numpy as np
from scipy.optimize import minimize

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'production_backup_2026-04-11_103611.db')
PLOT_DIR = os.path.join(os.path.dirname(__file__), 'plots')
os.makedirs(PLOT_DIR, exist_ok=True)

AS_OF = datetime.date(2026, 4, 11)

EXERCISES = [
    (7, 'Barbell Curl'),
    (25, 'Face Pulls'),
    (67, 'Incline Hammer Curl'),
    (11, 'Leg Press'),
    (81, 'Neutral Grip Lat Pulldown'),
    (69, 'Preacher Curl'),
    (41, 'Seated Cable Row V-grip'),
    (80, 'Straight-Bar Cable Curl'),
]

TODAYS_SETS = {
    7: [(60, 15, 8), (70, 10, 9), (80, 6, 10)],
    25: [(22.5, 20, 5), (27.5, 20, 7), (37.5, 20, 8)],
    67: [(10, 18, 5), (15, 18, 6), (25, 12, 9)],
    11: [(430, 15, 7), (470, 11, 8), (520, 9, 10)],
    81: [(140, 16, 7), (165, 10, 2), (195, 7, 10)],
    69: [(45, 16, 7), (65, 8, 9), (75, 4, 10)],
    41: [(110, 16, 7), (120, 11, 8), (140, 8, 9)],
    80: [(42.5, 15, 8), (47.5, 10, 9), (52.5, 7, 10)],
}


def get_prior_data(db, exercise_id, as_of, days=30):
    """Get RPE sets from the last N days before as_of."""
    rows = db.execute(
        """SELECT ws.weight, ws.reps, ws.rpe, s.date
           FROM workout_sets ws
           JOIN workout_sessions s ON ws.session_id = s.id
           WHERE ws.exercise_id = ?
             AND ws.rpe IS NOT NULL AND ws.rpe >= 5.0
             AND s.date >= date(?, '-' || ? || ' days')
             AND s.date < date(?)
           ORDER BY s.date""",
        (exercise_id, as_of.isoformat(), days, as_of.isoformat()),
    ).fetchall()
    return rows


def rpe_confidence(rpe):
    return max(0.2, math.exp(-0.25 * (10 - rpe)))


def recency_weight(age_days, half_life=30.0):
    return math.exp(-math.log(2) * age_days / half_life)


def curve_pred(W, M, k, gamma):
    """Predict r_fail for weight W."""
    if W >= M:
        return 0.0
    return k * (M / W - 1) ** gamma


def fit_model(weights, r_fail, fit_w, M_prior, lambda_M, fix_gamma=None):
    """Fit the strength curve model. Returns (M, k, gamma, loss)."""
    W = np.array(weights)
    R = np.array(r_fail)
    fw = np.array(fit_w)

    M_lower = max(W.max() * 1.01, M_prior * 0.8)
    M_upper = max(M_prior * 1.5, W.max() * 2.0)

    best = None
    M_factors = [1.1, 1.3, 1.5, 2.0]
    gamma_inits = [0.15, 0.5, 1.0] if fix_gamma is None else [fix_gamma]

    for mf in M_factors:
        for gi in gamma_inits:
            M0 = min(max(W.max() * mf, M_lower), M_upper)
            if fix_gamma is not None:
                x0 = [M0, 10.0]
                bounds = [(M_lower, M_upper), (0.5, 200.0)]
            else:
                x0 = [M0, 10.0, gi]
                bounds = [(M_lower, M_upper), (0.5, 200.0), (0.05, 3.0)]

            def loss(params):
                if fix_gamma is not None:
                    M, k = params
                    g = fix_gamma
                else:
                    M, k, g = params
                pred = k * (M / W - 1) ** g
                resid = fw * (pred - R) ** 2
                reg = lambda_M * math.log(M / M_prior) ** 2
                return float(resid.sum() + reg)

            try:
                res = minimize(loss, x0, method='L-BFGS-B', bounds=bounds)
                if best is None or res.fun < best[3]:
                    if fix_gamma is not None:
                        best = (res.x[0], res.x[1], fix_gamma, res.fun)
                    else:
                        best = (res.x[0], res.x[1], res.x[2], res.fun)
            except Exception:
                pass

    return best


def analyze_exercise(db, eid, ename, as_of, half_life=30.0, days=30, label="current"):
    """Fit curve and compare to today's actuals. Returns dict of results."""
    rows = get_prior_data(db, eid, as_of, days)
    if not rows:
        return None

    weights = np.array([r['weight'] for r in rows])
    reps_arr = np.array([r['reps'] for r in rows])
    rpes = np.array([r['rpe'] for r in rows])
    r_fail = reps_arr + (10 - rpes)

    ages = []
    for r in rows:
        d = datetime.date.fromisoformat(r['date'][:10]) if isinstance(r, dict) else datetime.date.fromisoformat(r[3][:10])
        ages.append((as_of - d).days)
    ages = np.array(ages, dtype=float)

    conf = np.array([rpe_confidence(rpe) for rpe in rpes])
    rec = np.array([recency_weight(a, half_life) for a in ages])
    fw = conf * rec

    # Brzycki prior
    brzycki = []
    for w, rf in zip(weights, r_fail):
        if rf < 37:
            brzycki.append(w * 36 / (37 - rf))
    M_prior = float(np.median(brzycki)) if brzycki else float(weights.max()) * 1.3

    # Identifiability
    n_distinct = len(set(weights.tolist()))
    wr = weights.max() / weights.min() - 1 if weights.min() > 0 else 0
    id_range = min(1.0, wr / 1.0)
    id_variety = min(1.0, (n_distinct - 1) / 4.0)
    if len(weights) > 1 and np.std(weights) > 0 and np.std(r_fail) > 0:
        corr = abs(np.corrcoef(weights, r_fail)[0, 1])
        if np.isnan(corr):
            corr = 0.0
    else:
        corr = 0.0
    ident = id_range ** 0.4 * id_variety ** 0.3 * max(corr, 0.01) ** 0.3
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    # Tier
    tier = 1 if len(rows) >= 5 and n_distinct >= 2 else 2
    fix_gamma = 0.20 if tier == 2 else None

    result = fit_model(weights, r_fail, fw, M_prior, lambda_M, fix_gamma)
    if result is None:
        return None

    M, k, gamma, loss = result

    # Compare with today's actuals
    comparisons = []
    for w, actual_reps, actual_rpe in TODAYS_SETS[eid]:
        actual_rfail = actual_reps + (10 - actual_rpe)
        predicted_rfail = curve_pred(w, M, k, gamma)
        predicted_reps = predicted_rfail - (10 - actual_rpe)  # at same RPE
        error = predicted_rfail - actual_rfail
        comparisons.append({
            'weight': w,
            'actual_reps': actual_reps,
            'actual_rpe': actual_rpe,
            'actual_rfail': actual_rfail,
            'predicted_rfail': predicted_rfail,
            'predicted_reps': predicted_reps,
            'error': error,
        })

    return {
        'name': ename,
        'eid': eid,
        'tier': tier,
        'M': M,
        'k': k,
        'gamma': gamma,
        'loss': loss,
        'M_prior': M_prior,
        'ident': ident,
        'lambda_M': lambda_M,
        'n_prior': len(rows),
        'n_distinct': n_distinct,
        'half_life': half_life,
        'label': label,
        'comparisons': comparisons,
        'prior_weights': weights,
        'prior_rfail': r_fail,
        'prior_fw': fw,
    }


def print_results(results):
    """Print comparison table."""
    for r in results:
        if r is None:
            continue
        print(f"\n{'='*60}")
        print(f"{r['name']} [{r['label']}]")
        print(f"  Tier {r['tier']}: M={r['M']:.1f}, k={r['k']:.2f}, gamma={r['gamma']:.3f}")
        print(f"  Prior: {r['n_prior']} sets, {r['n_distinct']} weights, half_life={r['half_life']}")
        print(f"  Brzycki prior: {r['M_prior']:.1f}, ident={r['ident']:.3f}, lambda={r['lambda_M']:.1f}")
        print(f"  {'Weight':>8} {'Actual':>8} {'Pred':>8} {'Error':>8} {'ActRF':>6} {'PredRF':>6}")
        total_err_sq = 0
        for c in r['comparisons']:
            print(f"  {c['weight']:>8.1f} {c['actual_reps']:>5}rep {c['predicted_reps']:>6.1f}rep {c['error']:>+6.1f}rf {c['actual_rfail']:>5.0f}rf {c['predicted_rfail']:>6.1f}rf")
            total_err_sq += c['error'] ** 2
        rmse = math.sqrt(total_err_sq / len(r['comparisons']))
        print(f"  RMSE(r_fail): {rmse:.2f}")


def plot_comparison(results_list, suffix=""):
    """Plot comparison of model variants for each exercise."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Group by exercise
    by_exercise = {}
    for r in results_list:
        if r is None:
            continue
        by_exercise.setdefault(r['eid'], []).append(r)

    for eid, variants in by_exercise.items():
        ename = variants[0]['name']
        fig, ax = plt.subplots(1, 1, figsize=(10, 6))

        # Plot prior data (from first variant)
        v0 = variants[0]
        sizes = v0['prior_fw'] * 60 + 10
        ax.scatter(v0['prior_weights'], v0['prior_rfail'], s=sizes,
                   alpha=0.5, c='gray', label='Prior data (sized by weight)', zorder=2)

        # Plot today's actual sets
        today = TODAYS_SETS[eid]
        tw = [s[0] for s in today]
        trf = [s[1] + (10 - s[2]) for s in today]
        ax.scatter(tw, trf, s=120, c='red', marker='*', zorder=5, label="Today's actual")

        # Plot curves
        colors = ['blue', 'green', 'orange', 'purple', 'brown']
        for i, v in enumerate(variants):
            M, k, gamma = v['M'], v['k'], v['gamma']
            w_range = np.linspace(v['prior_weights'].min() * 0.7, M * 0.99, 200)
            pred = k * (M / w_range - 1) ** gamma
            ax.plot(w_range, pred, c=colors[i % len(colors)], linewidth=2,
                    label=f"{v['label']} (M={M:.0f}, k={k:.1f}, γ={gamma:.2f})")
            # Mark predicted points for today's weights
            for c in v['comparisons']:
                ax.plot(c['weight'], c['predicted_rfail'], 'o',
                        c=colors[i % len(colors)], markersize=8, alpha=0.7)

        ax.set_xlabel('Weight (lb)')
        ax.set_ylabel('r_fail (reps to failure)')
        ax.set_title(f'{ename} — Model Comparison')
        ax.legend(fontsize=8, loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

        fname = f"comparison_{ename.replace(' ', '_')}{suffix}.png"
        fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Saved {fname}")


# ── Main Analysis ──

if __name__ == '__main__':
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    print("=" * 60)
    print("PHASE 1: Current model (30d half-life, production settings)")
    print("=" * 60)
    current_results = []
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, half_life=30.0, days=30, label="current (hl=30d)")
        current_results.append(r)
    print_results(current_results)

    print("\n\n" + "=" * 60)
    print("PHASE 2: Shorter half-life (14d) — weight recent data more")
    print("=" * 60)
    short_hl_results = []
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, half_life=14.0, days=30, label="hl=14d")
        short_hl_results.append(r)
    print_results(short_hl_results)

    print("\n\n" + "=" * 60)
    print("PHASE 3: Very short half-life (7d)")
    print("=" * 60)
    vshort_results = []
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, half_life=7.0, days=30, label="hl=7d")
        vshort_results.append(r)
    print_results(vshort_results)

    print("\n\n" + "=" * 60)
    print("PHASE 4: Shorter window (14d only)")
    print("=" * 60)
    short_window_results = []
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, half_life=14.0, days=14, label="14d window")
        short_window_results.append(r)
    print_results(short_window_results)

    # Compute summary RMSE per variant
    print("\n\n" + "=" * 60)
    print("SUMMARY: Average RMSE(r_fail) across exercises")
    print("=" * 60)
    for label, results in [
        ("current (hl=30d)", current_results),
        ("hl=14d", short_hl_results),
        ("hl=7d", vshort_results),
        ("14d window", short_window_results),
    ]:
        rmses = []
        for r in results:
            if r is None:
                continue
            errs = [c['error'] ** 2 for c in r['comparisons']]
            rmses.append(math.sqrt(sum(errs) / len(errs)))
        if rmses:
            print(f"  {label:20s}: mean RMSE={np.mean(rmses):.2f}, median={np.median(rmses):.2f}, max={max(rmses):.2f}")

    # Generate plots
    print("\n\nGenerating comparison plots...")
    all_results = current_results + short_hl_results + vshort_results + short_window_results
    plot_comparison(all_results)

    db.close()
    print("\nDone!")
