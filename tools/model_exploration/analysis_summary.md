# Model Exploration — Refreshed Analysis (April 2026)

**Dataset cutoff:** `production_db_backup_2026-04-18-085900.db` (sessions
`2025-08-24` → `2026-04-17`; 4113 sets, 676 with RPE — RPE data starts
2026-03 after progressive-overload rollout).

**Scope:** Purely offline analysis in `tools/model_exploration/`. No
production code changes. Baseline is the existing fresh-curve
`r_fresh(W) = k · (M/W − 1)^γ` with Brzycki-bounded M fit in
`strength_curve.py`. We look for *additional* signal in the residual.

## Method

1. **Leakage-free curve fits.** For every distinct session date *t* we refit
   one strength curve per exercise using only RPE sets with
   `session_date < t`. This cached per-date fit produces a baseline
   `rtf_predicted` for every RPE set on date *t*. 25 distinct dates → 388
   sets predictable (the 273 un-predictable sets are cold-start for their
   exercise in early-March 2026).
2. **Residual = `rtf_actual − rtf_predicted`.** `rtf_actual = reps + (10 − RPE)`.
3. **Freshness features per session-exercise:** days since last session,
   same exercise, same primary tissue, same Push/Pull/Legs/Core group;
   7-day rolling volume on same primary tissues; exercise order in session.
4. **Within-session features per set:** AR(1) dose accumulator, prior
   volume from earlier sets of this exercise, prior volume from earlier
   exercises in this session.

## Headline numbers

### Baseline bias — the dominant signal

| Model | Overall RMSE | Set-1 RMSE | Set-2 RMSE | Set-3 RMSE |
|---|---|---|---|---|
| M0: fresh curve only | 6.18 | 7.19 | 6.91 | 4.26 |
| M1: + per-exercise intercept | **3.68** | 4.17 | 3.36 | 3.55 |
| M2: M1 + per-session intercept | 3.33 | 3.65 | 3.00 | 3.43 |
| M3a: M2 + linear local fatigue (prior AR(1) dose) | 3.31 | 3.58 | 3.03 | 3.42 |
| M3b: M2 + per-(exercise, set-index) dummies | **3.08** | 3.28 | 2.92 | 3.14 |
| M3c: M2 + global per-set-index | 3.20 | 3.47 | 3.00 | 3.22 |

**40% of the residual variance (M0→M1) is just per-exercise baseline bias**
in the fresh curve, not fatigue. This is the biggest single prediction win
available.

### Signed bias by set index under the best models

| Model | Set 1 | Set 2 | Set 3 |
|---|---|---|---|
| M0 fresh curve | **+2.26** | **+1.02** | +0.36 |
| M1 per-exercise intercept only | +1.18 | ±0 | −1.19 |
| M2 (+ session intercept) | +1.13 | ±0 | −1.18 |
| M3c (+ global per-set-index) | ±0 | ±0 | ±0 |

The signed bias monotonically decays by **~1.2 reps per set** under M1/M2.
That's the within-session fatigue signal in its cleanest form.

## Finding 1 — Set-1 accuracy and "freshness"

`set1_freshness.py` regresses Set-1 residual on freshness features
after de-meaning by exercise (so exercise-specific baseline bias doesn't
contaminate the freshness signal):

- **Joint OLS R² on exercise-demeaned residuals: 0.075** (7.5%).
  The hypothesis that wall-clock freshness is a major Set-1 driver is
  **not supported** by the data. Most of the Set-1 variance is
  exercise-specific.
- The strongest standardized coefficient was `days_since_any_session`
  (−0.99): more days off → slightly **smaller** underprediction. That's
  the opposite direction from a "detraining" story and is probably
  confounded by training-cycle effects (deload/fresh weeks also happen
  to be lighter sessions).
- `acute_load_7d_same_tissue` had a positive standardized coefficient
  (+0.51, Pearson r = +0.15): **more recent volume → bigger
  underprediction**. Reading: when a tissue is being trained heavily the
  athlete is actively getting stronger, and the curve (fit from older
  data) lags the current state.
- `days_since_same_exercise` is non-monotone: +4.3 residual at 7–10 days,
  −0.4 at >10 days. Likely a mix of true weekly periodicity and
  small-sample noise (31 sets in the >10-day bucket with std ~10 rep).

Bottom line: **"time since last" features are not the Set-1 predictor you
want.** The real Set-1 bias is ~+1.2 reps after removing exercise
baseline — largely a fresh-curve miscalibration (underprediction at
lighter weights, especially for face-pulls-style exercises previously
trained only at a single weight).

## Finding 2 — Set-2 / Set-3 fatigue decomposition

Two compartments were fit on residual after M1 (per-exercise intercept):

- **Session intercept α_j** (your "metabolic/day" hypothesis) —
  explains a further ~10% RMSE reduction (M1→M2). **Std across
  sessions ≈ 0.5–1 rep**, not large. **Correlation with observable
  session covariates is ~0.0:**
  - `corr(α_j, prior_session_volume_max) = −0.04`
  - `corr(α_j, ex_order_max) = −0.08`

  So session intercepts are real but are not predicted by how much
  volume has accumulated earlier in the session. This bucket captures
  unobservable daily state (sleep, nutrition, stress, random CNS
  readiness) rather than within-day metabolic accumulation.

- **Local within-exercise fatigue β_{e,s}.** Three parameterizations
  tried:
  - Linear `−θ · prior_dose_ar1` (θ = −1.05): essentially no
    improvement beyond session intercept (RMSE 3.33 → 3.31).
  - **Per-(exercise, set-index) dummies**: 3.33 → 3.08, the best model.
    Captures exercise-specific fatigue decay that a single dose
    kernel misses.
  - **Global per-set-index** (single shared β across exercises):
    3.33 → 3.20. Residual-after-M2 values are
    Set1 +1.13, Set2 ±0, Set3 −1.18. This is a clean **~1.2 reps/set
    linear decay**, shared across all exercises.

Net: the cleanest story is **per-exercise baseline bias + per-session
random intercept + shared monotone per-set-index fatigue**. The
AR(1) dose accumulator that we had sketched in `session_fatigue.py`
does **not** win on this data — too many free parameters chasing the
same signal that a 3-parameter per-set-index dummy captures.

## Rubber-duck interpretation

Your two-compartment hypothesis is close but needs revision:

- **"Overall metabolic fatigue that varies by day"**: CONFIRMED as a
  random intercept, but it's SMALL (std ~0.5–1 rep) and is **not
  predictable** from the covariates we can observe (prior session
  volume, exercise order). Sleep/nutrition would need to be logged
  separately to push this further. Treating it as an unobservable random
  effect is the honest move.
- **"Tissue-specific parameter that depends on the exercise being
  done"**: CONFIRMED via the per-(exercise, set-index) model. Isolation
  exercises fatigue faster than compounds in the data
  (`BY ISOLATION FLAG`: isolation Set-1 bias +2.72, compound +1.51 —
  isolation starts with a bigger underprediction, but the per-set decay
  is steeper so later sets cross zero).
- **"Accumulated volume earlier in the session"**: NOT CONFIRMED.
  `prior_session_volume` correlation with session intercept ≈ 0. The
  within-session fatigue signal appears to be dominated by the current
  exercise's own history, not by earlier exercises.

## Recommended next steps (exploration only, not production)

1. **Per-exercise fresh-curve recalibration** (biggest RMSE win, and
   safest): add a per-exercise offset to the fresh curve, learned from
   a rolling trailing window. Currently the curve fit's point estimate
   ignores systematic bias at light weights (face pulls, cable curls).
   A simple residual offset would give a 40% RMSE cut with almost zero
   risk.
2. **Per-exercise, per-set-index fatigue table** (second biggest win):
   learn a 3-vector `β_e = (0, β_{e,2}, β_{e,3})` per exercise and
   subtract at prediction time. Cheap, interpretable, and gives the
   remaining ~15% RMSE cut.
3. **Session intercept as a random effect only** — don't try to
   predict it from covariates in production; just shrink to zero for new
   sessions. Revisit if/when we start logging sleep/nutrition.
4. **Retain the AR(1) dose machinery for recovery/volume tracking** but
   drop it from within-session rep prediction — it is not competitive
   with the set-index dummies here.

## Files

- `features.py` — per-set freshness/within-session feature engineering.
- `residuals.py` — leakage-free residual table.
- `set1_freshness.py` — Set-1 analysis; plots under
  `plots/set1_freshness/`.
- `fatigue_decomposition.py` — M0…M3c model comparison; plots under
  `plots/fatigue/`.
- `refresh_analysis.py` — single entrypoint running all of the above.
- `literature_review.md` — sports-science backing for the decomposition.

## Deprecated (superseded by this refresh)

- `analyze_today.py`, `set3_prediction.py`, `test_new_production.py`,
  `targeted_improvements.py` — all used hard-coded 2026-04-11 data and
  an as-of cutoff that is no longer current. Kept in the tree for
  reference but not part of the pipeline.
