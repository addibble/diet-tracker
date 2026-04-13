"""Reproduce the Back Extension Machine prescription bug.
After set 2 (200 lb x 13 @ RPE 8), what did the system prescribe for set 3?"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import sqlite3
import numpy as np
from collections import defaultdict

DB_PATH = 'production_db_backup_2026-04-12-164418.db'
EXERCISE_ID = 29  # Back Extension Machine
TARGET_DATE = '2026-04-12'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Get historical sets (30-day window, RPE >= 7)
hist_rows = conn.execute("""
    SELECT ws.weight, ws.reps, ws.rpe, sess.date,
           julianday(?) - julianday(sess.date) as age_days
    FROM workout_sets ws
    JOIN workout_sessions sess ON sess.id = ws.session_id
    WHERE ws.exercise_id = ?
      AND ws.rpe >= 7
      AND sess.date >= date(?, '-30 days')
      AND sess.date < ?
      AND ws.weight > 0
    ORDER BY sess.date DESC
""", (TARGET_DATE, EXERCISE_ID, TARGET_DATE, TARGET_DATE)).fetchall()

print("=== Historical sets (30d, RPE>=7) ===")
for h in hist_rows:
    rir = 10 - h['rpe']
    r_fail = h['reps'] + rir
    brz = h['weight'] * 36.0 / (37.0 - min(r_fail, 36))
    print(f"  {h['date']}: {h['weight']} lb x {h['reps']} @ RPE {h['rpe']}"
          f"  (r_fail={r_fail:.0f}, 1RM={brz:.1f}, age={h['age_days']:.0f}d)")

# Get the exercise metadata
ex = conn.execute("SELECT * FROM exercises WHERE id = ?", (EXERCISE_ID,)).fetchone()
print(f"\nExercise: {ex['name']}, allow_heavy={ex['allow_heavy_loading']}, "
      f"load_input_mode={ex['load_input_mode']}")

# Now simulate the curve fitting
from app.strength_model import (
    fresh_curve, solve_weight, predict_reps, _rpe_confidence, _recency_weights,
    _filter_stale_sessions, _estimate_M_bounds, _identifiability_score, _fit_params,
    CurveFit, HEAVY_SCHEME, LIGHT_SCHEME, MIN_RPE_FOR_FIT, MIN_SETS_TIER1,
    MIN_SETS_TIER2, MIN_DISTINCT_WEIGHTS_TIER1, DEFAULT_GAMMA,
    GAMMA_DEMOTE_THRESHOLD, SESSION_TARGET_SHARE,
)

# Build historical observations
eff_weights = []
reps_to_failure = []
confidences = []
ages_days = []

for h in hist_rows:
    w = h['weight']
    rir = 10.0 - h['rpe']
    eff_weights.append(w)
    reps_to_failure.append(h['reps'] + rir)
    confidences.append(_rpe_confidence(h['rpe']))
    ages_days.append(float(h['age_days']))

# Filter stale sessions
print(f"\nBefore t-test filter: {len(eff_weights)} observations")
eff_weights, reps_to_failure, confidences, ages_days = _filter_stale_sessions(
    eff_weights, reps_to_failure, confidences, ages_days
)
print(f"After t-test filter: {len(eff_weights)} observations")

# Today's sets in order
today_sets = [
    {"weight": 175.0, "reps": 17, "rpe": 7.0},  # Set 1
    {"weight": 200.0, "reps": 13, "rpe": 8.0},  # Set 2
    {"weight": 220.0, "reps": 10, "rpe": 9.0},  # Set 3 (what was actually done)
]

scheme = HEAVY_SCHEME  # allow_heavy_loading = True

# Simulate prescription after each set
for n_done in range(3):
    prior = today_sets[:n_done]
    
    # Build combined observations
    all_w = list(eff_weights)
    all_r = list(reps_to_failure)
    all_c = list(confidences)
    all_a = list(ages_days)
    
    for obs in prior:
        rir = 10.0 - obs["rpe"]
        all_w.append(obs["weight"])
        all_r.append(obs["reps"] + rir)
        all_c.append(_rpe_confidence(obs["rpe"]))
        all_a.append(0.0)
    
    n_obs = len(all_w)
    W = np.array(all_w)
    r = np.array(all_r)
    conf = np.array(all_c)
    recency = _recency_weights(all_a)
    fit_w = conf * recency
    
    # Session boost
    n_session = len(prior)
    n_prior_obs = n_obs - n_session
    if n_session > 0 and n_prior_obs > 0:
        prior_total = float(np.sum(fit_w[:n_prior_obs]))
        session_total = float(np.sum(fit_w[n_prior_obs:]))
        if session_total > 0 and prior_total > 0:
            boost = (SESSION_TARGET_SHARE * prior_total) / ((1 - SESSION_TARGET_SHARE) * session_total)
            boost = max(1.0, min(boost, 100.0))
            fit_w[n_prior_obs:] *= boost
            print(f"\n  Session boost for set {n_done+1} prescription: {boost:.1f}x")
    
    distinct_w = len(set(round(w, 1) for w in all_w))
    tier = "tier1" if n_obs >= MIN_SETS_TIER1 and distinct_w >= MIN_DISTINCT_WEIGHTS_TIER1 else "tier2"
    
    M_lower, M_upper, M_prior = _estimate_M_bounds(all_w, all_r)
    ident = _identifiability_score(all_w, all_r)
    lambda_M = 10.0 + 20.0 * (1.0 - ident)
    
    fixed_gamma = DEFAULT_GAMMA if tier == "tier2" else None
    M_fit, k_fit, gamma_fit, success = _fit_params(
        W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, fixed_gamma
    )
    
    if tier == "tier1" and gamma_fit < GAMMA_DEMOTE_THRESHOLD:
        tier = "tier2"
        M_fit, k_fit, gamma_fit, success = _fit_params(
            W, r, fit_w, M_lower, M_upper, M_prior, lambda_M, DEFAULT_GAMMA
        )
    
    fit = CurveFit(M=M_fit, k=k_fit, gamma=gamma_fit, n_obs=n_obs,
                   rmse=0, max_observed_weight=float(np.max(W)),
                   fit_tier=tier, identifiability=ident)
    
    print(f"\n{'='*60}")
    print(f"After {n_done} sets → prescribing set {n_done+1}:")
    print(f"  Fit: M={M_fit:.1f}, k={k_fit:.1f}, γ={gamma_fit:.3f}, tier={tier}")
    print(f"  n_obs={n_obs}, distinct_w={distinct_w}, ident={ident:.3f}")
    
    set_idx = n_done
    if set_idx < len(scheme):
        r_fail_target, rir, target_rpe, expected_reps, rep_min, rep_max = scheme[set_idx]
        prescribed_w = solve_weight(r_fail_target, fit)
        actual_r_fail = predict_reps(prescribed_w, fit)
        actual_expected = max(1, round(actual_r_fail - rir))
        
        print(f"  Scheme: r_fail_target={r_fail_target}, RIR={rir}, target_rpe={target_rpe}")
        print(f"  → Prescribed weight: {prescribed_w:.1f} lb")
        print(f"  → Predicted r_fail at that weight: {actual_r_fail:.1f}")
        print(f"  → Expected reps: {actual_expected} (RPE {target_rpe})")
        
        # What does the curve predict at 200 lb specifically?
        r_at_200 = predict_reps(200.0, fit)
        print(f"  → Curve predicts r_fail={r_at_200:.1f} at 200 lb")
        r_at_220 = predict_reps(220.0, fit)
        print(f"  → Curve predicts r_fail={r_at_220:.1f} at 220 lb")
    
    if n_done < 3:
        actual = today_sets[n_done]
        print(f"  ACTUAL: {actual['weight']} lb x {actual['reps']} @ RPE {actual['rpe']}")

conn.close()
