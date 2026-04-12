"""
Sequential refit simulation — properly evaluates how the planner would actually work.

For each exercise and each set, we predict r_fail using ONLY information available
at that point (prior data + any completed sets from today's session).

Then we evaluate how accurate each prediction was.

Key improvements to test over production baseline:
1. tier2 default gamma = 0.50 instead of 0.20
2. Proper sequential refit (adds real data points between sets)
3. Conservative cold-start defaults when no prior data exists
"""
import sqlite3
import sys
import os
import math
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
import numpy as np
from scipy.optimize import minimize
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

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
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def rpe_confidence(rpe, floor=0.2):
    return max(floor, math.exp(-0.25 * (10 - rpe)))


def recency_weight(age_days, half_life=30.0):
    return math.exp(-math.log(2) * age_days / half_life)


def curve_pred(W, M, k, gamma):
    if W >= M:
        return 0.0
    return k * (M / W - 1) ** gamma


def brzycki_1rm(w, r_fail):
    if r_fail >= 37:
        return w * 3.0
    return w * 36 / (37 - r_fail)


def fit_model(data_points, M_prior, lambda_M, fix_gamma=None, gamma_lower=0.05):
    """Fit the strength curve from a list of (weight, r_fail, fit_weight) tuples."""
    if not data_points:
        return None

    W = np.array([d[0] for d in data_points])
    R = np.array([d[1] for d in data_points])
    fw = np.array([d[2] for d in data_points])

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
                bounds = [(M_lower, M_upper), (0.5, 200.0), (gamma_lower, 3.0)]

            def loss(params, _fix_gamma=fix_gamma):
                if _fix_gamma is not None:
                    pM, pk = params
                    pg = _fix_gamma
                else:
                    pM, pk, pg = params
                ratio = np.clip(pM / W, 1.001, None)
                pred = pk * (ratio - 1) ** pg
                resid = fw * (pred - R) ** 2
                reg = lambda_M * math.log(pM / M_prior) ** 2
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


def prepare_data(observations, as_of, half_life=30.0, rpe_floor=0.2):
    """Convert observations to (weight, r_fail, fit_weight) tuples + compute M_prior."""
    data_points = []
    brzycki_list = []

    for w, reps, rpe, ts in observations:
        r_fail = reps + (10 - rpe)
        if isinstance(ts, str):
            d = datetime.date.fromisoformat(ts[:10])
        else:
            d = ts
        age = max(0, (as_of - d).days)

        conf = rpe_confidence(rpe, floor=rpe_floor)
        rec = recency_weight(age, half_life)
        fw = conf * rec

        data_points.append((w, r_fail, fw))

        if 0 < r_fail < 37:
            brzycki_list.append(brzycki_1rm(w, r_fail))

    M_prior = float(np.median(brzycki_list)) if brzycki_list else max(d[0] for d in data_points) * 1.3
    return data_points, M_prior


def compute_identifiability(data_points):
    """Compute identifiability score."""
    weights = np.array([d[0] for d in data_points])
    r_fail = np.array([d[1] for d in data_points])

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

    return id_range ** 0.4 * id_variety ** 0.3 * max(corr, 0.01) ** 0.3


def fit_with_config(observations, as_of, config):
    """Fit curve using a specific configuration."""
    if not observations:
        return None

    half_life = config.get('half_life', 30.0)
    rpe_floor = config.get('rpe_floor', 0.2)
    default_gamma = config.get('default_gamma', 0.20)
    tier1_min = config.get('tier1_min', 5)
    tier2_min = config.get('tier2_min', 3)

    data_points, M_prior = prepare_data(observations, as_of, half_life, rpe_floor)
    ident = compute_identifiability(data_points)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)

    n_real = len(data_points)
    n_distinct = len(set(d[0] for d in data_points))

    tier = 1 if n_real >= tier1_min and n_distinct >= 2 else (2 if n_real >= tier2_min else 0)
    if tier == 0 and n_real >= 1:
        tier = 2  # Force tier2 with even 1 data point for cold-start

    fix_gamma = default_gamma if tier == 2 else None

    result = fit_model(data_points, M_prior, lambda_M, fix_gamma)
    if result is None:
        return None

    M, k, gamma, loss = result
    return {
        'M': M, 'k': k, 'gamma': gamma, 'loss': loss,
        'M_prior': M_prior, 'ident': ident, 'lambda_M': lambda_M,
        'tier': tier, 'n_obs': n_real, 'n_distinct': n_distinct,
        'data_points': data_points,
    }


def sequential_refit_simulation(db, eid, ename, as_of, config):
    """
    Simulate the actual planner experience:
    - Predict set 1 from prior data only
    - After set 1 actual is known, refit and predict set 2
    - After set 2 actual is known, refit and predict set 3
    - Compare each prediction to actual
    """
    prior = get_prior_data(db, eid, as_of, config.get('days', 30))
    today = TODAYS_SETS.get(eid, [])
    if not today:
        return None

    results = []
    session_obs = []  # Accumulates today's completed sets

    for set_idx, (weight, actual_reps, actual_rpe) in enumerate(today):
        # Build observation pool: prior + already-completed session sets
        all_obs = list(prior) + session_obs

        actual_rfail = actual_reps + (10 - actual_rpe)

        if not all_obs:
            # Cold start — use Brzycki defaults
            # We need at least some basis for prediction
            # Use "no prior data" → can't predict set 1
            results.append({
                'set_num': set_idx + 1,
                'weight': weight,
                'actual_reps': actual_reps,
                'actual_rpe': actual_rpe,
                'actual_rfail': actual_rfail,
                'predicted_rfail': None,
                'error': None,
                'n_obs': 0,
                'tier': 0,
            })
        else:
            fit = fit_with_config(all_obs, as_of, config)
            if fit is None:
                results.append({
                    'set_num': set_idx + 1,
                    'weight': weight,
                    'actual_reps': actual_reps,
                    'actual_rpe': actual_rpe,
                    'actual_rfail': actual_rfail,
                    'predicted_rfail': None,
                    'error': None,
                    'n_obs': len(all_obs),
                    'tier': 0,
                })
            else:
                pred_rfail = curve_pred(weight, fit['M'], fit['k'], fit['gamma'])
                error = pred_rfail - actual_rfail
                results.append({
                    'set_num': set_idx + 1,
                    'weight': weight,
                    'actual_reps': actual_reps,
                    'actual_rpe': actual_rpe,
                    'actual_rfail': actual_rfail,
                    'predicted_rfail': pred_rfail,
                    'error': error,
                    'n_obs': fit['n_obs'],
                    'tier': fit['tier'],
                    'M': fit['M'],
                    'k': fit['k'],
                    'gamma': fit['gamma'],
                    'data_points': fit['data_points'],
                })

        # Add this set to session observations for the next refit
        session_obs.append((weight, actual_reps, actual_rpe,
                           as_of.isoformat() + f'T12:{set_idx:02d}:00'))

    return results


def rmse_for_seq(results):
    """Compute RMSE from sequential results, only for sets that have predictions."""
    errors = [r['error'] for r in results if r['error'] is not None]
    if not errors:
        return None
    return math.sqrt(sum(e**2 for e in errors) / len(errors))


def plot_sequential_comparison(all_results, config_labels, suffix=""):
    """Plot per-exercise sequential refit comparison."""
    # Group by exercise
    by_exercise = {}
    for config_label, results_dict in zip(config_labels, all_results):
        for eid, ename, results in results_dict:
            by_exercise.setdefault((eid, ename), []).append((config_label, results))

    for (eid, ename), configs in by_exercise.items():
        fig, axes = plt.subplots(1, 3, figsize=(20, 6))
        colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728', '#9467bd']

        today = TODAYS_SETS.get(eid, [])
        set_labels = [f"Set {i+1}\n{s[0]:.0f}lb" for i, s in enumerate(today)]

        # Left: Strength curves at each refit stage for best config
        ax = axes[0]
        best_config = min(configs, key=lambda c: rmse_for_seq(c[1]) or 999)
        best_label, best_results = best_config

        for i, r in enumerate(best_results):
            if r.get('data_points') and r.get('M'):
                M, k, gamma = r['M'], r['k'], r['gamma']
                dp = r['data_points']
                w_min = min(d[0] for d in dp) * 0.7
                w_max = M * 0.98
                w_range = np.linspace(w_min, w_max, 200)
                pred = k * (M / w_range - 1) ** gamma
                alpha_val = 0.3 + 0.35 * i
                ax.plot(w_range, pred, linewidth=2, alpha=alpha_val,
                        label=f"After set {i}" if i > 0 else "Pre-session",
                        color=colors[i])

        # Plot prior data points
        db_temp = sqlite3.connect(DB_PATH)
        prior = get_prior_data(db_temp, eid, AS_OF)
        db_temp.close()
        if prior:
            pw = [p[0] for p in prior]
            pr = [p[1] + (10 - p[2]) for p in prior]
            ax.scatter(pw, pr, s=50, c='gray', alpha=0.5, edgecolors='gray', label='Prior data')

        # Plot today's actual
        tw = [s[0] for s in today]
        trf = [s[1] + (10 - s[2]) for s in today]
        ax.scatter(tw, trf, s=140, c='red', marker='*', zorder=5, label="Today's actual")

        ax.set_xlabel('Weight (lb)')
        ax.set_ylabel('r_fail')
        ax.set_title(f'{ename}\nCurve evolution ({best_label})')
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

        # Middle: Per-set prediction error across configs
        ax2 = axes[1]
        n_configs = len(configs)
        x = np.arange(len(today))
        width = 0.8 / max(n_configs, 1)

        for ci, (clabel, results) in enumerate(configs):
            errors = []
            for r in results:
                errors.append(r['error'] if r['error'] is not None else 0)
            offset = (ci - n_configs / 2 + 0.5) * width
            bars = ax2.bar(x + offset, errors, width, label=clabel,
                          color=colors[ci % len(colors)], alpha=0.7)
            for bar, err in zip(bars, errors):
                if err != 0:
                    ax2.text(bar.get_x() + bar.get_width() / 2,
                            bar.get_height() + (0.2 if err >= 0 else -0.5),
                            f'{err:+.1f}', ha='center', fontsize=7)

        ax2.set_xticks(x)
        ax2.set_xticklabels(set_labels)
        ax2.set_ylabel('Prediction error (r_fail)')
        ax2.set_title(f'{ename}\nPer-set error')
        ax2.axhline(y=0, c='black', linewidth=0.5)
        ax2.legend(fontsize=7)
        ax2.grid(True, alpha=0.3, axis='y')

        # Right: RMSE summary
        ax3 = axes[2]
        rmse_vals = []
        for clabel, results in configs:
            rmse = rmse_for_seq(results)
            rmse_vals.append((clabel, rmse if rmse else 0))

        labels_r = [r[0] for r in rmse_vals]
        vals = [r[1] for r in rmse_vals]
        bar_colors = colors[:len(vals)]
        bars = ax3.barh(labels_r, vals, color=bar_colors, alpha=0.7)
        for bar, v in zip(bars, vals):
            ax3.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2,
                    f'{v:.2f}', va='center', fontsize=9)
        ax3.set_xlabel('RMSE (r_fail)')
        ax3.set_title(f'{ename}\nOverall RMSE')
        ax3.set_xlim(0, max(vals) * 1.3 + 1)
        ax3.grid(True, alpha=0.3, axis='x')

        fname = f"sequential_{ename.replace(' ', '_')}{suffix}.png"
        fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Saved {fname}")


def plot_overall_summary(all_results, config_labels, suffix=""):
    """Plot overall summary heatmap + bar chart."""
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))

    # Left: RMSE heatmap
    ax = axes[0]
    exercise_names = [ename for _, ename in EXERCISES]
    data = np.full((len(exercise_names), len(config_labels)), float('nan'))

    for ci, (clabel, results_list) in enumerate(zip(config_labels, all_results)):
        for eid, ename, results in results_list:
            ei = next(i for i, (e, _) in enumerate(EXERCISES) if e == eid)
            rmse = rmse_for_seq(results)
            if rmse is not None:
                data[ei, ci] = rmse

    im = ax.imshow(data, cmap='RdYlGn_r', aspect='auto', vmin=0, vmax=10)
    ax.set_xticks(range(len(config_labels)))
    ax.set_xticklabels(config_labels, rotation=45, ha='right', fontsize=8)
    ax.set_yticks(range(len(exercise_names)))
    ax.set_yticklabels(exercise_names, fontsize=9)

    for i in range(len(exercise_names)):
        for j in range(len(config_labels)):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=8,
                        color='white' if v > 6 else 'black')

    ax.set_title('Per-Exercise RMSE — Lower is Better')
    plt.colorbar(im, ax=ax, label='RMSE (r_fail)')

    # Right: Mean RMSE bar chart
    ax2 = axes[1]
    colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728', '#9467bd']
    means = []
    for ci, (clabel, results_list) in enumerate(zip(config_labels, all_results)):
        rmses = [rmse_for_seq(r) for _, _, r in results_list if rmse_for_seq(r) is not None]
        means.append(np.mean(rmses) if rmses else 0)

    bars = ax2.bar(config_labels, means, color=colors[:len(means)], alpha=0.7)
    for bar, m in zip(bars, means):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{m:.2f}', ha='center', fontsize=9)

    ax2.set_ylabel('Mean RMSE (r_fail)')
    ax2.set_title('Overall Model Comparison')
    ax2.tick_params(axis='x', rotation=45)
    ax2.grid(True, alpha=0.3, axis='y')

    fname = f"overall_summary{suffix}.png"
    fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Saved {fname}")


# ── Configs to test ──

CONFIGS = {
    'A: production\n(γ=0.20, no refit)': {
        'default_gamma': 0.20,
        'rpe_floor': 0.2,
        'half_life': 30.0,
        'days': 30,
    },
    'B: γ=0.50\n(no refit)': {
        'default_gamma': 0.50,
        'rpe_floor': 0.2,
        'half_life': 30.0,
        'days': 30,
    },
    'C: γ=0.50\nfloor=0.05': {
        'default_gamma': 0.50,
        'rpe_floor': 0.05,
        'half_life': 30.0,
        'days': 30,
    },
    'D: γ=0.50\nhl=14d': {
        'default_gamma': 0.50,
        'rpe_floor': 0.05,
        'half_life': 14.0,
        'days': 30,
    },
    'E: γ=0.50\n60d window': {
        'default_gamma': 0.50,
        'rpe_floor': 0.05,
        'half_life': 14.0,
        'days': 60,
    },
}


if __name__ == '__main__':
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    all_results = []
    config_labels = []

    for config_name, config in CONFIGS.items():
        print(f"\n{'='*70}")
        print(f"Config: {config_name.replace(chr(10), ' ')}")
        print(f"{'='*70}")

        results_list = []
        for eid, ename in EXERCISES:
            results = sequential_refit_simulation(db, eid, ename, AS_OF, config)
            if results:
                results_list.append((eid, ename, results))
                rmse = rmse_for_seq(results)
                rmse_str = f"RMSE={rmse:.2f}" if rmse else "no predictions"
                print(f"  {ename:30s} {rmse_str}")
                for r in results:
                    if r['predicted_rfail'] is not None:
                        print(f"    Set {r['set_num']}: {r['weight']:>7.1f}lb "
                              f"actual rf={r['actual_rfail']:.0f} "
                              f"pred rf={r['predicted_rfail']:.1f} "
                              f"err={r['error']:+.1f} "
                              f"(n={r['n_obs']}, tier={r['tier']})")
                    else:
                        print(f"    Set {r['set_num']}: {r['weight']:>7.1f}lb "
                              f"actual rf={r['actual_rfail']:.0f} "
                              f"NO PREDICTION (cold start)")

        all_results.append(results_list)
        config_labels.append(config_name)

    # Summary
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"\n{'Exercise':30s}", end='')
    for cl in config_labels:
        print(f"  {cl.replace(chr(10), ' '):>14s}", end='')
    print()
    print("-" * (30 + 16 * len(config_labels)))

    for eid, ename in EXERCISES:
        print(f"{ename:30s}", end='')
        for results_list in all_results:
            match = [r for e, n, r in results_list if e == eid]
            if match:
                rmse = rmse_for_seq(match[0])
                if rmse is not None:
                    print(f"  {rmse:>14.2f}", end='')
                else:
                    print(f"  {'cold':>14s}", end='')
            else:
                print(f"  {'—':>14s}", end='')
        print()

    print("-" * (30 + 16 * len(config_labels)))
    print(f"{'MEAN (all)':30s}", end='')
    for results_list in all_results:
        rmses = [rmse_for_seq(r) for _, _, r in results_list if rmse_for_seq(r) is not None]
        if rmses:
            print(f"  {np.mean(rmses):>14.2f}", end='')
        else:
            print(f"  {'—':>14s}", end='')
    print()

    print(f"{'MEAN (has prior)':30s}", end='')
    prior_eids = {25, 11, 81, 69, 80}  # Exercises with prior data
    for results_list in all_results:
        rmses = [rmse_for_seq(r) for e, _, r in results_list if e in prior_eids and rmse_for_seq(r) is not None]
        if rmses:
            print(f"  {np.mean(rmses):>14.2f}", end='')
        else:
            print(f"  {'—':>14s}", end='')
    print()

    # Plots
    print("\n\nGenerating plots...")
    plot_sequential_comparison(all_results, config_labels)
    plot_overall_summary(all_results, config_labels)

    db.close()
    print("\nDone! Plots saved to:", PLOT_DIR)
