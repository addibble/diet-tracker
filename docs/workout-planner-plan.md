# Workout Planner & Recovery Learning — Implementation Plan

This document describes the implementation plan for upgrading the training model with subjective recovery learning, estimated 1RM strength tracking, rep-range-aware load channels, exposed model diagnostics, and an algorithmic workout planner.

## Phase 1: Data Quality Fixes

### 1a. Fix bodyweight exercise load calculation

**Problem:** Exercises like pushups have `load_input_mode = "external_weight"` and `bodyweight_fraction = 0.0`, so the model sees zero load from bodyweight work.

**Changes:**
- Add LLM tool support for updating `load_input_mode` and `bodyweight_fraction` on exercises
- Add an API endpoint or LLM tool to audit exercises with `equipment = "bodyweight"` that still have `load_input_mode = "external_weight"`
- Common bodyweight fractions (research-based):
  - Pushup: 0.64
  - Pull-up / Chin-up: 1.0
  - Dip: 1.0
  - Inverted Row: 0.6
  - Plank: 0.5
  - Lunge (bodyweight): 0.5
  - Pistol squat: 0.95

**Files:** `backend/app/routers/exercises.py`, `backend/app/llm_tools/workout.py`

### 1b. Add `stiffness_0_10` to `TissueRecoveryLog`

**Problem:** Soreness, pain, and readiness exist but stiffness (a joint/tendon signal distinct from muscular soreness) is missing.

**Changes:**
- Add `stiffness_0_10: int = 0` column to `TissueRecoveryLog`

**Files:** `backend/app/models.py`, `backend/app/routers/training_model.py`

## Phase 2: Estimated 1RM & Strength Tracking

### 2a. Compute per-exercise per-session estimated 1RM

**Problem:** The model treats 10×100 the same as 5×200 (both = 1000 load units). It cannot distinguish strength vs endurance adaptation or track "am I getting stronger?"

**Changes:**
- Add `_estimated_1rm(weight, reps)` function using Epley formula: `weight × (1 + reps/30)`. Cap at 10 reps (formula unreliable above that).
- During `_collect_daily_exposure()`, track per-exercise best e1RM per session alongside existing load.
- Store in a new context field `e1rm_by_exercise`: `dict[int, list[tuple[date, float]]]` — time series of best e1RM per exercise per day.

**Files:** `backend/app/training_model.py`

### 2b. Expose e1RM trends via API

**Changes:**
- New endpoint `GET /api/training-model/exercises/{exercise_id}/strength` returning e1RM time series, trend direction, and recent peak.
- Include e1RM in the exercise risk ranking response for each exercise.

**Files:** `backend/app/routers/training_model.py`, `backend/app/training_model.py`

## Phase 3: Rep-Range-Aware Load Channels

### 3a. Split tissue exposure into stimulus channels

**Problem:** All load is pooled into one `raw_load` regardless of rep range. A 3×5 heavy day and a 3×15 pump day look identical if total volume matches.

**Changes:**
- Add three new fields to `ExposureRecord`:
  - `strength_load` (1-5 reps): contributes more to neural/strength adaptation
  - `hypertrophy_load` (6-12 reps): primary muscle-building stimulus
  - `endurance_load` (13+ reps): muscular endurance / conditioning
- In `_collect_daily_exposure()`, route `effective_load × routing_factor` into the appropriate channel based on set rep count.
- `raw_load` remains the sum of all channels (backward compatible).

**Files:** `backend/app/training_model.py`

### 3b. Use stimulus channels in capacity and recovery

**Changes:**
- Capacity estimation: weight strength_load higher in capacity drift (heavy work drives capacity up faster).
- Recovery learning: track which rep ranges produce longer soreness-decay curves (Phase 4 dependency).
- Risk scoring: high strength_load with low recovery increases injury risk more than high endurance_load.

**Files:** `backend/app/training_model.py`

## Phase 4: Subjective Recovery Integration

### 4a. Body region grouping layer

**Problem:** Logging soreness per-tissue (40+ tissues) is too high-friction. Users think in body regions.

**Changes:**
- Add `region` field to `Tissue` model (e.g., "shoulders", "upper_back", "lower_back", "chest", "hips", "knees", "quads", "hamstrings", "calves", "arms", "core", "neck").
- New model `RecoveryCheckIn` — a region-level daily check-in:
  - `date`, `region`, `soreness_0_10`, `pain_0_10`, `stiffness_0_10`, `readiness_0_10`, `notes`
- The model distributes region-level signals to constituent tissues weighted by recent exercise exposure (tissues that were actually trained get more of the soreness signal).
- Keep `TissueRecoveryLog` for direct tissue-level entries (LLM/advanced use).

**Files:** `backend/app/models.py`, `backend/app/routers/training_model.py`

### 4b. Wire recovery check-ins into `_learn_recovery_days()`

**Problem:** Recovery is currently inferred only from volume drop → rebound. Subjective data is ignored.

**Changes:**
- After a training day, look for check-in data in the following days.
- Track soreness onset (typically peaks day +1 or +2) and decay curve (returns to ≤2/10).
- Days from load event to soreness ≤ 2 = observed recovery duration.
- Blend observed subjective recovery with the existing volume-rebound estimate:
  - If subjective data exists: `recovery_tau = 0.6 × subjective + 0.4 × volume_rebound`
  - If no subjective data: fall back to volume-rebound only (current behavior)

**Files:** `backend/app/training_model.py`

### 4c. Use soreness/pain as risk features

**Changes:**
- Add `current_soreness` and `current_pain` to `_score_risk()` feature set.
- Pain > 4 without an active tissue condition auto-suggests creating one (surfaced in API response, not auto-created).
- Soreness that doesn't decay on the expected curve (still elevated at day +4 when expected recovery is 2 days) is an early warning signal.

**Files:** `backend/app/training_model.py`

## Phase 5: Expose Model Diagnostics

### 5a. Collapse and overload window API

**Problem:** Collapse dates and overload windows are computed internally but not visible for validation.

**Changes:**
- Add `collapse_dates` and `overload_windows` (dates where `ramp_ratio > 1.3`) to the tissue history endpoint response.
- Add `learned_recovery_days` and `baseline_capacity` to tissue history response.

**Files:** `backend/app/routers/training_model.py`, `backend/app/training_model.py`

### 5b. Capacity trend in tissue summary

**Changes:**
- Include `capacity_trend` (% change over last 30 days) in the per-tissue summary.
- Include `capacity_history` (daily capacity_state values) in tissue history endpoint.
- This directly answers "how much stronger am I getting?"

**Files:** `backend/app/training_model.py`

### 5c. Expose e1RM history per exercise

**Changes:**
- Already covered in Phase 2b. Include in exercise risk ranking too for quick access.

## Phase 6: Workout Planner

### 6a. Data model for training programs

**Changes:**
- New model `TrainingProgram`:
  - `id`, `name`, `active`, `created_at`
  - Represents a block/phase (e.g., "Strength Block", "Hypertrophy Block", "Deload Week")
- New model `ProgramDay`:
  - `id`, `program_id`, `day_label` (e.g., "A", "B", "Push", "Pull", "Upper", "Lower")
  - `target_tissues` — JSON list of tissue regions this day targets
  - `sort_order`
- Modify `RoutineExercise` to add optional `program_day_id` FK
  - Exercises can belong to a program day or be standalone (backward compatible)
- New model `PlannedSession`:
  - `id`, `program_day_id`, `date`, `status` (planned/completed/skipped)
  - Links a program day template to a specific calendar date
  - After completion, links to the actual `WorkoutSession`

**Files:** `backend/app/models.py`, `backend/app/routers/planner.py`

### 6b. Planner algorithm: what to train today

The planner selects which program day to run based on:

1. **Recovery readiness:** For each program day, compute aggregate tissue readiness across its target tissues. Skip days whose primary tissues haven't recovered (recovery_state < 0.6 or active condition blocks it).
2. **Time since last hit:** Prefer program days whose target tissues haven't been trained longest (frequency balancing).
3. **Active conditions:** Exclude or modify days that heavily load injured/tender tissues.
4. **User override:** The user can always pick a specific day; the planner just reorders the default suggestion.

Output: ranked list of program days for today, with the top pick as default.

**Files:** `backend/app/planner.py`, `backend/app/routers/planner.py`

### 6c. Planner algorithm: rep scheme selection

For each exercise in today's session, the planner picks a rep scheme:

1. **Heavy day** (3-5 reps @ 80-85% e1RM):
   - Tissue recovery_state >= 0.8
   - At least 5 days since last heavy work on primary tissues
   - e1RM trend is stable or rising
2. **Volume day** (8-12 reps @ 65-75% e1RM):
   - Tissue recovery_state >= 0.6
   - Default when not heavy or light
3. **Light/pump day** (15-20 reps @ 50-60% e1RM):
   - Tissue recovery_state < 0.6 OR tissue is tender/rehabbing
   - Recent soreness check-in elevated
4. **Progressive overload targets:**
   - If last session was completed successfully at the same rep scheme, suggest +2.5-5 lbs (barbell) or +1 rep
   - If last session had failures, suggest same weight, aim for full completion

Output per exercise: `{ sets, reps, target_weight, rep_scheme, rationale }`

**Files:** `backend/app/planner.py`

### 6d. Planner API

**Endpoints:**
- `GET /api/planner/today` — returns recommended program day with exercise prescriptions
- `GET /api/planner/week` — returns next 7 days of planned sessions
- `POST /api/planner/programs` — create/update training programs
- `POST /api/planner/accept` — accept today's plan, creating a WorkoutSession with pre-filled targets

**Files:** `backend/app/routers/planner.py`

### 6e. LLM tool for planner

**Changes:**
- New `get_workout_plan` tool that returns today's recommended session with exercise prescriptions, rationale, and alternatives.
- Allows the LLM to discuss and modify the plan conversationally before the user commits.

**Files:** `backend/app/llm_tools/workout.py`

## Implementation Order

The phases have dependencies but within each phase, work is independent:

```
Phase 1 (data fixes) ──────────────────┐
    1a: bodyweight fix                  │  No dependencies, immediate win
    1b: stiffness field                 │
                                        ▼
Phase 2 (e1RM) ─────────────────────────┐
    2a: compute e1RM                    │  Depends on: Phase 1 (correct loads)
    2b: expose via API                  │
                                        ▼
Phase 3 (load channels) ───────────────┐
    3a: split into channels             │  Depends on: Phase 1
    3b: use in capacity/recovery        │  Depends on: Phase 4 for full value
                                        ▼
Phase 4 (recovery learning) ───────────┐
    4a: region grouping + check-in      │  Independent
    4b: wire into recovery learning     │  Depends on: 4a
    4c: soreness as risk feature        │  Depends on: 4a
                                        ▼
Phase 5 (diagnostics) ─────────────────┐
    5a: collapse/overload windows       │  Independent (expose existing data)
    5b: capacity trend                  │  Independent
                                        ▼
Phase 6 (planner) ─────────────────────┐
    6a: program data model              │  Independent
    6b: day selection algorithm         │  Depends on: 6a, Phase 4, Phase 5
    6c: rep scheme selection            │  Depends on: 6a, Phase 2, Phase 3
    6d: planner API                     │  Depends on: 6b, 6c
    6e: LLM tool                        │  Depends on: 6d
```

## Implementation Status

All phases are implemented:

- **Phase 1** (data fixes): DONE — bodyweight load_input_mode + bodyweight_fraction in LLM tool, stiffness field added
- **Phase 2** (e1RM): DONE — Epley formula, per-exercise time series, trend detection, API endpoint, included in exercise ranking
- **Phase 3** (stimulus channels): DONE — strength/hypertrophy/endurance load channels in ExposureRecord
- **Phase 4** (recovery): DONE — region grouping on Tissue, RecoveryCheckIn model, check-in API, subjective recovery learning in _learn_recovery_days, soreness/pain as risk features in _score_risk
- **Phase 5** (diagnostics): DONE — collapse dates, overload windows, baseline capacity, capacity_trend_30d_pct exposed in API
- **Phase 6** (planner): DONE — TrainingProgram/ProgramDay/ProgramDayExercise/PlannedSession models, planner algorithm (day selection + rep scheme), planner API, frontend TrainingPage

### New files
- `backend/app/planner.py` — planner algorithm
- `backend/app/routers/planner.py` — planner API endpoints
- `frontend/src/pages/TrainingPage.tsx` — training dashboard with progressive loading

### New API endpoints
- `GET /api/training-model/exercises/{id}/strength` — e1RM time series
- `POST /api/training-model/check-in` — recovery check-in
- `GET /api/training-model/check-ins` — list check-ins
- `GET /api/training-model/regions` — body regions with tissues
- `GET /api/planner/today` — today's workout suggestion
- `GET /api/planner/week` — week plan
- `GET /api/planner/programs` — list programs
- `POST /api/planner/programs` — create program
- `PUT /api/planner/programs/{id}` — update program
- `POST /api/planner/accept` — accept plan
- `GET /api/planner/history` — planned session history
