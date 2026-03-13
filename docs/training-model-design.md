# Strength-and-Trouble Forecasting Model

This document describes the current training model implementation in the diet tracker and the exercise recommendations built on top of it.

Relevant code:

- [backend/app/training_model.py](/Users/drew/src/diet-tracker-codex3/backend/app/training_model.py)
- [backend/app/routers/training_model.py](/Users/drew/src/diet-tracker-codex3/backend/app/routers/training_model.py)
- [backend/app/models.py](/Users/drew/src/diet-tracker-codex3/backend/app/models.py)
- [backend/app/llm_tools/workout.py](/Users/drew/src/diet-tracker-codex3/backend/app/llm_tools/workout.py)
- [frontend/src/api.ts](/Users/drew/src/diet-tracker-codex3/frontend/src/api.ts)
- [frontend/src/pages/WorkoutPage.tsx](/Users/drew/src/diet-tracker-codex3/frontend/src/pages/WorkoutPage.tsx)

## Goals

The model is built to answer two practical questions:

1. Which tissues look likely to get into trouble soon, before an obvious collapse in training volume?
2. Which exercises should be done, used cautiously, or avoided today if the goal is to keep building strength while avoiding injury?

The model is explicitly not trying to reconstruct true anatomical force from bar path, joint angles, or biomechanics lab data. The input data does not support that. Instead, it treats logged training as a repeated exposure system:

- exercises route load into tissues
- tissues have user-specific latent capacity
- fatigue rises and decays over time
- recovery can be inferred from overload, deload, and rebound behavior
- future trouble can be forecast from patterns that historically preceded collapse or injury-condition notes

## Modeling Philosophy

The original workout tracker already had useful structure:

- exercises
- tissues
- exercise-to-tissue mappings
- loading factors
- recovery-hour heuristics
- tissue condition notes
- routine targets

The training model keeps that structure but changes the interpretation:

- `loading_factor` is no longer treated as truth
- `routing_factor` is the actual load-allocation prior used by the model
- `tissue_capacity` is learned from history instead of being fixed
- `recovery_tau_days` is learned from rebound behavior instead of being trusted blindly
- collapse and injury notes are treated as outcome labels for risk learning

This is a hybrid model, not a full neural net:

- deterministic state equations encode load, fatigue, recovery, and capacity drift
- learned coefficients calibrate risk scoring from event vs non-event windows
- exercise recommendations are derived from tissue state, not predicted independently by a black box

That makes the system inspectable and easier to debug when a recommendation looks wrong.

## Data Model

The following tables support the model.

### `exercises`

Additional fields:

- `load_input_mode`
- `bodyweight_fraction`
- `estimated_minutes_per_set`

Purpose:

- tells the model how to interpret raw set weight
- allows bodyweight and mixed-load exercises to participate in the same pipeline
- supports future efficiency and time-spent analyses

### `exercise_tissues`

Legacy fields remain:

- `role`
- `loading_factor`

New fields:

- `routing_factor`
- `fatigue_factor`
- `joint_strain_factor`
- `tendon_strain_factor`

Purpose:

- `routing_factor` controls how much exercise load flows into a tissue
- `fatigue_factor` controls how much acute fatigue accumulates
- `joint_strain_factor` and `tendon_strain_factor` bias strain-sensitive tissues upward

The old `loading_factor` is preserved as a prior and for compatibility, but it is not treated as a physiological truth.

### `tissue_model_configs`

Per-tissue defaults and tunables:

- `capacity_prior`
- `recovery_tau_days`
- `fatigue_tau_days`
- `collapse_drop_threshold`
- `ramp_sensitivity`
- `risk_sensitivity`

Purpose:

- gives each tissue a seeded prior when history is sparse
- keeps the model bounded and regularized instead of overreacting to tiny samples

### `tissue_recovery_logs`

Optional subjective data:

- `soreness_0_10`
- `pain_0_10`
- `readiness_0_10`
- `source_session_id`

Purpose:

- future calibration path for subjective recovery
- not yet heavily used in v1 scoring

### `training_exclusion_windows`

Fields:

- `start_date`
- `end_date`
- `kind`
- `notes`
- `exclude_from_model`

Purpose:

- removes non-training disruptions from overload learning
- currently used for the surgery window from December 16, 2025 through December 31, 2025

Without exclusion windows, the model would incorrectly interpret surgery deloads as successful overload recovery.

## Input Preparation

The model builds a full context in `build_training_model_summary()` and `_build_context()`.

Preparation rules:

- future sessions are excluded if `as_of` is provided
- empty placeholder sessions are ignored
- duplicate same-day set signatures are deduplicated
- future weight logs are ignored
- exclusion windows are expanded into explicit excluded calendar days

The result is:

- a normalized history window
- daily tissue exposure records
- per-tissue condition events
- per-exercise to per-tissue event statistics

## Effective Load

Every informative set is converted into a scalar effective load:

```text
effective_set_load
= reps * effective_weight * effort_factor * completion_factor
```

### Effective weight

`effective_weight` depends on the exercise:

- `external_weight`: logged set weight
- `bodyweight`: latest known bodyweight on or before the workout date times `bodyweight_fraction`
- `mixed`: external load plus bodyweight component
- timed sets currently do not contribute meaningful training load unless there is rep-based load

### Effort factor

If `rpe` is present:

```text
effort_factor = clamp(1 + 0.05 * (rpe - 7), 0.85, 1.15)
```

If `rpe` is missing:

- default `effort_factor = 1.0`

Because `rpe` coverage is sparse in the current data, the model does not infer aggressive effort from missing values.

### Completion factor

- `full = 1.0`
- `partial = 0.9`
- `failed = 1.05`

This slightly increases stress for failed work and discounts partial completion.

## Tissue Exposure

For each mapped tissue, the model accumulates daily exposure channels:

- `raw_load`
- `fatigue_load`
- `strain_load`
- `failures`
- `exercise_loads`

Core routing:

```text
raw tissue load contribution
= effective_set_load * routing_factor
```

Additional channels:

- `fatigue_load += effective_set_load * fatigue_factor`
- `strain_load += effective_set_load * max(joint_strain_factor, tendon_strain_factor)`
- failed reps increment a discrete failure signal

These exposures are collected in `_collect_daily_exposure()`.

## Capacity Normalization

Raw external load is not directly meaningful across exercises. A 400 lb leg press and a 400 lb row do not imply the same tissue demand. The model therefore learns a per-tissue capacity baseline and normalizes exposure against it.

Baseline capacity is estimated from the tissue's own non-excluded history:

- collect positive `raw_load` values
- take approximately the 75th percentile
- fall back to `capacity_prior` when sparse

Normalized load:

```text
normalized_load = raw_load / max(current_capacity, 1.0)
```

Interpretation:

- `normalized_load ~= 1.0`: recent demand is around the tissue's current capacity estimate
- `normalized_load > 1.0`: the tissue is being asked to do more than its current modeled baseline
- `normalized_load < 1.0`: current demand is below modeled baseline

This is the key step that makes the exercise-to-tissue graph numerically interpretable instead of just heuristic.

## Learned Recovery

Recovery is inferred from overload, deload, and rebound behavior in `_learn_recovery_days()`.

High-level logic:

- find periods with meaningful prior load
- detect a moderate drop in the next window
- look for rebound to around 80% of prior baseline
- measure days until rebound
- blend the observed rebound timing with the seeded `recovery_tau_days`

Important design choice:

- moderate deloads after overload are treated as recovery evidence
- exclusion windows are ignored completely

This matches the user's training pattern: many deloads are real recovery responses, but the surgery window should not teach the model anything about healthy rebound.

## Collapse Detection

The model needs labels for "trouble happened here." It does not use only explicit injury notes, because those are sparse. Instead it constructs collapse signals in `_detect_collapse_dates()`.

A collapse window is flagged when:

- there is enough recent history to establish a baseline
- there is enough future history to see whether exposure drops
- the date is not in an exclusion window
- future average load drops enough below recent baseline

The threshold is controlled per tissue by `collapse_drop_threshold`.

This does not prove injury. It marks a date whose surrounding training pattern resembles a training collapse. Those windows are then used as downstream targets for risk learning.

## Tissue State Model

For each tissue and day, `_compute_tissue_states()` tracks:

- `raw_load`
- `normalized_load`
- `capacity_state`
- `acute_fatigue`
- `chronic_load`
- `recovery_state`
- `ramp_ratio`
- `risk_7d`
- `risk_14d`
- `collapse_flag`
- `failure_count`
- `contributors`

### Fatigue and chronic load

Fatigue and chronic load both decay exponentially:

```text
acute_fatigue = decay(previous_acute_fatigue, fatigue_tau) + fatigue_load / capacity
chronic_load = decay(previous_chronic_load, chronic_tau) + normalized_load
```

Interpretation:

- `acute_fatigue` reacts faster to recent stress
- `chronic_load` is a slower-running baseline

### Recovery state

Recovery is represented as:

```text
recovery_state = 1 / (1 + acute_fatigue)
```

Interpretation:

- near `1.0` means low current fatigue
- lower values mean the tissue is still carrying acute stress

### Capacity state

Capacity is allowed to drift:

- back toward baseline over time
- upward when the tissue sees tolerable challenge with decent recovery
- downward when normalized load exceeds the safe band

The update function is:

```text
drift = current_capacity + (baseline_capacity - current_capacity) * 0.04
adaptation = baseline_capacity * max(0, min(normalized_load, 1.15) - 0.45) * 0.03 * recovery_state
penalty = baseline_capacity * max(0, normalized_load - 1.25) * 0.035
next_capacity = max(baseline_capacity * 0.55, drift + adaptation - penalty)
```

Interpretation:

- challenging but recoverable work improves capacity
- excessive work degrades it
- the state is bounded so it cannot collapse unrealistically

### Ramp ratio

Ramp is a spike detector:

```text
ramp_ratio = recent_7 / max(recent_28 / 4, baseline_capacity * 0.15, 1.0)
```

Interpretation:

- `1.0` is roughly stable versus recent baseline
- higher values indicate aggressive ramping

This is one of the strongest risk signals in the current model.

## Risk Learning

The model predicts:

- `risk_7d`
- `risk_14d`

These are not clinically calibrated injury probabilities. They are event-similarity scores: "how much does the current state resemble periods that led to collapse or condition events in the next horizon?"

### Event targets

Targets are built from:

- collapse dates
- explicit tissue condition dates

For each date:

- if a collapse or condition note occurs within the next horizon, that date is treated as an event sample
- otherwise it is a non-event sample

### Learned coefficients

`_learn_event_coefficients()` computes a small per-feature scaling by comparing event vs non-event means for:

- `normalized_load`
- `acute_ratio`
- `ramp_ratio`
- `condition`
- `prior`

Each coefficient is clamped to stay within a reasonable range.

This is a compact supervised layer on top of the mechanistic state model.

### Risk score

`_score_risk()` combines:

- sustained normalized load
- acute fatigue
- ramp ratio
- current tissue condition severity
- similarity to prior collapse zones
- recent failed reps

Each feature contributes with a base weight multiplied by its learned coefficient.

Those contributions are then passed through a logistic transform to produce a 0-100 score.

Interpretation:

- `0-30`: currently low similarity to pre-trouble patterns
- `30-60`: monitor
- `60+`: strong warning band
- `80-100`: current pattern looks very similar to prior pre-collapse or pre-note windows

Important caveat:

- `100%` does not mean certainty of injury
- it means the model is seeing a very strong pattern match to previous trouble precursors

## Contributor Strings

The model also returns short driver labels for interpretability. Current labels include:

- `sustained normalized load`
- `acute fatigue`
- `aggressive ramp`
- `recent tissue condition`
- `historical collapse proximity`
- `recent failed reps`

These are derived from whichever features contributed most to the current tissue risk score.

## Exercise-Level Recommendations

The app does not ask the user to translate tissue rows into exercise decisions manually. Instead it builds exercise recommendations in `build_exercise_risk_ranking()`.

For each exercise:

- gather all mapped tissues
- pull each tissue's current state
- compute weighted exercise metrics from routing factors

Current output fields:

- `weighted_risk_7d`
- `weighted_risk_14d`
- `max_tissue_risk_7d`
- `weighted_normalized_load`
- `suitability_score`
- `recommendation`
- `recommendation_reason`
- `recommendation_details`
- `blocked_tissues`
- `favored_tissues`
- detailed per-tissue subrows

### Weighted risk

Exercise risk is computed by weighting tissue risk by routing factor:

```text
weighted_risk_7d
= sum(routing_factor * tissue_risk_7d) / sum(routing_factor)
```

Equivalent calculations are used for:

- 14-day risk
- normalized load

### Suitability score

The current suitability score is:

```text
suitability
= clamp(
  100
  - weighted_risk_7d
  - max_tissue_risk_7d * 0.2
  + recovering_bonus * 10,
  0,
  100
)
```

This is deliberately simple:

- exercise becomes less suitable as risk rises
- exercise gets a small boost if it trains tissues that are recovering well and not currently high-risk

### Recommendation class

Current recommendation thresholds:

- `avoid`
  - blocked tissues exist and either `max_tissue_risk_7d >= 75` or `weighted_risk_7d >= 60`
- `caution`
  - `max_tissue_risk_7d >= 55` or `weighted_risk_7d >= 40`
- `good`
  - otherwise

### Recommendation reason

The backend also emits explicit reason strings so the frontend and LLM do not have to reconstruct them:

- examples:
  - `Avoid because it directly loads Lumbar Spine, External Oblique while current tissue risk is elevated.`
  - `Use caution because recent tissue risk is elevated and this exercise still leans on Hip Joint.`
  - `Good candidate because its main tissues are recovering well and current weighted risk is low.`

`recommendation_details` contains shorter fragments such as:

- `high 7d tissue risk`
- `max tissue risk 88%`
- `loads Lumbar Spine, Rectus Abdominis`
- `favours recovering tissues: Lower Trapezius, Infraspinatus`

These are useful both for UI display and for LLM tool output.

## API Surface

The training model router exposes the following endpoints.

### `GET /api/training-model/summary`

Returns:

- top-level overview
- current per-tissue states
- exercise insights

Primary use:

- dashboard summary
- frontend overview cards

### `GET /api/training-model/exercises`

Returns exercise-level recommendation rows.

Query params:

- `as_of`
- `sort_by = risk_7d | risk_14d | suitability | normalized_load`
- `direction = asc | desc`
- `limit`
- `recommendation = avoid | caution | good`

Primary use:

- action-oriented workout selection
- LLM exercise recommendation queries

### `GET /api/training-model/tissues/{tissue_id}/history`

Returns:

- current tissue metadata
- learned recovery days
- collapse dates
- recent state history

Primary use:

- "why is this tissue at risk?"
- debugging and interpretation

### `GET/POST/DELETE /api/training-model/exclusion-windows`

Primary use:

- surgery
- illness
- travel
- other periods that should not be interpreted as training response

### `POST /api/training-model/recovery-log`

Primary use:

- future subjective calibration path

## Frontend Recommendation Flow

The workout page uses two model-driven surfaces.

### Exercise board

The main recommendation board splits exercises into:

- `Avoid Today`
- `Use Caution`
- `Good Candidates`

This gives a direct action surface without requiring the user to mentally map tissue warnings back to exercises.

### Routine ordering

The routine card is now ordered by current recommendation:

- good candidates first
- caution next
- avoid last

Within those bands, exercises are ordered by suitability and then by routine sort order.

That means the page does not just describe tissue risk; it changes how the planned session is presented.

## LLM Integration

The workout tool surface exposes model-aware exercise rows through `get_exercises(... include=["training_risk"])`.

This allows the LLM to ask for:

- exercises to avoid today
- good candidates today
- exercises sorted by 7-day tissue risk
- exercises sorted by suitability

Because the backend now emits explicit recommendation reasons, the LLM can explain recommendations using the model's own reasoning instead of freehand physiological guesses.

## Current Limitations

This is still a v1 system.

Known limitations:

- no inverse-dynamics biomechanics
- sparse `rpe`
- no explicit hypertrophy state yet
- limited handling of true exercise substitution equivalence
- no personalized subjective recovery learning yet
- risk percentages are heuristic similarity scores, not calibrated injury probabilities
- exercise routing quality still depends heavily on mapping quality

The current system is best interpreted as:

- good for ranking and triage
- good for spotting ramps and overloaded regions early
- useful for comparing exercises against one another today
- not yet a source of precise physiological truth

## Implemented Next Steps

The first next-step improvements now in progress after v1 are:

1. Backend-owned exercise recommendation reasons.
2. Routine ordering by recommendation band and suitability.
3. Better LLM interpretability through exercise-level rather than tissue-only reasoning.

Those changes make the model easier to trust operationally because the same recommendation semantics flow through backend, frontend, and LLM tooling.

## Future Next Steps

High-value follow-up work:

1. Use `tissue_recovery_logs` to adjust recovery estimates directly.
2. Detect substitution-equivalent exposure so routine changes are not mistaken for injury.
3. Add explicit exercise-level "recheck date" or "safe to retry in N days" estimates.
4. Add a session-construction helper that selects today's best routine subset automatically.
5. Calibrate risk scores against a larger history so thresholds become less heuristic.
6. Add hypertrophy-oriented secondary objectives after the strength-and-trouble foundation is stable.

