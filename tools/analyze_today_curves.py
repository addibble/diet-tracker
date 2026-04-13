"""Analyze today's (Apr 12) sets with the strength curve model.
For each weighted exercise, show: historical curve fit, per-set refit, 
Brzycki 1RM estimates, inflection detection."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import sqlite3
from collections import defaultdict

DB_PATH = 'production_db_backup_2026-04-12-164418.db'
TARGET_DATE = '2026-04-12'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get today's weighted sets grouped by exercise
rows = conn.execute("""
    SELECT e.name, e.id as exercise_id, ws.weight, ws.reps, ws.rpe, ws.set_order,
           e.load_input_mode, e.allow_heavy_loading
    FROM workout_sets ws
    JOIN exercises e ON e.id = ws.exercise_id
    JOIN workout_sessions sess ON sess.id = ws.session_id
    WHERE sess.date = ?
      AND ws.weight > 0
    ORDER BY e.name, ws.set_order
""", (TARGET_DATE,)).fetchall()

exercises = defaultdict(list)
exercise_meta = {}
for r in rows:
    exercises[r['name']].append(dict(r))
    exercise_meta[r['name']] = {
        'exercise_id': r['exercise_id'],
        'load_input_mode': r['load_input_mode'],
        'allow_heavy_loading': r['allow_heavy_loading'],
    }

print(f"Today's weighted exercises: {len(exercises)}")
print("=" * 80)

for ex_name in sorted(exercises.keys()):
    sets = exercises[ex_name]
    meta = exercise_meta[ex_name]
    eid = meta['exercise_id']
    heavy = meta['allow_heavy_loading']
    
    print(f"\n{'─' * 80}")
    print(f"  {ex_name}  (id={eid}, heavy={'Y' if heavy else 'N'})")
    print(f"{'─' * 80}")
    
    # Show today's sets
    brzycki_1rms = []
    for s in sets:
        w = s['weight']
        r = s['reps']
        rpe = s['rpe']
        rir = 10.0 - rpe
        r_fail = r + rir
        brz_1rm = w * 36.0 / (37.0 - min(r_fail, 36)) if r_fail < 37 else w * 2.5
        brzycki_1rms.append(brz_1rm)
        print(f"    Set {s['set_order']:2d}: {w:6.1f} lb × {r:2d} reps @ RPE {rpe:.0f}"
              f"  (r_fail={r_fail:.0f}, Brzycki 1RM={brz_1rm:.1f})")
    
    # Inflection check
    if len(brzycki_1rms) >= 3:
        trend = []
        for i in range(1, len(brzycki_1rms)):
            delta = brzycki_1rms[i] - brzycki_1rms[i-1]
            trend.append(f"{'↑' if delta > 0 else '↓'}{abs(delta):.1f}")
        
        inflecting = brzycki_1rms[-1] < brzycki_1rms[-2]
        avg_1rm = sum(brzycki_1rms) / len(brzycki_1rms)
        print(f"    ──")
        print(f"    Brzycki 1RM trend: {' → '.join(f'{x:.0f}' for x in brzycki_1rms)}"
              f"  ({' '.join(trend)})")
        print(f"    Inflecting: {'YES ✓' if inflecting else 'NO'}")
        print(f"    Avg estimated 1RM: {avg_1rm:.1f} lb")
    
    # Historical context: how many qualifying sets in 30-day window?
    hist = conn.execute("""
        SELECT ws.weight, ws.reps, ws.rpe, sess.date
        FROM workout_sets ws
        JOIN workout_sessions sess ON sess.id = ws.session_id
        WHERE ws.exercise_id = ?
          AND ws.rpe >= 7
          AND sess.date >= date(?, '-30 days')
          AND sess.date < ?
          AND ws.weight > 0
        ORDER BY sess.date
    """, (eid, TARGET_DATE, TARGET_DATE)).fetchall()
    
    distinct_weights = len(set(round(h['weight'], 1) for h in hist))
    print(f"    Historical (30d): {len(hist)} qualifying sets, {distinct_weights} distinct weights")
    
    if len(hist) >= 3:
        hist_1rms = []
        for h in hist:
            rf = h['reps'] + (10 - h['rpe'])
            brz = h['weight'] * 36.0 / (37.0 - min(rf, 36))
            hist_1rms.append(brz)
        import numpy as np
        print(f"    Historical 1RM: median={np.median(hist_1rms):.1f}, "
              f"range=[{min(hist_1rms):.1f}, {max(hist_1rms):.1f}]")

conn.close()
print(f"\n{'=' * 80}")
print("Done.")
