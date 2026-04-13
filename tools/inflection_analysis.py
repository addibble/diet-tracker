"""Analyze inflection point of fitted curve for seated leg curl."""
import sqlite3
import numpy as np
from datetime import date, timedelta
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.strength_model import (
    fresh_curve, _rpe_confidence,
    fit_from_data, refit_from_data, MIN_RPE_FOR_FIT,
)

DB = os.path.join(os.path.dirname(__file__), "..", "production_backup_2026-04-12_170723.db")
SESSION_ID = 389
SESSION_DATE = date(2026, 4, 12)
EXERCISE_NAME = "Seated Leg Curl"


def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    eid = c.execute("SELECT id FROM exercises WHERE name = ?", (EXERCISE_NAME,)).fetchone()[0]
    print(f"{EXERCISE_NAME} (id={eid})")

    sets = c.execute("""
        SELECT weight, reps, rpe FROM workout_sets
        WHERE session_id = ? AND exercise_id = ? AND weight > 0
        ORDER BY set_order
    """, (SESSION_ID, eid)).fetchall()
    print(f"Session sets: {[(w, r, rpe) for w, r, rpe in sets]}")

    cutoff = SESSION_DATE - timedelta(days=30)
    rows = c.execute("""
        SELECT wk.weight, wk.reps, wk.rpe, ws.date, ws.id
        FROM workout_sets wk JOIN workout_sessions ws ON wk.session_id = ws.id
        WHERE wk.exercise_id = ? AND ws.date >= ? AND ws.date <= ?
          AND wk.rpe IS NOT NULL AND wk.rpe >= ? AND wk.reps IS NOT NULL
        ORDER BY ws.date, wk.set_order
    """, (eid, cutoff.isoformat(), SESSION_DATE.isoformat(), MIN_RPE_FOR_FIT)).fetchall()

    hist_w, hist_r, hist_c, hist_a = [], [], [], []
    for w, r, rpe, d, sid in rows:
        if sid != SESSION_ID:
            age = (SESSION_DATE - date.fromisoformat(d)).days
            hist_w.append(float(w))
            hist_r.append(r + (10.0 - rpe))
            hist_c.append(_rpe_confidence(rpe))
            hist_a.append(float(age))

    print(f"\nHistorical: {len(hist_w)} sets")
    for w, r in zip(hist_w, hist_r):
        print(f"  {w:.0f} lb x {r:.1f} reps-to-failure")

    # Progressive refit after each set
    session_w, session_r, session_c = [], [], []
    for i, (w, r, rpe) in enumerate(sets):
        rir = 10.0 - rpe
        session_w.append(float(w))
        session_r.append(r + rir)
        session_c.append(_rpe_confidence(rpe))

        print(f"\n--- After set {i+1}: {w} x {r} @ RPE {rpe} (reps_to_fail={r+rir:.1f}) ---")
        fit = refit_from_data(
            hist_w[:], hist_r[:], hist_c[:], hist_a[:],
            session_w[:], session_r[:], session_c[:],
        )
        if not fit:
            print("  No fit")
            continue

        M, k, gamma = fit.M, fit.k, fit.gamma
        print(f"  M={M:.1f}  k={k:.1f}  gamma={gamma:.3f}  [{fit.fit_tier}]")

        # Analytical inflection point derivation for r = k*(M/w - 1)^gamma:
        #   Let u = M/w - 1, so r = k*u^gamma
        #   dr/dw = -k*gamma*M * u^(gamma-1) / w^2
        #   d2r/dw2 = k*gamma*M/w^3 * [ (gamma+1)*M/w - 2 ] * u^(gamma-2)
        #   d2r = 0 when (gamma+1)*M/w = 2, i.e. w* = M*(gamma+1)/2
        w_star = M * (gamma + 1) / 2.0
        print(f"  Inflection point: w* = M*(gamma+1)/2 = {w_star:.1f} lb")

        if w_star < M:
            if w > w_star:
                print(f"  >> Set weight {w} is PAST inflection by {w - w_star:.1f} lb (concave DOWN region)")
            else:
                print(f"  >> Set weight {w} is BEFORE inflection by {w_star - w:.1f} lb (concave UP region)")
        else:
            print(f"  >> Inflection at {w_star:.1f} >= M={M:.1f}, so entire valid range is concave UP")

        # Show numerical second derivative at the set weight
        eps = 0.5
        r_at = fresh_curve(float(w), M, k, gamma)
        r_lo = fresh_curve(float(w) - eps, M, k, gamma)
        r_hi = fresh_curve(float(w) + eps, M, k, gamma)
        d2r = (r_hi - 2 * r_at + r_lo) / (eps ** 2)
        print(f"  r''({w}) = {d2r:.6f}  ({'concave down' if d2r < 0 else 'concave up'})")

        # Where should we aim to have a set to be "past" inflection?
        if w_star < M:
            target_pct = w_star / M * 100
            print(f"  To be past inflection, need weight > {w_star:.0f} lb ({target_pct:.0f}% of 1RM)")

    conn.close()


if __name__ == "__main__":
    main()
