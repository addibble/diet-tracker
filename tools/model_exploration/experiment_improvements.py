"""
Experimental model improvements for strength curve fitting.

Key problems identified from today's (2026-04-11) workout:
1. Flat curves: gamma=0.20 (tier2 default) is too low - curves don't bend enough
2. Single-weight prior data: can't shape the curve at all
3. Brzycki prior under-leveraged: should contribute synthetic data points
4. RPE=2 outlier: low-RPE data gets too much credit (confidence floor too high)
5. High-rep outliers (27.5x30@RPE10) distort fits

Improvements to test:
A. Higher default gamma (0.50 instead of 0.20) for tier2
B. Brzycki synthetic points: add virtual observations from the Brzycki formula
C. Lower RPE confidence floor (0.05 instead of 0.2) to down-weight bad RPE data
D. Combined A+B+C
E. Include today's sets progressively (simulate in-session refit)
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
    return rows


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
        return w * 3.0  # cap
    return w * 36 / (37 - r_fail)


def fit_model(weights, r_fail, fit_w, M_prior, lambda_M, fix_gamma=None,
              synth_weights=None, synth_rfail=None, synth_w=None):
    """Fit the strength curve, optionally with synthetic Brzycki points."""
    W = np.array(weights)
    R = np.array(r_fail)
    fw = np.array(fit_w)

    # Add synthetic points if provided
    if synth_weights is not None:
        W = np.concatenate([W, np.array(synth_weights)])
        R = np.concatenate([R, np.array(synth_rfail)])
        fw = np.concatenate([fw, np.array(synth_w)])

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
                    pM, pk = params
                    pg = fix_gamma
                else:
                    pM, pk, pg = params
                ratio = pM / W
                ratio = np.clip(ratio, 1.001, None)
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


def make_brzycki_synthetic(weights, r_fail, M_prior, n_points=5):
    """Generate synthetic observations from the Brzycki formula to regularize the curve shape."""
    # Create virtual data points along the Brzycki curve
    # Brzycki: r_fail = 37 * (1 - W/M) = 37 - 37*W/M
    # Or equivalently: r_fail(W) = 36 * (M/W - 1) at gamma=1, k=36

    W_min = min(weights) * 0.8
    W_max = M_prior * 0.95
    synth_W = np.linspace(W_min, W_max, n_points)
    synth_R = np.array([max(1.0, 36 * (M_prior / w - 1)) for w in synth_W])

    # Weight synthetic points less than real data
    synth_fw = np.full(n_points, 0.15)  # low but nonzero weight

    return synth_W.tolist(), synth_R.tolist(), synth_fw.tolist()


def analyze_exercise(db, eid, ename, as_of, *,
                     half_life=30.0, days=30, label="current",
                     default_gamma=0.20, rpe_floor=0.2,
                     use_synthetic=False, include_today_sets=0,
                     tier1_min=5, tier2_min=3):
    """Fit curve and compare to today's actuals."""
    rows = get_prior_data(db, eid, as_of, days)

    # Optionally include some of today's sets (for refit simulation)
    today_additions = []
    if include_today_sets > 0 and eid in TODAYS_SETS:
        for w, r, rpe in TODAYS_SETS[eid][:include_today_sets]:
            today_additions.append((w, r, rpe, as_of.isoformat() + 'T12:00:00'))

    all_data = list(rows) + today_additions

    if not all_data:
        return None

    weights = np.array([r[0] if isinstance(r, tuple) else r['weight'] for r in all_data])
    reps_arr = np.array([r[1] if isinstance(r, tuple) else r['reps'] for r in all_data])
    rpes = np.array([r[2] if isinstance(r, tuple) else r['rpe'] for r in all_data])
    r_fail = reps_arr + (10 - rpes)

    ages = []
    for r in all_data:
        ts = r[3] if isinstance(r, tuple) else r['date']
        d = datetime.date.fromisoformat(ts[:10]) if isinstance(ts, str) else ts
        ages.append(max(0, (as_of - d).days))
    ages = np.array(ages, dtype=float)

    conf = np.array([rpe_confidence(rpe, floor=rpe_floor) for rpe in rpes])
    rec = np.array([recency_weight(a, half_life) for a in ages])
    fw = conf * rec

    # Brzycki prior
    brzycki = []
    for w, rf in zip(weights, r_fail):
        if rf < 37 and rf > 0:
            brzycki.append(brzycki_1rm(w, rf))
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
    n_real = len(all_data)
    tier = 1 if n_real >= tier1_min and n_distinct >= 2 else 2
    fix_gamma = default_gamma if tier == 2 else None

    # Synthetic Brzycki points
    synth_args = {}
    if use_synthetic:
        sw, sr, sfw = make_brzycki_synthetic(weights, r_fail, M_prior)
        synth_args = {'synth_weights': sw, 'synth_rfail': sr, 'synth_w': sfw}

    result = fit_model(weights, r_fail, fw, M_prior, lambda_M, fix_gamma, **synth_args)
    if result is None:
        return None

    M, k, gamma, loss = result

    # Compare with today's actuals (all 3 sets)
    comparisons = []
    for w, actual_reps, actual_rpe in TODAYS_SETS.get(eid, []):
        actual_rfail = actual_reps + (10 - actual_rpe)
        predicted_rfail = curve_pred(w, M, k, gamma)
        predicted_reps = predicted_rfail - (10 - actual_rpe)
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
        'name': ename, 'eid': eid, 'tier': tier,
        'M': M, 'k': k, 'gamma': gamma, 'loss': loss,
        'M_prior': M_prior, 'ident': ident, 'lambda_M': lambda_M,
        'n_prior': n_real, 'n_distinct': n_distinct,
        'half_life': half_life, 'label': label,
        'comparisons': comparisons,
        'prior_weights': weights, 'prior_rfail': r_fail, 'prior_fw': fw,
    }


def rmse_for(result):
    if result is None or not result['comparisons']:
        return None
    errs = [c['error'] ** 2 for c in result['comparisons']]
    return math.sqrt(sum(errs) / len(errs))


def print_results(results):
    for r in results:
        if r is None:
            continue
        rmse = rmse_for(r)
        print(f"\n  {r['name']:30s} [{r['label']}]")
        print(f"    Tier {r['tier']}: M={r['M']:.1f}, k={r['k']:.2f}, γ={r['gamma']:.3f} | RMSE={rmse:.2f}")
        print(f"    Prior: {r['n_prior']} sets, {r['n_distinct']} weights | Brzycki M_prior={r['M_prior']:.1f}")
        for c in r['comparisons']:
            sign = '+' if c['error'] >= 0 else ''
            print(f"    {c['weight']:>7.1f} lb: actual {c['actual_reps']:>2} reps @RPE{c['actual_rpe']:.0f} (rf={c['actual_rfail']:.0f})"
                  f"  pred rf={c['predicted_rfail']:.1f}  err={sign}{c['error']:.1f}")


def summary_table(variants):
    """Print a summary RMSE table across all variants and exercises."""
    print(f"\n{'Exercise':30s}", end='')
    labels = list(dict.fromkeys(r['label'] for r in variants if r is not None))
    for l in labels:
        print(f"  {l:>12s}", end='')
    print()
    print("-" * (30 + 14 * len(labels)))

    by_exercise = {}
    for r in variants:
        if r is None:
            continue
        by_exercise.setdefault(r['name'], {})[r['label']] = rmse_for(r)

    for ename, label_rmses in sorted(by_exercise.items()):
        print(f"{ename:30s}", end='')
        for l in labels:
            v = label_rmses.get(l)
            if v is not None:
                print(f"  {v:>12.2f}", end='')
            else:
                print(f"  {'—':>12s}", end='')
        print()

    # Averages
    print("-" * (30 + 14 * len(labels)))
    print(f"{'MEAN':30s}", end='')
    for l in labels:
        vals = [label_rmses.get(l) for label_rmses in by_exercise.values() if label_rmses.get(l) is not None]
        if vals:
            print(f"  {np.mean(vals):>12.2f}", end='')
        else:
            print(f"  {'—':>12s}", end='')
    print()


def plot_all_exercises(variants, suffix=""):
    """Plot per-exercise comparison of model variants."""
    by_exercise = {}
    for r in variants:
        if r is None:
            continue
        by_exercise.setdefault(r['eid'], []).append(r)

    for eid, vlist in by_exercise.items():
        ename = vlist[0]['name']
        n_variants = len(vlist)
        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        # Left: curves
        ax = axes[0]
        colors = ['#1f77b4', '#2ca02c', '#ff7f0e', '#d62728', '#9467bd', '#8c564b', '#e377c2']

        # Prior data (from first variant that has it)
        v0 = vlist[0]
        sizes = v0['prior_fw'] / v0['prior_fw'].max() * 80 + 10
        ax.scatter(v0['prior_weights'], v0['prior_rfail'], s=sizes,
                   alpha=0.4, c='gray', edgecolors='gray', label='Prior data', zorder=2)

        # Today's actual
        today = TODAYS_SETS.get(eid, [])
        tw = [s[0] for s in today]
        trf = [s[1] + (10 - s[2]) for s in today]
        ax.scatter(tw, trf, s=140, c='red', marker='*', zorder=5, label="Today's actual")

        for i, v in enumerate(vlist):
            M, k, gamma = v['M'], v['k'], v['gamma']
            w_min = min(v['prior_weights'].min(), min(tw) if tw else 999) * 0.7
            w_max = M * 0.98
            w_range = np.linspace(w_min, w_max, 300)
            pred = k * (M / w_range - 1) ** gamma
            rmse = rmse_for(v)
            ax.plot(w_range, pred, c=colors[i % len(colors)], linewidth=2,
                    label=f"{v['label']} (M={M:.0f}, γ={gamma:.2f}, RMSE={rmse:.1f})")

        ax.set_xlabel('Weight (lb)')
        ax.set_ylabel('r_fail')
        ax.set_title(f'{ename} — Strength Curves')
        ax.legend(fontsize=7, loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

        # Right: error bar chart
        ax2 = axes[1]
        set_labels = [f"{s[0]:.0f}lb" for s in today] if today else []
        x = np.arange(len(set_labels))
        width = 0.8 / max(n_variants, 1)

        for i, v in enumerate(vlist):
            errors = [c['error'] for c in v['comparisons']]
            offset = (i - n_variants / 2 + 0.5) * width
            bars = ax2.bar(x + offset, errors, width, label=v['label'],
                           color=colors[i % len(colors)], alpha=0.7)
            for bar, err in zip(bars, errors):
                ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                         f'{err:+.1f}', ha='center', va='bottom' if err >= 0 else 'top',
                         fontsize=7)

        ax2.set_xticks(x)
        ax2.set_xticklabels(set_labels)
        ax2.set_ylabel('Prediction error (r_fail)')
        ax2.set_title(f'{ename} — Prediction Error by Set')
        ax2.axhline(y=0, c='black', linewidth=0.5)
        ax2.legend(fontsize=7)
        ax2.grid(True, alpha=0.3, axis='y')

        fname = f"improvement_{ename.replace(' ', '_')}{suffix}.png"
        fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  Saved {fname}")


def plot_summary_heatmap(variants, suffix=""):
    """Plot RMSE heatmap across exercises and variants."""
    by_exercise = {}
    for r in variants:
        if r is None:
            continue
        by_exercise.setdefault(r['name'], {})[r['label']] = rmse_for(r)

    labels = list(dict.fromkeys(r['label'] for r in variants if r is not None))
    exercise_names = sorted(by_exercise.keys())

    data = []
    for ename in exercise_names:
        row = [by_exercise[ename].get(l, float('nan')) for l in labels]
        data.append(row)
    data = np.array(data)

    fig, ax = plt.subplots(figsize=(12, max(6, len(exercise_names) * 0.6)))
    im = ax.imshow(data, cmap='RdYlGn_r', aspect='auto', vmin=0, vmax=8)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=9)
    ax.set_yticks(range(len(exercise_names)))
    ax.set_yticklabels(exercise_names, fontsize=9)

    for i in range(len(exercise_names)):
        for j in range(len(labels)):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=8,
                        color='white' if v > 5 else 'black')

    ax.set_title('RMSE (r_fail) — Lower is Better', fontsize=12)
    plt.colorbar(im, ax=ax, label='RMSE')

    fname = f"summary_heatmap{suffix}.png"
    fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  Saved {fname}")


# ── Main ──

if __name__ == '__main__':
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    all_variants = []

    # ── Variant A: Current production model ──
    print("=" * 70)
    print("Variant A: Current production model (γ=0.20 tier2, floor=0.2)")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="A: current",
                             default_gamma=0.20, rpe_floor=0.2)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'A: current'])

    # ── Variant B: Higher default gamma ──
    print("\n" + "=" * 70)
    print("Variant B: Higher tier2 gamma (γ=0.50)")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="B: γ=0.50",
                             default_gamma=0.50, rpe_floor=0.2)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'B: γ=0.50'])

    # ── Variant C: Brzycki synthetic points ──
    print("\n" + "=" * 70)
    print("Variant C: Brzycki synthetic points + γ=0.50")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="C: synth+γ=0.50",
                             default_gamma=0.50, rpe_floor=0.2, use_synthetic=True)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'C: synth+γ=0.50'])

    # ── Variant D: Lower RPE floor (0.05) ──
    print("\n" + "=" * 70)
    print("Variant D: Lower RPE floor (0.05) + γ=0.50 + synthetic")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="D: floor=.05",
                             default_gamma=0.50, rpe_floor=0.05, use_synthetic=True)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'D: floor=.05'])

    # ── Variant E: Gamma=0.80 (more aggressive curvature) ──
    print("\n" + "=" * 70)
    print("Variant E: Higher gamma (γ=0.80) + synthetic + floor=0.05")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="E: γ=0.80",
                             default_gamma=0.80, rpe_floor=0.05, use_synthetic=True)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'E: γ=0.80'])

    # ── Variant F: Simulate in-session refit (include set 1, predict 2&3) ──
    print("\n" + "=" * 70)
    print("Variant F: Refit after set 1 (best settings from above)")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="F: refit+1set",
                             default_gamma=0.50, rpe_floor=0.05,
                             use_synthetic=True, include_today_sets=1)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'F: refit+1set'])

    # ── Variant G: Refit after sets 1+2 ──
    print("\n" + "=" * 70)
    print("Variant G: Refit after sets 1+2")
    print("=" * 70)
    for eid, ename in EXERCISES:
        r = analyze_exercise(db, eid, ename, AS_OF, label="G: refit+2sets",
                             default_gamma=0.50, rpe_floor=0.05,
                             use_synthetic=True, include_today_sets=2)
        all_variants.append(r)
    print_results([v for v in all_variants if v and v['label'] == 'G: refit+2sets'])

    # ── Summary ──
    print("\n\n" + "=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    summary_table(all_variants)

    # ── Plots ──
    print("\n\nGenerating plots...")
    plot_all_exercises(all_variants)
    plot_summary_heatmap(all_variants)

    db.close()
    print("\nAll done! Plots saved to:", PLOT_DIR)
