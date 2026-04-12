"""
Targeted improvements — the most promising ideas from analysis:

1. Session fatigue discount: subtract ~1 r_fail per prior set in session
2. Boosted in-session refit weight (3x multiplier for today's data)
3. Smarter tier2 gamma: use Brzycki-implied gamma instead of fixed default
4. Combined best settings
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


def fit_model(W, R, fw, M_prior, lambda_M, fix_gamma=None, gamma_lower=0.05):
    """Fit the strength curve."""
    W = np.asarray(W, dtype=float)
    R = np.asarray(R, dtype=float)
    fw = np.asarray(fw, dtype=float)

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

            def loss(params, _fg=fix_gamma):
                if _fg is not None:
                    pM, pk = params
                    pg = _fg
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


def sequential_refit(db, eid, ename, as_of, config):
    """Simulate sequential refit with advanced features."""
    prior = get_prior_data(db, eid, as_of, config.get('days', 30))
    today = TODAYS_SETS.get(eid, [])
    if not today:
        return None

    half_life = config.get('half_life', 30.0)
    rpe_floor = config.get('rpe_floor', 0.2)
    default_gamma = config.get('default_gamma', 0.20)
    tier1_min = config.get('tier1_min', 5)
    session_boost = config.get('session_boost', 1.0)  # multiplier for in-session data
    fatigue_per_set = config.get('fatigue_per_set', 0.0)  # r_fail reduction per prior set
    use_brzycki_gamma = config.get('use_brzycki_gamma', False)

    results = []
    session_obs = []

    for set_idx, (weight, actual_reps, actual_rpe) in enumerate(today):
        actual_rfail = actual_reps + (10 - actual_rpe)

        # Build weighted observations
        all_obs = list(prior) + session_obs
        if not all_obs:
            results.append({
                'set_num': set_idx + 1, 'weight': weight,
                'actual_reps': actual_reps, 'actual_rpe': actual_rpe,
                'actual_rfail': actual_rfail, 'predicted_rfail': None,
                'error': None, 'n_obs': 0, 'tier': 0,
            })
            session_obs.append((weight, actual_reps, actual_rpe,
                               as_of.isoformat() + f'T12:{set_idx:02d}:00'))
            continue

        # Prepare data with weights
        weights_arr = []
        rfail_arr = []
        fw_arr = []
        brzycki_list = []

        for w, reps, rpe, ts in all_obs:
            r_fail = reps + (10 - rpe)
            if isinstance(ts, str):
                d = datetime.date.fromisoformat(ts[:10])
            else:
                d = ts
            age = max(0, (as_of - d).days)

            conf = rpe_confidence(rpe, floor=rpe_floor)
            rec = recency_weight(age, half_life)
            fw = conf * rec

            # Boost in-session data
            if age == 0 and session_boost > 1.0:
                fw *= session_boost

            weights_arr.append(w)
            rfail_arr.append(r_fail)
            fw_arr.append(fw)

            if 0 < r_fail < 37:
                brzycki_list.append(brzycki_1rm(w, r_fail))

        W = np.array(weights_arr)
        R = np.array(rfail_arr)
        fw = np.array(fw_arr)

        M_prior = float(np.median(brzycki_list)) if brzycki_list else float(W.max()) * 1.3

        # Identifiability
        n_distinct = len(set(W.tolist()))
        wr = W.max() / W.min() - 1 if W.min() > 0 else 0
        id_range = min(1.0, wr / 1.0)
        id_variety = min(1.0, (n_distinct - 1) / 4.0)
        if len(W) > 1 and np.std(W) > 0 and np.std(R) > 0:
            corr = abs(np.corrcoef(W, R)[0, 1])
            if np.isnan(corr):
                corr = 0.0
        else:
            corr = 0.0
        ident = id_range ** 0.4 * id_variety ** 0.3 * max(corr, 0.01) ** 0.3
        lambda_M = 10.0 + 20.0 * (1.0 - ident)

        n_real = len(all_obs)
        tier = 1 if n_real >= tier1_min and n_distinct >= 2 else 2

        # Determine gamma for tier2
        if tier == 2:
            if use_brzycki_gamma:
                # Use Brzycki-implied gamma: Brzycki is gamma=1, k=36
                # Our curve uses k*(M/W-1)^gamma
                # With varied data, we can infer gamma
                # With limited data, Brzycki shape (gamma~1.0) is our best prior
                # But that tends to be too steep for isolation exercises
                # Compromise: use 0.63 (geometric mean of Brzycki 1.0 and our 0.40)
                fix_gamma = 0.63
            else:
                fix_gamma = default_gamma
        else:
            fix_gamma = None

        result = fit_model(W, R, fw, M_prior, lambda_M, fix_gamma)
        if result is None:
            results.append({
                'set_num': set_idx + 1, 'weight': weight,
                'actual_reps': actual_reps, 'actual_rpe': actual_rpe,
                'actual_rfail': actual_rfail, 'predicted_rfail': None,
                'error': None, 'n_obs': n_real, 'tier': tier,
            })
        else:
            M, k, gamma, loss = result
            pred_rfail = curve_pred(weight, M, k, gamma)

            # Apply fatigue discount for later sets in session
            fatigue = fatigue_per_set * set_idx
            adj_pred = max(1.0, pred_rfail - fatigue)

            error = adj_pred - actual_rfail
            results.append({
                'set_num': set_idx + 1, 'weight': weight,
                'actual_reps': actual_reps, 'actual_rpe': actual_rpe,
                'actual_rfail': actual_rfail,
                'predicted_rfail': adj_pred,
                'raw_predicted': pred_rfail,
                'fatigue_adj': fatigue,
                'error': error, 'n_obs': n_real, 'tier': tier,
                'M': M, 'k': k, 'gamma': gamma,
            })

        session_obs.append((weight, actual_reps, actual_rpe,
                           as_of.isoformat() + f'T12:{set_idx:02d}:00'))

    return results


def rmse_for(results):
    errors = [r['error'] for r in results if r['error'] is not None]
    if not errors:
        return None
    return math.sqrt(sum(e**2 for e in errors) / len(errors))


def mean_signed_error(results, set_num=None):
    """Mean signed error, optionally filtered by set number."""
    errors = []
    for r in results:
        if r['error'] is not None:
            if set_num is None or r['set_num'] == set_num:
                errors.append(r['error'])
    return sum(errors) / len(errors) if errors else None


def run_all(db, config, label):
    print(f"\n{'='*70}")
    print(f"Config: {label}")
    for k, v in config.items():
        print(f"  {k}: {v}")
    print(f"{'='*70}")

    results_dict = {}
    for eid, ename in EXERCISES:
        results = sequential_refit(db, eid, ename, AS_OF, config)
        if results:
            results_dict[(eid, ename)] = results
            rmse = rmse_for(results)
            print(f"\n  {ename:30s} RMSE={rmse:.2f}" if rmse else f"\n  {ename:30s} no predictions")
            for r in results:
                if r['predicted_rfail'] is not None:
                    fat = f" (fatigue:{r.get('fatigue_adj',0):+.1f})" if r.get('fatigue_adj', 0) else ""
                    print(f"    Set {r['set_num']}: {r['weight']:>7.1f}lb "
                          f"actual rf={r['actual_rfail']:.0f} "
                          f"pred rf={r['predicted_rfail']:.1f}{fat} "
                          f"err={r['error']:+.1f} "
                          f"(n={r['n_obs']}, T{r['tier']})")
                else:
                    print(f"    Set {r['set_num']}: {r['weight']:>7.1f}lb "
                          f"actual rf={r['actual_rfail']:.0f} "
                          f"NO PREDICTION (cold start)")
    return results_dict


def plot_comprehensive(all_configs, suffix=""):
    """Generate one comprehensive plot per exercise."""
    labels = list(all_configs.keys())
    colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728', '#9467bd']

    for eid, ename in EXERCISES:
        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        today = TODAYS_SETS.get(eid, [])
        set_labels = [f"Set {i+1}\n{s[0]:.0f}lb" for i, s in enumerate(today)]

        # Left: Error by set
        ax = axes[0]
        n_cfg = len(labels)
        x = np.arange(len(today))
        width = 0.8 / max(n_cfg, 1)

        for ci, label in enumerate(labels):
            results = all_configs[label].get((eid, ename), [])
            if not results:
                continue
            errors = [r['error'] if r['error'] is not None else 0 for r in results]
            offset = (ci - n_cfg / 2 + 0.5) * width
            bars = ax.bar(x + offset, errors, width, label=label.split('\n')[0],
                         color=colors[ci % len(colors)], alpha=0.7)
            for bar, err in zip(bars, errors):
                if err != 0:
                    va = 'bottom' if err >= 0 else 'top'
                    nudge = 0.2 if err >= 0 else -0.4
                    ax.text(bar.get_x() + bar.get_width() / 2,
                           bar.get_height() + nudge,
                           f'{err:+.1f}', ha='center', va=va, fontsize=7)

        ax.set_xticks(x)
        ax.set_xticklabels(set_labels)
        ax.set_ylabel('Prediction error (r_fail)')
        ax.set_title(f'{ename} — Per-set error')
        ax.axhline(y=0, c='black', linewidth=0.5)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3, axis='y')

        # Right: RMSE summary
        ax2 = axes[1]
        rmse_data = []
        for ci, label in enumerate(labels):
            results = all_configs[label].get((eid, ename), [])
            if results:
                rmse = rmse_for(results)
                rmse_data.append((label.split('\n')[0], rmse or 0, colors[ci % len(colors)]))

        if rmse_data:
            lbs = [r[0] for r in rmse_data]
            vals = [r[1] for r in rmse_data]
            cols = [r[2] for r in rmse_data]
            bars = ax2.barh(lbs, vals, color=cols, alpha=0.7)
            for bar, v in zip(bars, vals):
                ax2.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2,
                        f'{v:.2f}', va='center', fontsize=9)
            ax2.set_xlabel('RMSE (r_fail)')
            ax2.set_title(f'{ename} — RMSE comparison')
            ax2.set_xlim(0, max(vals) * 1.3 + 1)
            ax2.grid(True, alpha=0.3, axis='x')

        fname = f"targeted_{ename.replace(' ', '_')}{suffix}.png"
        fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Saved {fname}")


def plot_final_summary(all_configs, suffix=""):
    """Final summary plot."""
    labels = list(all_configs.keys())
    short_labels = [l.split('\n')[0] for l in labels]
    colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728', '#9467bd']

    fig, axes = plt.subplots(1, 2, figsize=(18, 8))

    # Left: Heatmap
    ax = axes[0]
    exercise_names = [ename for _, ename in EXERCISES]
    data = np.full((len(exercise_names), len(labels)), float('nan'))

    for ci, label in enumerate(labels):
        for ei, (eid, ename) in enumerate(EXERCISES):
            results = all_configs[label].get((eid, ename), [])
            if results:
                rmse = rmse_for(results)
                if rmse is not None:
                    data[ei, ci] = rmse

    im = ax.imshow(data, cmap='RdYlGn_r', aspect='auto', vmin=0, vmax=8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(short_labels, rotation=45, ha='right', fontsize=8)
    ax.set_yticks(range(len(exercise_names)))
    ax.set_yticklabels(exercise_names, fontsize=9)

    for i in range(len(exercise_names)):
        for j in range(len(labels)):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=8,
                        color='white' if v > 5 else 'black')

    ax.set_title('RMSE by Exercise and Config')
    plt.colorbar(im, ax=ax, label='RMSE (r_fail)')

    # Right: Overall mean RMSE
    ax2 = axes[1]
    means = []
    prior_means = []
    prior_eids = {25, 11, 81, 69, 80}

    for label in labels:
        all_rmse = []
        prior_rmse = []
        for eid, ename in EXERCISES:
            results = all_configs[label].get((eid, ename), [])
            if results:
                rmse = rmse_for(results)
                if rmse is not None:
                    all_rmse.append(rmse)
                    if eid in prior_eids:
                        prior_rmse.append(rmse)
        means.append(np.mean(all_rmse) if all_rmse else 0)
        prior_means.append(np.mean(prior_rmse) if prior_rmse else 0)

    x = np.arange(len(labels))
    w = 0.35
    bars1 = ax2.bar(x - w/2, means, w, label='All exercises', color='#1f77b4', alpha=0.7)
    bars2 = ax2.bar(x + w/2, prior_means, w, label='Has prior data', color='#ff7f0e', alpha=0.7)

    for bar, v in zip(bars1, means):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f'{v:.2f}', ha='center', fontsize=8)
    for bar, v in zip(bars2, prior_means):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f'{v:.2f}', ha='center', fontsize=8)

    ax2.set_xticks(x)
    ax2.set_xticklabels(short_labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('Mean RMSE')
    ax2.set_title('Overall Model Comparison')
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')

    fname = f"final_summary{suffix}.png"
    fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Saved {fname}")


if __name__ == '__main__':
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    all_configs = {}

    # ── A: Production baseline ──
    cfg_a = run_all(db, {
        'default_gamma': 0.20, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
    }, "A: Production\n(γ=0.20)")
    all_configs["A: Production\n(γ=0.20)"] = cfg_a

    # ── B: Best from round 1 (γ=0.50) ──
    cfg_b = run_all(db, {
        'default_gamma': 0.50, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
    }, "B: γ=0.50\nbaseline")
    all_configs["B: γ=0.50\nbaseline"] = cfg_b

    # ── C: γ=0.50 + session fatigue discount ──
    cfg_c = run_all(db, {
        'default_gamma': 0.50, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
        'fatigue_per_set': 1.0,
    }, "C: +fatigue\n(-1/set)")
    all_configs["C: +fatigue\n(-1/set)"] = cfg_c

    # ── D: γ=0.50 + fatigue + boosted refit ──
    cfg_d = run_all(db, {
        'default_gamma': 0.50, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
        'fatigue_per_set': 1.0, 'session_boost': 3.0,
    }, "D: +boost 3x\n+fatigue")
    all_configs["D: +boost 3x\n+fatigue"] = cfg_d

    # ── E: Brzycki gamma (0.63) + fatigue + boost ──
    cfg_e = run_all(db, {
        'default_gamma': 0.63, 'rpe_floor': 0.05, 'half_life': 14.0, 'days': 30,
        'fatigue_per_set': 1.0, 'session_boost': 3.0,
        'use_brzycki_gamma': True,
    }, "E: γ=0.63\n+all tweaks")
    all_configs["E: γ=0.63\n+all tweaks"] = cfg_e

    # ── Summary ──
    print(f"\n\n{'='*70}")
    print("COMPREHENSIVE SUMMARY")
    print(f"{'='*70}")

    labels = list(all_configs.keys())
    short_labels = [l.replace('\n', ' ') for l in labels]

    # Table header
    print(f"\n{'Exercise':30s}", end='')
    for sl in short_labels:
        print(f"  {sl:>14s}", end='')
    print()
    print("-" * (30 + 16 * len(labels)))

    for eid, ename in EXERCISES:
        print(f"{ename:30s}", end='')
        for label in labels:
            results = all_configs[label].get((eid, ename), [])
            if results:
                rmse = rmse_for(results)
                if rmse is not None:
                    print(f"  {rmse:>14.2f}", end='')
                else:
                    print(f"  {'cold':>14s}", end='')
            else:
                print(f"  {'—':>14s}", end='')
        print()

    print("-" * (30 + 16 * len(labels)))
    print(f"{'MEAN (all)':30s}", end='')
    for label in labels:
        rmses = [rmse_for(r) for r in all_configs[label].values() if rmse_for(r) is not None]
        print(f"  {np.mean(rmses):>14.2f}" if rmses else f"  {'—':>14s}", end='')
    print()

    prior_eids = {25, 11, 81, 69, 80}
    print(f"{'MEAN (has prior)':30s}", end='')
    for label in labels:
        rmses = [rmse_for(r) for (e, n), r in all_configs[label].items() if e in prior_eids and rmse_for(r) is not None]
        print(f"  {np.mean(rmses):>14.2f}" if rmses else f"  {'—':>14s}", end='')
    print()

    # Per-set error analysis
    print(f"\n\nPer-set mean signed error:")
    print(f"{'Config':30s}  {'Set 1':>10s}  {'Set 2':>10s}  {'Set 3':>10s}")
    print("-" * 64)
    for label in labels:
        errs = {1: [], 2: [], 3: []}
        for results in all_configs[label].values():
            for r in results:
                if r['error'] is not None:
                    errs[r['set_num']].append(r['error'])

        sl = label.replace('\n', ' ')
        s1 = f"{np.mean(errs[1]):+.2f}" if errs[1] else "—"
        s2 = f"{np.mean(errs[2]):+.2f}" if errs[2] else "—"
        s3 = f"{np.mean(errs[3]):+.2f}" if errs[3] else "—"
        print(f"{sl:30s}  {s1:>10s}  {s2:>10s}  {s3:>10s}")

    # Plots
    print("\n\nGenerating plots...")
    plot_comprehensive(all_configs)
    plot_final_summary(all_configs)

    db.close()
    print("\nDone! Plots saved to:", PLOT_DIR)
