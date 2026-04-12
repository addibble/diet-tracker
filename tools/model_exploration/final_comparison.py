"""
Final recommended model comparison — production vs proposed improvements.

Recommended changes:
1. tier2 default gamma: 0.20 → 0.50
2. Session fatigue discount: 0.3 r_fail per prior set in session
3. Session refit boost: 5x weight for in-session data
"""
import sqlite3
import sys
import os
import math
import datetime

sys.path.insert(0, '.')
from targeted_improvements import *

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

CONFIGS_FINAL = {
    'Production\n(current)': {
        'default_gamma': 0.20, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
        'fatigue_per_set': 0.0, 'session_boost': 1.0,
    },
    'Proposed\n(recommended)': {
        'default_gamma': 0.50, 'rpe_floor': 0.2, 'half_life': 30.0, 'days': 30,
        'fatigue_per_set': 0.3, 'session_boost': 5.0,
    },
}


def plot_final_report():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    configs = {}
    for name, cfg in CONFIGS_FINAL.items():
        results = {}
        for eid, ename in EXERCISES:
            r = sequential_refit(db, eid, ename, AS_OF, cfg)
            if r:
                results[(eid, ename)] = r
        configs[name] = results

    # ── Grand comparison figure ──
    n_ex = len(EXERCISES)
    fig = plt.figure(figsize=(24, n_ex * 3.5 + 3))
    gs = gridspec.GridSpec(n_ex + 1, 4, figure=fig, hspace=0.4, wspace=0.3,
                           height_ratios=[1]*n_ex + [0.5])

    prod_color = '#d62728'  # red
    prop_color = '#2ca02c'  # green

    for ei, (eid, ename) in enumerate(EXERCISES):
        today = TODAYS_SETS.get(eid, [])

        # ── Column 1: Curve comparison ──
        ax1 = fig.add_subplot(gs[ei, 0:2])

        # Prior data
        prior = get_prior_data(db, eid, AS_OF)
        if prior:
            pw = [p[0] for p in prior]
            pr = [p[1] + (10 - p[2]) for p in prior]
            sizes = [max(20, rpe_confidence(p[2]) * 80) for p in prior]
            ax1.scatter(pw, pr, s=sizes, c='#888888', alpha=0.5, edgecolors='gray',
                       label='Prior data', zorder=2)

        # Today's actual
        tw = [s[0] for s in today]
        trf = [s[1] + (10 - s[2]) for s in today]
        ax1.scatter(tw, trf, s=180, c='gold', marker='*', edgecolors='black',
                   zorder=5, label="Today's actual", linewidth=0.5)

        # Draw curves for last refit stage
        for ci, (cname, cresults) in enumerate(configs.items()):
            results = cresults.get((eid, ename), [])
            if not results:
                continue
            # Use last result that has fit parameters
            last_fit = None
            for r in reversed(results):
                if r.get('M'):
                    last_fit = r
                    break
            if last_fit:
                M, k, gamma = last_fit['M'], last_fit['k'], last_fit['gamma']
                w_min = min(tw + [p[0] for p in prior] if prior else tw) * 0.7
                w_max = M * 0.98
                w_range = np.linspace(w_min, w_max, 200)
                pred = k * (M / w_range - 1) ** gamma
                color = prod_color if ci == 0 else prop_color
                style = '--' if ci == 0 else '-'
                label_short = cname.split('\n')[0]
                ax1.plot(w_range, pred, c=color, linewidth=2.5 if ci == 1 else 1.5,
                        linestyle=style, alpha=0.8,
                        label=f'{label_short} (M={M:.0f}, γ={gamma:.2f})')

        ax1.set_xlabel('Weight (lb)', fontsize=9)
        ax1.set_ylabel('r_fail', fontsize=9)
        ax1.set_title(f'{ename}', fontsize=11, fontweight='bold')
        ax1.legend(fontsize=7, loc='upper right')
        ax1.grid(True, alpha=0.2)
        ax1.set_ylim(bottom=0)

        # ── Column 2: Per-set error bars ──
        ax2 = fig.add_subplot(gs[ei, 2])
        set_labels = [f"S{i+1}\n{s[0]:.0f}lb" for i, s in enumerate(today)]
        x = np.arange(len(today))
        w = 0.35

        for ci, (cname, cresults) in enumerate(configs.items()):
            results = cresults.get((eid, ename), [])
            if not results:
                continue
            errors = [r['error'] if r['error'] is not None else 0 for r in results]
            color = prod_color if ci == 0 else prop_color
            offset = -w/2 if ci == 0 else w/2
            bars = ax2.bar(x + offset, errors, w, color=color, alpha=0.7,
                          label=cname.split('\n')[0])
            for bar, err in zip(bars, errors):
                if err != 0:
                    va = 'bottom' if err >= 0 else 'top'
                    nudge = 0.3 if err >= 0 else -0.5
                    ax2.text(bar.get_x() + bar.get_width()/2,
                            bar.get_height() + nudge,
                            f'{err:+.1f}', ha='center', va=va, fontsize=8)

        ax2.set_xticks(x)
        ax2.set_xticklabels(set_labels, fontsize=8)
        ax2.set_ylabel('Error (r_fail)', fontsize=9)
        ax2.axhline(y=0, c='black', linewidth=0.5)
        if ei == 0:
            ax2.legend(fontsize=7)
        ax2.grid(True, alpha=0.2, axis='y')

        # ── Column 3: RMSE comparison ──
        ax3 = fig.add_subplot(gs[ei, 3])
        rmse_data = []
        for ci, (cname, cresults) in enumerate(configs.items()):
            results = cresults.get((eid, ename), [])
            if results:
                rmse = rmse_for(results)
                color = prod_color if ci == 0 else prop_color
                rmse_data.append((cname.split('\n')[0], rmse or 0, color))

        if rmse_data:
            lbs = [r[0] for r in rmse_data]
            vals = [r[1] for r in rmse_data]
            cols = [r[2] for r in rmse_data]
            bars = ax3.barh(lbs, vals, color=cols, alpha=0.7, height=0.5)
            for bar, v in zip(bars, vals):
                pct = ''
                if len(vals) == 2 and vals[0] > 0:
                    change = (vals[1] - vals[0]) / vals[0] * 100
                    pct = f' ({change:+.0f}%)'
                ax3.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height()/2,
                        f'{v:.2f}{pct if bar == bars[-1] else ""}',
                        va='center', fontsize=9)
            ax3.set_xlabel('RMSE', fontsize=9)
            ax3.set_xlim(0, max(vals) * 1.4 + 0.5)
            ax3.grid(True, alpha=0.2, axis='x')

    # ── Bottom summary row ──
    ax_sum = fig.add_subplot(gs[n_ex, :])
    ax_sum.axis('off')

    # Compute overall stats
    summary_lines = []
    for cname, cresults in configs.items():
        rmses = [rmse_for(r) for r in cresults.values() if rmse_for(r) is not None]
        set_errs = {1: [], 2: [], 3: []}
        for results in cresults.values():
            for r in results:
                if r['error'] is not None:
                    set_errs[r['set_num']].append(r['error'])

        m = np.mean(rmses) if rmses else 0
        s1 = np.mean(set_errs[1]) if set_errs[1] else 0
        s2 = np.mean(set_errs[2]) if set_errs[2] else 0
        s3 = np.mean(set_errs[3]) if set_errs[3] else 0
        short = cname.split('\n')[0]
        summary_lines.append(f'{short}: Mean RMSE = {m:.2f}  |  Set bias: S1={s1:+.1f}, S2={s2:+.1f}, S3={s3:+.1f}')

    change_pct = 0
    rmses_list = []
    for cresults in configs.values():
        rmses = [rmse_for(r) for r in cresults.values() if rmse_for(r) is not None]
        rmses_list.append(np.mean(rmses) if rmses else 0)
    if len(rmses_list) == 2 and rmses_list[0] > 0:
        change_pct = (rmses_list[1] - rmses_list[0]) / rmses_list[0] * 100

    text = '\n'.join(summary_lines) + f'\n\nOverall improvement: {change_pct:+.1f}% RMSE reduction'
    text += '\nChanges: γ tier2 0.20→0.50 | fatigue -0.3/set | session boost 5x'
    ax_sum.text(0.5, 0.5, text, ha='center', va='center', fontsize=11,
                family='monospace', transform=ax_sum.transAxes,
                bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    fig.suptitle('Strength Curve Model: Production vs Proposed Improvements\n'
                 'April 11, 2026 Workout — 8 Exercises, 24 Sets',
                 fontsize=14, fontweight='bold', y=0.995)

    fname = 'FINAL_model_comparison.png'
    fig.savefig(os.path.join(PLOT_DIR, fname), dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'Saved {fname}')

    db.close()


if __name__ == '__main__':
    plot_final_report()
