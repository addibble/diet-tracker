"""Test t-test session filtering + tier2 demotion across all exercises."""
import sqlite3
import numpy as np
from scipy import stats
from datetime import date, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings("ignore")

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from app.strength_model import (
    _fit_params, _estimate_M_bounds, _identifiability_score,
    CurveFit, solve_weight, _recency_weights, _rpe_confidence,
)

DB = os.path.join(os.path.dirname(__file__), "..", "..", "production_backup_2026-04-11_103611.db")
conn = sqlite3.connect(DB)
today = date(2026, 4, 11)
cutoff = (today - timedelta(days=30)).isoformat()

rows = conn.execute("""
    SELECT ws.exercise_id, e.name, ws.weight, ws.reps, ws.rpe,
           s.date, e.allow_heavy_loading
    FROM workout_sets ws
    JOIN workout_sessions s ON ws.session_id = s.id
    JOIN exercises e ON ws.exercise_id = e.id
    WHERE ws.rpe IS NOT NULL AND ws.rpe >= 7.0
      AND ws.weight IS NOT NULL AND ws.weight > 0
      AND s.date >= ?
      AND e.load_input_mode = 'external_weight'
    ORDER BY ws.exercise_id, s.date DESC
""", (cutoff,)).fetchall()

by_ex = defaultdict(list)
for eid, name, w, reps, rpe, d, heavy in rows:
    by_ex[eid].append((name, w, reps, rpe, d, heavy))

print(f"Exercises with RPE>=7 data in last 30d: {len(by_ex)}\n")

all_pass = True
for eid, sets in sorted(by_ex.items()):
    name = sets[0][0]
    heavy = sets[0][5]
    if len(sets) < 3:
        continue

    # Group by session date, compute per-set 1RM
    by_date = defaultdict(list)
    for _, w, reps, rpe, d, _ in sets:
        age = (today - date.fromisoformat(d)).days
        rf = reps + (10 - rpe)
        denom = max(1 - rf / 37.0, 0.05)
        by_date[age].append((w, rf, rpe, w / denom))

    sorted_ages = sorted(by_date.keys())
    anchor_age = sorted_ages[0]
    anchor_1rms = [d[3] for d in by_date[anchor_age]]

    # T-test filter: drop sessions significantly different from most recent
    kept_data = list(by_date[anchor_age])
    kept_ages = [anchor_age] * len(kept_data)
    dropped = []
    for age in sorted_ages[1:]:
        s1rms = [d[3] for d in by_date[age]]
        if len(s1rms) >= 2 and len(anchor_1rms) >= 2:
            _, p = stats.ttest_ind(anchor_1rms, s1rms, equal_var=False)
            if p < 0.05:
                dropped.append(age)
                continue
        kept_data.extend(by_date[age])
        kept_ages.extend([age] * len(by_date[age]))

    W = np.array([d[0] for d in kept_data], dtype=float)
    r = np.array([d[1] for d in kept_data], dtype=float)
    conf = np.array([_rpe_confidence(d[2]) for d in kept_data])
    rec = np.array(_recency_weights(kept_ages))
    fw = conf * rec
    if len(W) < 3:
        continue

    M_lo, M_hi, Mp = _estimate_M_bounds(W.tolist(), r.tolist())
    ident = _identifiability_score(W.tolist(), r.tolist())
    lam = 10.0 + 20.0 * (1.0 - ident)

    # Free gamma (tier 1)
    M1, k1, g1, _ = _fit_params(W, r, fw, M_lo, M_hi, Mp, lam, fixed_gamma=None)
    fit1 = CurveFit(M=M1, k=k1, gamma=g1, n_obs=len(W), rmse=0,
                     max_observed_weight=float(np.max(W)), fit_tier="t1")
    s1f = solve_weight(18 if heavy else 23, fit1)
    s2f = solve_weight(12 if heavy else 20, fit1)

    # Fixed gamma (tier 2)
    M2, k2, g2, _ = _fit_params(W, r, fw, M_lo, M_hi, Mp, lam, fixed_gamma=1.0)
    fit2 = CurveFit(M=M2, k=k2, gamma=1.0, n_obs=len(W), rmse=0,
                     max_observed_weight=float(np.max(W)), fit_tier="t2")
    s1t = solve_weight(18 if heavy else 23, fit2)
    s2t = solve_weight(12 if heavy else 20, fit2)

    rf = s2f / s1f if s1f > 0 else 99
    rt = s2t / s1t if s1t > 0 else 99

    # Auto-demote if gamma hits bounds
    use_t2 = g1 <= 0.16 or g1 >= 2.49
    best_r = rt if use_t2 else rf

    flag = " *** FAIL" if best_r > 1.5 else ""
    if flag:
        all_pass = False
    demote = " [demote->t2]" if use_t2 else ""
    drop_s = f" drop={dropped}" if dropped else ""
    print(f"{name:32s} n={len(W):2d} g={g1:.2f} free={rf:.2f}x t2={rt:.2f}x{demote}{drop_s}{flag}")

conn.close()
print(f"\n{'ALL PASS' if all_pass else 'SOME FAILURES'}")
