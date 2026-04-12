# Model Analysis Summary — April 11, 2026 Workout

## ⚠️ CORRECTED — Previous analysis had a critical data bug

The original analysis filtered sets using `ws.completed_at` instead of
`workout_sessions.date`. Since `completed_at` is only populated for today's
progressive sets, **59% of all prior RPE data was invisible** (288 of 492 sets).
All results below use the corrected queries joining on `workout_sessions.date`.

## Data Analyzed
- 8 exercises, 24 sets from today's progressive-overload session
- **ALL 8 exercises had prior RPE data** (no cold-start — previously misreported as 3 cold-start)
- Prior data counts: Barbell Curl (5), Face Pulls (11), Incline Hammer Curl (4),
  Leg Press (13), Lat Pulldown (9), Preacher Curl (11), Cable Row (16), Cable Curl (9)

## Production Model Performance (corrected baseline)
- **Mean RMSE: 3.41** (previously reported as 3.77 — model is better than we thought)
- Set 1 bias: **-3.25** (systematically underpredicts reps on first set)
- Set 2 bias: **-1.36** (still underpredicts)
- Set 3 bias: **+0.05** (nearly perfect on last set)
- Root cause: Model tends to underestimate what the athlete can do, especially at
  lighter weights. This is likely because prior data is clustered at moderate
  weight/rep combos and the curve extrapolates conservatively.

## What Did NOT Help (corrected results)

### γ=0.50 — No improvement (was previously the #1 recommendation!)
- Mean RMSE: 3.43 vs 3.41 — essentially tied
- With correct data, most exercises qualify for **Tier 1** (free gamma fit from data),
  so the tier2 default gamma is rarely used
- The previous "17% improvement" was an artifact of missing data forcing Tier 2 fits

### Fatigue discount — Actually hurts
- Adding -1.0/set fatigue: RMSE goes UP from 3.41 → 3.74
- Adding -1.0/set fatigue + 3x boost: 3.48 — still worse than no fatigue
- The model already underpredicts; subtracting more makes it worse
- Previously showed improvement because buggy data made the model overpredict

### Session boost — Marginal
- 3x boost with fatigue: 3.48 (worse than baseline)
- Helps some exercises (Preacher Curl, Straight-Bar Curl) but hurts others

## Per-Exercise Analysis (corrected)

### Near-perfect predictions
- **Barbell Curl**: RMSE 0.43 — all 3 sets within 1 rep of prediction. Model works great here.
- **Leg Press**: RMSE 1.65 — slight underprediction on Set 1, very good on Sets 2-3.

### Systematic underprediction
- **Face Pulls**: RMSE 5.92 — underpredicts by 5-7 reps every set. Prior data was all
  at 42.5lb; today used 22.5-37.5lb. The athlete is much stronger than the curve
  suggests at lower weights. The curve shape may be wrong for this exercise class.
- **Seated Cable Row V-grip**: RMSE 3.71 — underpredicts all sets by 3-4 reps. Prior data
  was clustered at 105-110lb × 12-20. The curve is anchored there and underestimates
  performance at 140lb (heavy end) AND 110lb (same weight, different day).
- **Incline Hammer Curl**: RMSE 3.85 — Set 1 at 10lb underpredicted by 5.7 reps (very light).

### Mixed / overprediction on later sets
- **Preacher Curl**: RMSE 4.58 — Set 1 underpredicted (-4.5), but Sets 2-3 overpredicted
  (+3.4, +5.6). This is the one exercise where fatigue discount helps — the athlete
  fatigues faster than others on this isolated movement.
- **Straight-Bar Cable Curl**: RMSE 3.57 — Similar: Set 1 perfect, Sets 2-3 overpredicted.
  Fatigue matters for curls.
- **Neutral Grip Lat Pulldown**: RMSE 3.59 — Set 2 at 165lb badly underpredicted
  (actual 18, pred 11.9). Likely the athlete was stronger than expected at this specific weight.

## Key Insight: The Model Problem is Heterogeneous

The underprediction pattern isn't uniform — it depends on exercise type:

1. **Compound movements** (Leg Press, Barbell Curl, Lat Pulldown): Model is quite accurate.
   Fatigue matters less, prediction is good.

2. **High-rep / light-weight exercises** (Face Pulls, Cable Row): Model systematically
   underpredicts, especially at weights below the prior data cluster. The curve shape
   is too conservative at extrapolating to lighter weights.

3. **Isolation curls** (Preacher Curl, Cable Curl): Model is good on Set 1 but
   overpredicts Sets 2-3. These exercises fatigue faster. A fatigue discount of
   ~1-2 reps/set would help specifically for these.

## Revised Recommendations

### 1. Do NOT change default gamma to 0.50
- With correct data, this has no meaningful effect
- The tier1/tier2 system is working as designed

### 2. Consider per-exercise-class fatigue rates
- Isolation exercises (curls): fatigue ~1-2 r_fail/set
- Compound exercises: fatigue ~0.3 r_fail/set or negligible
- This could be tied to muscle group or exercise category

### 3. Investigate the consistent underprediction on Set 1
- Average Set 1 bias = -3.25 reps (model says you'll do 3 fewer than you actually do)
- This suggests either: recent strength gains, or warm-up set performance
  exceeds cold-fit estimates, or the curve shape is systematically too conservative
- Possible fix: shorter half-life to weight recent data more, or a Set 1 "optimism" offset

### 4. Face Pulls needs investigation
- RMSE 5.92 is the worst by far
- Prior data was all at ONE weight (42.5lb). Today's range was 22.5-37.5lb
- The curve literally has no information about performance at these lower weights
- This is a **data diversity problem**, not a model problem. The progressive set scheme
  should fix this over time by collecting data at 3 different weights per session.

## Plots Generated
All saved to `tools/model_exploration/plots/`:
- `FINAL_model_comparison.png` — Side-by-side: production vs proposed (all 8 exercises)
- `final_summary.png` — RMSE heatmap + overall comparison
- `targeted_*.png` — Per-exercise comparisons across all configs

## Files Modified (experimental only, not production)
- All `get_prior_data()` functions fixed to use `workout_sessions.date` instead of `ws.completed_at`
- No production code was changed.

## Production Code Status
- `backend/app/strength_model.py` uses `WorkoutSession.date` — **NOT affected by this bug**
- The bug was only in experimental analysis scripts
