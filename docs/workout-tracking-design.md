# Workout Tracking Design Doc

## Overview

Add strength training tracking to the diet tracker app. Users log workouts via the
existing LLM chat interface. The system tracks exercises, tissues (muscles, tendons,
connective tissue), sets/reps/weight, and recommends which tissues are ready to train
based on recovery time and injury status.

### Design Goals

1. **Complete tissue model** — every muscle, tendon, and joint in the body, seeded at startup
2. **Per-tissue loading factors** — quantify how much each exercise stresses each tissue
3. **Recovery-based scheduling** — no fixed splits; suggest what to train based on readiness
4. **Injury lifecycle management** — detect, track, rehab, and recover from tissue issues
5. **Rep completion feedback** — drive progressive overload from structured performance data
6. **Historical import** — load all past workout spreadsheets via LLM chat
7. **Volume analysis** — sets × reps × loading_factor per tissue over time
8. **LLM-first management** — the chat can fully manage every aspect of the database

## Database Schema

### New Tables

```sql
-- Canonical exercise definitions
CREATE TABLE exercise (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,       -- "Incline DB Press"
    equipment       TEXT,                       -- "dumbbell", "cable", "barbell", "machine", "bodyweight", "kettlebell"
    notes           TEXT,                       -- form cues, etc.
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Muscles, tendons, joints, and tissue groups — hierarchical, DB-driven
-- LOG TABLE: rows are never updated, only appended. To change recovery_hours or
-- notes, insert a new row with the same name. Queries use the row with the latest
-- updated_at for each name. Preserves full history of recovery setting changes.
CREATE TABLE tissue (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,              -- "pectoralis_major", "supraspinatus", "achilles_tendon"
    display_name    TEXT NOT NULL,              -- "Pec Major", "Supraspinatus", "Achilles Tendon"
    type            TEXT NOT NULL DEFAULT 'muscle',  -- "muscle", "tendon", "joint", "tissue_group"
    parent_id       INTEGER REFERENCES tissue(id),   -- e.g. "posterior_deltoid" → parent "deltoid"
    recovery_hours  REAL NOT NULL DEFAULT 48,  -- base recovery time for this tissue
    notes           TEXT,                       -- special considerations
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which tissues an exercise loads (many-to-many)
-- LOG TABLE: append-only. To change loading_factor or role, insert a new row.
-- Queries use the latest updated_at per (exercise_id, tissue_id).
CREATE TABLE exercise_tissue (
    id              INTEGER PRIMARY KEY,
    exercise_id     INTEGER NOT NULL REFERENCES exercise(id) ON DELETE CASCADE,
    tissue_id       INTEGER NOT NULL REFERENCES tissue(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'primary',  -- "primary", "secondary", "stabilizer"
    loading_factor  REAL NOT NULL DEFAULT 1.0, -- 0.0-1.0: how much relative load on this tissue
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A workout session (one per gym visit)
CREATE TABLE workout_session (
    id              INTEGER PRIMARY KEY,
    date            TEXT NOT NULL,              -- YYYY-MM-DD
    started_at      TEXT,                       -- ISO timestamp (optional)
    finished_at     TEXT,                       -- ISO timestamp (optional)
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Individual sets performed
CREATE TABLE workout_set (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES workout_session(id) ON DELETE CASCADE,
    exercise_id     INTEGER NOT NULL REFERENCES exercise(id),
    set_order       INTEGER NOT NULL,           -- 1, 2, 3... within this exercise in this session
    reps            INTEGER,                    -- null for timed sets
    weight          REAL,                       -- in lbs, null for bodyweight
    duration_secs   INTEGER,                    -- for timed exercises (planks, carries, flutter kicks)
    distance_steps  INTEGER,                    -- for farmer's carry, walking lunges
    rpe             REAL,                       -- rate of perceived exertion 1-10 (optional)
    rep_completion  TEXT,                       -- "full", "partial", "failed" (see Rep Completion)
    notes           TEXT,                       -- "slow eccentric", "pause at top", etc.
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Routine template: defines target exercises and rep ranges
CREATE TABLE routine_exercise (
    id              INTEGER PRIMARY KEY,
    exercise_id     INTEGER NOT NULL REFERENCES exercise(id) ON DELETE CASCADE,
    target_sets     INTEGER NOT NULL,           -- e.g. 3
    target_rep_min  INTEGER,                    -- e.g. 10 (for "3x10-12")
    target_rep_max  INTEGER,                    -- e.g. 12
    sort_order      INTEGER NOT NULL DEFAULT 0, -- display ordering
    active          INTEGER NOT NULL DEFAULT 1, -- 0 = temporarily disabled
    notes           TEXT,                       -- "heavy day", "light/moderate"
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tissue condition log — append-only
-- Tracks injury events, recovery arcs, and rehab status over time.
-- Query latest per tissue_id for current state.
CREATE TABLE tissue_condition (
    id              INTEGER PRIMARY KEY,
    tissue_id       INTEGER NOT NULL REFERENCES tissue(id),
    status          TEXT NOT NULL,              -- see Injury State Machine below
    severity        INTEGER NOT NULL DEFAULT 0, -- 0-4
    max_loading_factor REAL,                   -- cap on exercises loading this tissue
                                                -- null = no restriction
    recovery_hours_override REAL,              -- override tissue's base recovery_hours
                                                -- null = use tissue default
    rehab_protocol  TEXT,                      -- "band external rotations 3x15 daily"
    notes           TEXT,                       -- "felt twinge during overhead press set 3"
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Log Table Pattern

`tissue`, `exercise_tissue`, and `tissue_condition` are append-only log tables. Rows are
never updated or deleted — a new row is inserted with the current timestamp. Queries select
the latest row per logical key:

```sql
-- Current tissue definitions (latest per name)
SELECT * FROM tissue t1
WHERE t1.updated_at = (
    SELECT MAX(t2.updated_at) FROM tissue t2 WHERE t2.name = t1.name
);

-- Current exercise-tissue mappings (latest per exercise+tissue pair)
SELECT * FROM exercise_tissue et1
WHERE et1.updated_at = (
    SELECT MAX(et2.updated_at) FROM exercise_tissue et2
    WHERE et2.exercise_id = et1.exercise_id AND et2.tissue_id = et1.tissue_id
);

-- Current tissue condition (latest per tissue)
SELECT * FROM tissue_condition tc1
WHERE tc1.updated_at = (
    SELECT MAX(tc2.updated_at) FROM tissue_condition tc2
    WHERE tc2.tissue_id = tc1.tissue_id
);
```

**Why log tables:**
- **Injury tracking**: recovery_hours changed from 72h → 168h → 72h over time, all preserved
- **Loading factor evolution**: refine understanding of exercise biomechanics over time
- **Volume analysis**: historical queries use the loading factors that were active at the
  time of each workout (by comparing workout date to mapping updated_at)
- **No data loss**: corrections are just new rows; old data stays for audit

### Rep Completion & Progression Widget

Each exercise in the routine has a target rep range (e.g., 3×8–12). After logging a
workout, the chat presents a widget per exercise to classify performance and record
actual reps completed:

```
How did you do on Incline DB Press (50 lbs, target 3×8-12)?

 ● Hit top of range (3×12)        → ready to increase weight next session
 ○ Within range (≥8, <12 reps)    → stay at this weight
 ○ Below range (<8 on any set)    → hold or decrease weight

 Reps per set: [12] [12] [11]     → total reps recorded for volume calc
```

**`rep_completion` values:**
- `"full"` — completed all sets at the top of the rep range. Progression trigger.
- `"partial"` — within range but not at top. Hold weight.
- `"failed"` — at least one set below the bottom of the range. Hold or deload.

**Actual reps are always recorded** per set in the `reps` field of `workout_set`. The
`rep_completion` is a classification that drives progression logic, but volume calculations
use the actual reps: **volume per tissue = Σ(sets × actual_reps × weight × loading_factor)**.

**Chat widget implementation:**

After the LLM logs a workout via `log_workout`, the response includes:

```xml
<REP_CHECK exercises='[
  {"exercise_name": "Incline DB Press", "weight": 50, "target_sets": 3,
   "target_rep_min": 8, "target_rep_max": 12}
]'/>
```

The frontend renders radio-button cards + rep input fields. On submission, it sends:
"Rep check: Incline DB Press = full, reps 12/12/11; Cable Fly = partial, reps 14/12/10"
The LLM calls `update_rep_completion` to patch workout_set records and gives progression
advice (e.g., "You've hit 3×12 at 50 lbs two sessions in a row — try 55 next time").

### Volume Accounting

Volume per tissue is calculated as:

```
tissue_volume = Σ (reps × weight × loading_factor)
               for each set where the exercise loads that tissue
```

This is more accurate than sets × loading_factor because it accounts for:
- Partial sets (failed to hit rep range)
- Variable reps across sets (12, 10, 8)
- Weight differences (heavy day vs moderate day)

Weekly volume per tissue is the primary metric for tracking training stimulus and
detecting overtraining or undertraining.

### Tissue Hierarchy (Seed Data)

The `tissue` table is seeded with the **complete human musculoskeletal system** at startup —
every muscle, major tendon, and joint relevant to strength training, organized hierarchically.

**Why start comprehensive:**
- Every exercise loads dozens of tissues (stabilizers, grip, core, etc.)
- Starting with the full map means the LLM can assign accurate loading factors from day one
- No need for retroactive updates when you "discover" a tissue later
- Future: linear regression on performance data can auto-tune loading factors per user

**Seed categories:**
- **Muscles:** ~200+ muscles covering every region
- **Tendons:** Major tendons relevant to lifting injuries (achilles, patellar, supraspinatus,
  biceps long head, common extensor, rotator cuff tendons, etc.)
- **Tissue groups:** Hierarchical groupings (e.g., "upper_body" → "chest" → "pectoralis_major"
  → "pec_clavicular_head")
- **Joints:** Major joints as reference points for injury tracking
  (shoulder, elbow, wrist, hip, knee, ankle)

```
tissue_group: chest (48h)
├── muscle: pectoralis_major (48h)
│   ├── muscle: pec_clavicular_head (48h)
│   └── muscle: pec_sternal_head (48h)
└── muscle: pectoralis_minor (48h)

tissue_group: upper_back (48h)
├── muscle: latissimus_dorsi (48h)
├── muscle: rhomboids (48h)
├── muscle: trapezius (48h)
│   ├── muscle: upper_trapezius (48h)
│   ├── muscle: mid_trapezius (48h)
│   └── muscle: lower_trapezius (48h)
└── muscle: teres_major (48h)

tissue_group: lower_back (72h)
└── muscle: erector_spinae (72h)

tissue_group: shoulders (48h)
├── muscle: deltoid (48h)
│   ├── muscle: anterior_deltoid (48h)
│   ├── muscle: lateral_deltoid (48h)
│   └── muscle: posterior_deltoid (48h)
└── tissue_group: rotator_cuff (72h)
    ├── tendon: supraspinatus (72h)
    ├── muscle: infraspinatus (72h)
    ├── muscle: teres_minor (72h)
    └── muscle: subscapularis (72h)

tissue_group: biceps (48h)
├── muscle: biceps_brachii (48h)
│   ├── muscle: biceps_long_head (48h)
│   └── muscle: biceps_short_head (48h)
└── muscle: brachialis (48h)

tissue_group: triceps (48h)
├── muscle: triceps_long_head (48h)
├── muscle: triceps_lateral_head (48h)
└── muscle: triceps_medial_head (48h)

tissue_group: forearms (48h)
├── muscle: brachioradialis (72h)
├── muscle: wrist_flexors (48h)
└── muscle: wrist_extensors (48h)

tissue_group: quads (72h)
├── muscle: rectus_femoris (72h)
├── muscle: vastus_lateralis (72h)
├── muscle: vastus_medialis (72h)
└── muscle: vastus_intermedius (72h)

tissue_group: hamstrings (72h)
├── muscle: biceps_femoris (72h)
├── muscle: semitendinosus (72h)
└── muscle: semimembranosus (72h)

tissue_group: glutes (72h)
├── muscle: gluteus_maximus (72h)
├── muscle: gluteus_medius (72h)
└── muscle: gluteus_minimus (72h)

tissue_group: calves (48h)
├── muscle: gastrocnemius (48h)
├── muscle: soleus (48h)
└── tendon: achilles_tendon (72h)

tissue_group: hip_adductors (48h)
├── muscle: adductor_magnus (48h)
├── muscle: adductor_longus (48h)
└── muscle: gracilis (48h)

tissue_group: hip_abductors (48h)
├── muscle: tensor_fasciae_latae (48h)
└── (gluteus_medius/minimus shared with glutes)

tissue_group: abs (24h)
├── muscle: rectus_abdominis (24h)
├── muscle: obliques (24h)
│   ├── muscle: internal_oblique (24h)
│   └── muscle: external_oblique (24h)
└── muscle: transverse_abdominis (24h)

tissue_group: hip_flexors (48h)
├── muscle: iliopsoas (48h)
└── (rectus_femoris shared with quads)

muscle: tibialis_anterior (48h)

-- Tendons (parented under relevant tissue groups)
tendon: patellar_tendon (72h)          → parent: quads
tendon: achilles_tendon (72h)          → parent: calves
tendon: supraspinatus_tendon (72h)     → parent: rotator_cuff
tendon: biceps_long_head_tendon (72h)  → parent: biceps
tendon: common_extensor_tendon (72h)   → parent: forearms

-- Joints (for injury tracking reference)
joint: shoulder_joint (72h)
joint: elbow_joint (72h)
joint: wrist_joint (48h)
joint: hip_joint (72h)
joint: knee_joint (72h)
joint: ankle_joint (72h)
```

The full seed script includes ~200+ tissues. The above is a representative subset.

**Loading factor examples:**
| Exercise | Tissue | Role | Loading Factor |
|---|---|---|---|
| Bench Press | pectoralis_major | primary | 1.0 |
| Bench Press | anterior_deltoid | secondary | 0.5 |
| Bench Press | triceps | secondary | 0.4 |
| Bench Press | shoulder_joint | stabilizer | 0.2 |
| Barbell Row | latissimus_dorsi | primary | 1.0 |
| Barbell Row | biceps_brachii | secondary | 0.3 |
| Barbell Row | brachioradialis | secondary | 0.2 |
| Rear Delt Fly | posterior_deltoid | primary | 1.0 |
| Rear Delt Fly | supraspinatus | stabilizer | 0.3 |
| Leg Press | quads | primary | 1.0 |
| Leg Press | achilles_tendon | stabilizer | 0.1 |
| Leg Press | knee_joint | stabilizer | 0.3 |

**Loading factor refinement roadmap:**
1. **Phase 1 (now):** LLM assigns loading factors from biomechanics knowledge
2. **Phase 2:** Correlate rep_completion + weight progression with per-tissue volume
   to detect which tissues are rate-limiting
3. **Phase 3:** Linear regression — if OHP stalls but bench doesn't, and the differentiating
   tissue is lateral_deltoid, its loading factor for OHP is probably underestimated
4. **Phase 4:** Per-user personalization from months of data

### Injury & Rehab State Machine

The `tissue_condition` table tracks injury lifecycle. Each transition appends a new row.

```
                    ┌─────────────────────────────────────────┐
                    │                                         │
                    ▼                                         │
              ┌──────────┐    user reports pain         ┌────┴─────┐
              │          │ ─────────────────────────────►│          │
              │ HEALTHY  │                               │  TENDER  │
              │          │◄──────────────────────────────│          │
              └──────────┘    resolved, no symptoms      └────┬─────┘
                    ▲                                         │
                    │                                    gets worse
                    │                                         │
                    │                                         ▼
              ┌─────┴────┐    improving, light load     ┌──────────┐
              │          │◄─────────────────────────────│          │
              │REHABBING │                               │ INJURED  │
              │          │─────────────────────────────►│          │
              └──────────┘    setback during rehab      └──────────┘
                    │
                    │ fully recovered
                    ▼
              ┌──────────┐
              │ HEALTHY  │
              └──────────┘
```

**State definitions and loading behavior:**

| Status | Severity | max_loading_factor | recovery_hours_override | Behavior |
|---|---|---|---|---|
| `healthy` | 0 | null (no cap) | null (use default) | Normal training, full load |
| `tender` | 1-2 | 0.2–0.5 | 1.5× default | Reduce load, avoid heavy compounds, monitor |
| `injured` | 3-4 | 0.0 | ∞ (very high) | **Zero load**. No exercises that touch this tissue. Rest only. |
| `rehabbing` | 1-2 | 0.1–0.3 | 1.2× default, decreasing | Low load, high rep rehab. Gradually reintroduce. |

**Rehab progression (typical for tendon like supraspinatus):**

```
Day 0:  injured, severity=4, max_loading=0.0, recovery_override=336 (2 weeks)
        → all exercises loading supraspinatus excluded
        → "Rest completely, ice, see a doctor if it doesn't improve"

Day 14: rehabbing, severity=3, max_loading=0.1, recovery_override=168
        → only exercises with supraspinatus loading ≤0.1 allowed (very light)
        → rehab: "band external rotations 3×15 daily, zero weight"

Day 28: rehabbing, severity=2, max_loading=0.2, recovery_override=96
        → light rows OK (supraspinatus loading=0.1), still no pressing
        → rehab: "light cable external rotations 3×15, 5 lbs"

Day 42: rehabbing, severity=1, max_loading=0.4, recovery_override=72
        → moderate pressing OK, avoid very heavy overhead work
        → rehab: "banded Y-raises, light face pulls"

Day 56: healthy, severity=0, max_loading=null, recovery_override=null
        → full training resumes, maintain prehab work
```

**Key principles:**
- `max_loading_factor` starts at 0 for acute injury and **gradually increases** during rehab
- `recovery_hours_override` starts very high and **gradually decreases** during rehab
- Exercises not touching the injured tissue are **completely unaffected** — full intensity
- The LLM proactively checks in on injured tissues and suggests state transitions
- All transitions are logged, creating a complete injury recovery timeline

### Recovery Propagation

When checking readiness, the query walks up the parent chain:
- If "supraspinatus" was trained, "rotator_cuff" and "shoulders" are also considered loaded
- If "supraspinatus" has a condition, any exercise loading it (even as stabilizer) is filtered
- Parent groups show the worst-case child status

## API Endpoints

### Exercises

```
GET    /api/exercises              -- list all (with tissue mappings)
GET    /api/exercises/{id}         -- single exercise detail
POST   /api/exercises              -- create exercise + tissue mappings
PUT    /api/exercises/{id}         -- update exercise + tissue mappings
DELETE /api/exercises/{id}         -- delete exercise
GET    /api/exercises/{id}/history -- sets grouped by session date, max weight, volume trends
```

### Workout Sessions

```
GET    /api/workout-sessions                    -- list sessions (?start_date=&end_date=)
GET    /api/workout-sessions/{id}               -- session detail with all sets
POST   /api/workout-sessions                    -- create session with sets
PUT    /api/workout-sessions/{id}               -- update session/sets
DELETE /api/workout-sessions/{id}               -- delete session
```

### Tissues

```
GET    /api/tissues                             -- list all (?tree=true for hierarchy)
GET    /api/tissues/{id}                        -- single tissue with children
POST   /api/tissues                             -- create tissue
PUT    /api/tissues/{id}                        -- update (appends log entry)
```

### Tissue Readiness

```
GET    /api/tissue-readiness                    -- per-tissue readiness:
                                                --   tissue (id, name, display_name, type)
                                                --   condition (current status, severity)
                                                --   last_trained (datetime)
                                                --   hours_since
                                                --   effective_recovery_hours
                                                --   recovery_pct (0-100)
                                                --   ready (bool)
                                                --   exercises_available (from routine)
```

### Routine

```
GET    /api/routine                             -- list all routine exercises
POST   /api/routine                             -- add exercise to routine
PUT    /api/routine/{id}                        -- update routine exercise
DELETE /api/routine/{id}                        -- remove from routine
```

### Tissue Conditions

```
GET    /api/tissue-conditions                   -- current conditions (latest per tissue)
GET    /api/tissue-conditions/{tissue_id}/history -- full condition history for a tissue
POST   /api/tissue-conditions                   -- log new condition entry
```

## LLM Chat Integration

### Chat Tools

All tools are added to the existing `/api/meals/chat` tool system. The LLM must be able
to fully manage every aspect of the workout database.

```python
# ── Workout Logging ──

# 1. Log a workout session
"log_workout": {
    "description": "Log a workout session with exercises, sets, reps, and weights. Creates exercises if they don't exist. Returns session_id for rep completion follow-up.",
    "parameters": {
        "date": "YYYY-MM-DD",
        "exercises": [
            {
                "exercise_name": str,        # fuzzy-matched to DB; created if no match
                "sets": [
                    {
                        "reps": int | null,      # null for timed sets
                        "weight": float | null,  # lbs, null for bodyweight
                        "duration_secs": int | null,
                        "distance_steps": int | null,
                        "rpe": float | null,
                        "notes": str | null
                    }
                ]
            }
        ],
        "notes": str | null
    }
}

# 2. Update rep completion after user feedback from widget
"update_rep_completion": {
    "description": "Set rep_completion and actual reps for exercises in a session",
    "parameters": {
        "session_id": int,
        "completions": [
            {
                "exercise_name": str,
                "rep_completion": "full" | "partial" | "failed",
                "reps_per_set": [int]        # actual reps per set for volume calc
            }
        ]
    }
}

# 3. Query workout history
"query_workout_history": {
    "description": "Query past workout sessions with optional filters",
    "parameters": {
        "start_date": str | null,
        "end_date": str | null,
        "exercise_name": str | null,
        "limit": int                 # default 20
    }
}

# 4. Edit or delete a workout session
"edit_workout_session": {
    "description": "Edit or delete a workout session or individual sets",
    "parameters": {
        "session_id": int,
        "action": "update" | "delete",
        "date": str | null,
        "notes": str | null,
        "add_sets": [...] | null,
        "remove_set_ids": [int] | null
    }
}

# ── Exercise Management ──

# 5. Manage exercise definitions
"manage_exercise": {
    "description": "Create, update, merge, delete, or list exercises. Handles name normalization and deduplication.",
    "parameters": {
        "action": "create" | "update" | "merge" | "delete" | "list",
        "name": str,
        "new_name": str | null,
        "equipment": str | null,
        "notes": str | null,
        "tissues": [
            {"name": str, "role": "primary"|"secondary"|"stabilizer", "loading_factor": float}
        ] | null,
        "merge_into": str | null     # for merge: target exercise (moves all history)
    }
}

# 6. Bulk set exercise-tissue mappings
"bulk_set_exercise_tissues": {
    "description": "Set tissue mappings for multiple exercises at once",
    "parameters": {
        "mappings": [
            {
                "exercise_name": str,
                "tissues": [
                    {"name": str, "role": str, "loading_factor": float}
                ]
            }
        ]
    }
}

# ── Tissue Management ──

# 7. Manage tissue hierarchy
"manage_tissue": {
    "description": "Create, update, or list tissues in the hierarchy",
    "parameters": {
        "action": "create" | "update" | "list" | "tree",
        "name": str | null,
        "display_name": str | null,
        "type": "muscle" | "tendon" | "joint" | "tissue_group" | null,
        "parent_name": str | null,
        "recovery_hours": float | null,
        "notes": str | null
    }
}

# 8. Update tissue recovery settings (appends to log)
"update_tissue_recovery": {
    "description": "Update recovery time or notes for a tissue",
    "parameters": {
        "tissue_name": str,
        "recovery_hours": float | null,
        "notes": str | null
    }
}

# ── Injury & Condition Management ──

# 9. Log tissue condition (injury/tenderness)
"log_tissue_condition": {
    "description": "Record current condition of a tissue. Drives the injury state machine.",
    "parameters": {
        "tissue_name": str,
        "status": "healthy" | "tender" | "injured" | "rehabbing",
        "severity": int,             # 0-4
        "max_loading_factor": float | null,
        "recovery_hours_override": float | null,
        "rehab_protocol": str | null,
        "notes": str | null
    }
}

# 10. Query tissue condition history
"query_tissue_condition": {
    "description": "Get condition history for a tissue — review full injury arc",
    "parameters": {
        "tissue_name": str,
        "limit": int                 # default 10
    }
}

# ── Readiness & Suggestions ──

# 11. Check tissue readiness
"check_tissue_readiness": {
    "description": "Check which tissues are recovered and ready to train. Includes condition status.",
    "parameters": {}
}

# 12. Suggest today's workout
"suggest_workout": {
    "description": "Suggest exercises from routine based on tissue readiness, conditions, and rep completion history. Includes rehab work for tissues in recovery.",
    "parameters": {}
}

# ── Routine Management ──

# 13. Manage routine
"manage_routine": {
    "description": "Add, update, remove, reorder, or list exercises in the training routine",
    "parameters": {
        "action": "add" | "update" | "remove" | "list" | "reorder",
        "exercise_name": str | null,
        "target_sets": int | null,
        "target_rep_min": int | null,
        "target_rep_max": int | null,
        "active": bool | null,
        "notes": str | null,
        "sort_order": int | null
    }
}

# ── Analysis ──

# 14. Query exercise history with PRs and trends
"query_exercise_history": {
    "description": "Get performance history — max weight, volume trends, rep completion streaks",
    "parameters": {
        "exercise_name": str,
        "limit": int                 # default 10 sessions
    }
}

# 15. Analyze tissue volume
"analyze_tissue_volume": {
    "description": "Analyze weekly training volume (sets × reps × weight × loading_factor) per tissue over time",
    "parameters": {
        "tissue_name": str | null,   # null = all tissues
        "start_date": str | null,
        "end_date": str | null,
        "group_by": "week" | "month"
    }
}

# 16. Suggest progressive overload
"suggest_progression": {
    "description": "Suggest weight/rep progression based on rep_completion history and tissue conditions",
    "parameters": {
        "exercise_name": str
    }
}
```

### System Prompt Addition

```
You can also help with workout tracking. The user has a strength training routine.

KNOWN EXERCISES: {exercise_list}
CURRENT ROUTINE: {routine_summary}
CURRENT TISSUE CONDITIONS: {tissue_conditions}

WORKOUT LOGGING:
- When the user describes a workout, use log_workout to record it.
- Parse natural language: "incline DB press 3x10 at 45", "leg press 430x5x3", etc.
- After logging, emit a <REP_CHECK> tag so the frontend shows the rep completion widget.
- When the user responds with rep completion data, use update_rep_completion.
- Always record actual reps per set for accurate volume calculations.

READINESS & SUGGESTIONS:
- When asked what to train, use check_tissue_readiness and suggest_workout.
- Show which exercises are available and which are excluded (and why).
- Include rehab work for any tissues in tender/rehabbing status.
- Never suggest exercises that exceed a tissue's max_loading_factor condition cap.

INJURY AWARENESS:
- If the user mentions pain, tenderness, tightness, or discomfort, use log_tissue_condition.
- Ask about severity and when it started.
- Follow the injury state machine: healthy → tender → injured → rehabbing → healthy.
- Suggest appropriate rehab protocols based on tissue type and severity.
- Maintain full training intensity on exercises that don't load affected tissues.
- Periodically ask about injured tissues: "How's your supraspinatus feeling today?"
- When suggesting workouts, note excluded exercises and why.

PROGRESSIVE OVERLOAD:
- Use suggest_progression to recommend weight increases based on rep_completion streaks.
- 2+ consecutive "full" sessions → suggest weight increase.
- "failed" → check tissue conditions, suggest deload or form adjustment.

DATA IMPORT:
- The user may paste spreadsheet data for historical workout import.
- Parse the data, create exercises and sessions, and assign tissue mappings.
- For dates: use explicit dates when available; estimate dates for round-based formats.
- Always do a dry run summary first before committing.
- See the Data Import section for format details.
```

## Data Import

Historical workout data is imported via the LLM chat. The user pastes spreadsheet data
and the LLM parses it using the existing tools (`log_workout`, `manage_exercise`,
`bulk_set_exercise_tissues`). No separate import program is needed — the LLM handles
format detection, date estimation, exercise creation, and tissue mapping assignment.

### Spreadsheet Formats

The user has 4 formats of historical data. The LLM is instructed to handle each:

**Format 1: Dated columns (Aug–Sep 2025)**
```
Exercise    SetsxReps    max    8/24/2025    8/25/2025    ...
Incl-DB Press Heavy    3x4-6    50.00                    35    ...
```
- Date columns are explicit. Non-empty cell = exercise done that day at that weight.
- Parse SetsxReps for set count. "Body weight" → weight=null. "2 sets" → override count.

**Format 2: Rounds with groups (Oct 2025)**
```
Exercise    Sets x Reps    Group    Round 1    Round 2    ...
Incline Dumbbell Press (Heavy)    4x6–8    Group 1    45    50    ...
```
- Groups cycle: G1R1, G2R1, G3R1, G4R1, G1R2, G2R2, ...
- Estimate dates: spread across the month (~5 sessions/week).

**Format 3: Progressive rep range (Dec 2025–Feb 2026)**
```
Exercise    Group    12-15 Reps    11-14 Reps    10-13 Reps    ...
Hammer Curl    0    25    30    30    ...
```
- Each column = one training phase (~1 week). Rep range from column header.
- Groups cycle same as rounds format.

**Format 4: Rounds with rep range (Feb 2026+)**
```
Exercise    Rep Range    Group #    2/16    Week 2
Cable Crunch (Heavy)    8–12    1        70
```
- Mixed date/week headers. "Week N" relative to first explicit date.

### Import Workflow (via chat)

```
User: [pastes spreadsheet data]
       "This is my October 2025 workout data"

LLM:  1. Detects format (rounds with groups)
      2. Parses exercises, weights, sets from the data
      3. Estimates dates (Oct 2025, ~5 sessions/week)
      4. Dry run summary:
         "Found 4 groups, 6 rounds each = 24 sessions
          Oct 1–31, estimated dates: 10/1, 10/2, 10/3, 10/5, ...
          32 exercises (8 new, 24 already in DB)
          New exercises needing tissue mappings:
            - Smith Machine Incline Press
            - Pec Deck Machine
            - ...
          Total sets to import: 384
          Ready to import? Say 'go' to commit."

User: "go"

LLM:  5. Creates exercises via manage_exercise
      6. Assigns tissue mappings via bulk_set_exercise_tissues
      7. Logs sessions via log_workout (one per estimated date)
      8. Returns summary: "Imported 24 sessions, 32 exercises, 384 sets"
```

## Frontend

### New Page: WorkoutPage (`/workout`)

**Sections:**

1. **Tissue Readiness Dashboard** (top)
   - Grid of tissue group cards
   - Color coded: green (ready), yellow (almost ready), red (recovering), purple (injured)
   - Shows hours since last trained, recovery %, condition status
   - Clicking a group expands to show child tissues and their individual statuses

2. **Today's Suggested Workout**
   - Based on ready tissues, pull matching routine exercises
   - Shows target sets × reps and last weight used
   - Rehab section shown separately for tissues in recovery
   - Excluded exercises shown greyed out with reason

3. **Recent Sessions** (scrollable list)
   - Date, exercises performed, total volume
   - Rep completion indicators (green/yellow/red dots)
   - Expandable to show individual sets

4. **Exercise Progress** (charts)
   - Per-exercise max weight over time (line chart)
   - Total volume per session (bar chart)
   - Rep completion trend (streak of full/partial/failed)

### Chat Integration

The existing MealLogPage chat handles both meals and workouts. The `<REP_CHECK>` widget
renders inline in the chat after workout logging. No separate chat page needed.

### Navigation Update

Add "Workout" tab to the Layout component's nav (between Dashboard and Chat).

## Work Packages (Parallelizable)

### Package 1: Backend Models + Seed Data
**Files:** `backend/app/models.py`, `backend/app/seed_tissues.py`, `backend/app/database.py`
- Add SQLModel classes: Tissue, TissueCondition, Exercise, ExerciseTissue, WorkoutSession, WorkoutSet, RoutineExercise
- Comprehensive seed script for full musculoskeletal hierarchy (~200+ tissues)
- Helper functions for log-table queries (current tissue state, current exercise-tissue mappings)
- Add table creation + seed to database.py startup

### Package 2: Backend API Routes
**Files:** `backend/app/routers/exercises.py`, `backend/app/routers/workout_sessions.py`, `backend/app/routers/routine.py`, `backend/app/routers/tissue_readiness.py`, `backend/app/routers/tissues.py`
- CRUD for exercises, sessions, routine, tissues, tissue conditions
- Tissue readiness calculation endpoint (with condition awareness + parent propagation)
- Exercise history endpoint with volume/PR tracking
- Wire routes in main.py

### Package 3: LLM Tools
**Files:** `backend/app/workout_tools.py` (new), `backend/app/llm.py` (extend)
- All 16 tool definitions and handler functions
- System prompt updates with exercise/routine/condition context
- Exercise name fuzzy matching and auto-creation
- Volume analysis and progression suggestion logic
- `<REP_CHECK>` tag generation after log_workout

### Package 4: Frontend Page + API
**Files:** `frontend/src/pages/WorkoutPage.tsx`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`
- WorkoutPage with tissue readiness dashboard, session list, exercise history charts
- Rep completion widget component (radio buttons + rep inputs, rendered from `<REP_CHECK>`)
- API client functions for all new endpoints
- Router + nav updates

### Dependency Order
```
Package 1 (models + seed)
    ├── Package 2 (API routes)        ─┐
    ├── Package 3 (LLM tools)          ├── parallel after Package 1
    └── Package 4 (frontend)          ─┘
```

---

## Agent Prompts

### Package 1: Backend Models + Seed Data

```
TASK: Create the database models and tissue seed data for the workout tracking feature.

CONTEXT: This is a diet tracker app (FastAPI + SQLModel + SQLite). You are adding workout
tracking. Read the existing models in backend/app/models.py and database.py to understand
the patterns used (SQLModel classes, table creation at startup, no Alembic).

CREATE THE FOLLOWING FILES:

1. backend/app/models.py — ADD these SQLModel classes (do not remove existing models):

   - Exercise: id, name (unique), equipment (optional), notes (optional), created_at
   - Tissue: id, name, display_name, type ("muscle"|"tendon"|"joint"|"tissue_group"),
     parent_id (self-ref FK), recovery_hours (default 48), notes, updated_at
     NOTE: This is a LOG TABLE — rows are never updated. Append new rows to change values.
   - ExerciseTissue: id, exercise_id (FK), tissue_id (FK), role ("primary"|"secondary"|"stabilizer"),
     loading_factor (float, default 1.0), updated_at
     NOTE: This is a LOG TABLE.
   - WorkoutSession: id, date, started_at (optional), finished_at (optional), notes, created_at
   - WorkoutSet: id, session_id (FK), exercise_id (FK), set_order, reps (optional),
     weight (optional), duration_secs (optional), distance_steps (optional), rpe (optional),
     rep_completion ("full"|"partial"|"failed", optional), notes, created_at
   - RoutineExercise: id, exercise_id (FK), target_sets, target_rep_min, target_rep_max,
     sort_order (default 0), active (default 1), notes, created_at
   - TissueCondition: id, tissue_id (FK), status ("healthy"|"tender"|"injured"|"rehabbing"),
     severity (int 0-4, default 0), max_loading_factor (optional float),
     recovery_hours_override (optional float), rehab_protocol (optional), notes, updated_at
     NOTE: This is a LOG TABLE.

2. backend/app/seed_tissues.py — Create a comprehensive seed function that populates the
   tissue table with the COMPLETE human musculoskeletal system relevant to strength training.
   This must include:
   - ~200+ tissues organized hierarchically (parent_id references)
   - Every major muscle group, individual muscles, and sub-muscles
   - Major tendons (achilles, patellar, supraspinatus, biceps long head, common extensor, etc.)
   - Major joints (shoulder, elbow, wrist, hip, knee, ankle, spine segments)
   - Appropriate recovery_hours per tissue (24h abs, 48h most muscles, 72h large compounds + tendons)
   - The function should be idempotent (skip if tissues already exist)

   Structure as a nested dict like:
   {
       "upper_body": {"type": "tissue_group", "recovery_hours": 48, "children": {
           "chest": {"type": "tissue_group", "recovery_hours": 48, "children": {
               "pectoralis_major": {"type": "muscle", "recovery_hours": 48, "children": {
                   "pec_clavicular_head": {"type": "muscle", "recovery_hours": 48},
                   "pec_sternal_head": {"type": "muscle", "recovery_hours": 48},
               }},
               ...
           }},
           ...
       }},
       "lower_body": { ... },
       "core": { ... },
   }

   Be comprehensive. Include: pectorals, deltoids (anterior/lateral/posterior), rotator cuff
   (supraspinatus, infraspinatus, teres minor, subscapularis), biceps (long/short head,
   brachialis), triceps (long/lateral/medial head), forearms (brachioradialis, wrist
   flexors/extensors), lats, traps (upper/mid/lower), rhomboids, erector spinae, teres major,
   quads (rectus femoris, vastus lateralis/medialis/intermedius), hamstrings (biceps femoris,
   semitendinosus, semimembranosus), glutes (maximus/medius/minimus), calves (gastrocnemius,
   soleus), hip adductors/abductors, abs (rectus abdominis, obliques internal/external,
   transverse abdominis), hip flexors (iliopsoas), tibialis anterior, serratus anterior,
   levator scapulae, and all major tendons and joints.

3. backend/app/database.py — Add the new tables to create_db_and_tables(). Call the seed
   function after table creation.

4. backend/app/workout_queries.py — Helper functions for log-table queries:
   - get_current_tissues(session) → list of current tissue states (latest per name)
   - get_current_exercise_tissues(session, exercise_id) → current tissue mappings for exercise
   - get_current_tissue_condition(session, tissue_id) → current condition for a tissue
   - get_tissue_tree(session) → hierarchical tree of all tissues

IMPORTANT:
- Follow existing code patterns in models.py (SQLModel, Optional fields, etc.)
- Use the same database engine/session patterns as the existing code
- Do NOT modify any existing models or break existing functionality
- Run: cd backend && python -c "from app.database import create_db_and_tables; create_db_and_tables()"
  to verify tables create successfully
```

### Package 2: Backend API Routes

```
TASK: Create REST API routes for the workout tracking feature.

CONTEXT: This is a FastAPI app. Read backend/app/routers/meals.py and backend/app/routers/foods.py
to understand the existing patterns (router setup, auth dependency, response models, error handling).
Read backend/app/models.py for the new workout models (Exercise, Tissue, ExerciseTissue,
WorkoutSession, WorkoutSet, RoutineExercise, TissueCondition).
Read backend/app/workout_queries.py for log-table query helpers.

CREATE THESE ROUTER FILES:

1. backend/app/routers/exercises.py
   - GET /exercises — list all exercises with current tissue mappings
   - GET /exercises/{id} — single exercise with tissue mappings
   - POST /exercises — create exercise with tissue mappings
   - PUT /exercises/{id} — update exercise (tissue mappings append to log)
   - DELETE /exercises/{id} — delete exercise
   - GET /exercises/{id}/history — all sets for this exercise grouped by session date,
     including max weight, total volume (sets×reps×weight), rep_completion stats

2. backend/app/routers/workout_sessions.py
   - GET /workout-sessions — list sessions (?start_date=&end_date=)
   - GET /workout-sessions/{id} — session detail with all sets and exercise info
   - POST /workout-sessions — create session with sets
   - PUT /workout-sessions/{id} — update session metadata and/or sets
   - DELETE /workout-sessions/{id} — delete session and all sets

3. backend/app/routers/tissues.py
   - GET /tissues — list all current tissues (?tree=true for hierarchy)
   - GET /tissues/{id} — single tissue with children
   - POST /tissues — create new tissue (appends to log)
   - PUT /tissues/{id} — update tissue recovery_hours/notes (appends to log)
   - GET /tissue-conditions — current conditions (latest per tissue, only non-healthy)
   - GET /tissue-conditions/{tissue_id}/history — full condition history
   - POST /tissue-conditions — log new condition entry

4. backend/app/routers/tissue_readiness.py
   - GET /tissue-readiness — for each tissue: last_trained, hours_since,
     effective_recovery_hours, recovery_pct (0-100), ready (bool), condition status,
     exercises_available from routine. Aggregates up parent chain (worst-case child).

5. backend/app/routers/routine.py
   - GET /routine — list all routine exercises with exercise details and last performance
   - POST /routine — add exercise to routine
   - PUT /routine/{id} — update routine exercise
   - DELETE /routine/{id} — remove from routine

THEN: Add all routers to backend/app/main.py (follow existing pattern for router inclusion).

All endpoints require auth (use the get_current_user dependency from backend/app/auth.py).

Volume calculation everywhere is: sets × reps × weight × loading_factor (per tissue).

Run the backend tests after to make sure nothing is broken:
  cd backend && python -m pytest -v
```

### Package 3: LLM Tools

```
TASK: Add workout tracking tools to the LLM chat system.

CONTEXT: Read backend/app/llm.py thoroughly — this is the existing LLM chat with tool use.
Understand how tools are defined (Anthropic tool_use format), how tool calls are handled
(the tool handler dispatch), and how the system prompt is built. Read the existing tools
(query_food_log, create_meal, create_food, log_weight, etc.) to understand the patterns.
Read backend/app/models.py for the new workout models.
Read backend/app/workout_queries.py for log-table query helpers.

CREATE: backend/app/workout_tools.py — All 16 workout tool definitions and handlers.

Tool definitions (Anthropic tool_use JSON schema format):
1.  log_workout — create session + sets, fuzzy-match exercise names to DB
2.  update_rep_completion — patch rep_completion + actual reps on workout_set records
3.  query_workout_history — query sessions with filters
4.  edit_workout_session — update or delete sessions/sets
5.  manage_exercise — create/update/merge/delete/list exercises with tissue mappings
6.  bulk_set_exercise_tissues — set tissue mappings for multiple exercises at once
7.  manage_tissue — create/update/list/tree tissues in the hierarchy
8.  update_tissue_recovery — update recovery_hours for a tissue (append to log)
9.  log_tissue_condition — record condition (follows injury state machine)
10. query_tissue_condition — get condition history
11. check_tissue_readiness — compute readiness for all tissues
12. suggest_workout — suggest exercises based on readiness + conditions + routine
13. manage_routine — add/update/remove/list/reorder routine exercises
14. query_exercise_history — performance history with PRs and volume trends
15. analyze_tissue_volume — weekly/monthly volume per tissue (Σ reps×weight×loading_factor)
16. suggest_progression — recommend weight/rep changes based on rep_completion streaks

Each tool needs:
- A tool definition dict (name, description, input_schema with JSON Schema properties)
- A handler function that receives the tool input dict and a SQLModel Session,
  executes the operation, and returns a result string

MODIFY: backend/app/llm.py
- Import workout tool definitions and handlers from workout_tools.py
- Add workout tools to the tools list passed to the Anthropic API
- Add workout tool handlers to the tool dispatch logic
- Update the system prompt to include:
  - List of known exercises
  - Current routine summary
  - Current tissue conditions (non-healthy only)
  - Instructions for workout logging, injury awareness, progressive overload, and data import
  - The <REP_CHECK> tag format for the frontend widget

IMPORTANT BEHAVIORS:
- log_workout: After logging, generate a <REP_CHECK exercises='[...]'/> tag in the response
  for exercises that have a routine entry with target rep ranges.
- suggest_workout: Check tissue_condition for each tissue. Exclude exercises where any
  loaded tissue has max_loading_factor exceeded. Add rehab exercises for tender/rehabbing tissues.
- suggest_progression: Look at last N sessions of rep_completion. 2+ consecutive "full" →
  suggest weight increase. "failed" → suggest deload. Check tissue conditions too.
- manage_exercise with action="merge": Move all workout_set records from source to target
  exercise, then delete source.
- Exercise name fuzzy matching: use difflib.get_close_matches or similar for matching
  user input to existing exercise names.

Volume formula everywhere: Σ(reps × weight × loading_factor) per tissue.

Run: cd backend && python -m pytest -v
```

### Package 4: Frontend Page + API

```
TASK: Create the workout tracking frontend page and API client functions.

CONTEXT: Read the existing frontend code to understand patterns:
- frontend/src/api.ts — API client with request() helper, TypeScript interfaces
- frontend/src/pages/DashboardPage.tsx — example of a page with charts and data fetching
- frontend/src/pages/MealLogPage.tsx — the chat interface (where <REP_CHECK> will render)
- frontend/src/components/Layout.tsx — navigation layout
- frontend/src/App.tsx — router setup
- The app uses React 19, Tailwind CSS v4, TypeScript, react-router v7

1. frontend/src/api.ts — ADD TypeScript interfaces and API functions:

   Interfaces:
   - Tissue { id, name, display_name, type, parent_id, recovery_hours, notes }
   - TissueCondition { id, tissue_id, status, severity, max_loading_factor,
     recovery_hours_override, rehab_protocol, notes, updated_at }
   - Exercise { id, name, equipment, notes, tissues: ExerciseTissueMapping[] }
   - ExerciseTissueMapping { tissue_id, tissue_name, role, loading_factor }
   - WorkoutSession { id, date, started_at, finished_at, notes, sets: WorkoutSetDetail[] }
   - WorkoutSetDetail { id, exercise_id, exercise_name, set_order, reps, weight,
     duration_secs, distance_steps, rpe, rep_completion, notes }
   - RoutineExercise { id, exercise: Exercise, target_sets, target_rep_min, target_rep_max,
     sort_order, active, notes }
   - TissueReadiness { tissue: Tissue, condition: TissueCondition | null, last_trained,
     hours_since, effective_recovery_hours, recovery_pct, ready, exercises_available }
   - ExerciseHistory { exercise: Exercise, sessions: { date, sets, max_weight, total_volume,
     rep_completion }[] }

   API functions:
   - getExercises(), getExercise(id), createExercise(...), updateExercise(id, ...)
   - getWorkoutSessions(startDate?, endDate?), getWorkoutSession(id),
     createWorkoutSession(...), deleteWorkoutSession(id)
   - getTissues(tree?), getTissueReadiness(), getTissueConditions()
   - getRoutine(), addRoutineExercise(...), updateRoutineExercise(id, ...),
     deleteRoutineExercise(id)
   - getExerciseHistory(id)

2. frontend/src/pages/WorkoutPage.tsx — NEW PAGE with these sections:

   a. Tissue Readiness Dashboard (top)
      - Grid of tissue group cards (show top-level groups: chest, back, shoulders, etc.)
      - Color coded: green (ready, >100% recovered), yellow (75-100%), red (<75%),
        purple (injured/tender condition)
      - Each card shows: group name, recovery %, hours until ready, condition badge
      - Click to expand and see child tissues + which routine exercises target them

   b. Today's Suggested Workout
      - Call getTissueReadiness() and getRoutine()
      - Show exercises from routine where all primary tissues are ready
      - Show target sets × reps and last weight used (from most recent session)
      - Rehab section: exercises for tissues in tender/rehabbing status
      - Greyed-out section: excluded exercises with reason ("supraspinatus recovering")

   c. Recent Sessions (scrollable list)
      - Last 10 sessions with date, exercise count, total volume
      - Rep completion dots per exercise (green/yellow/red)
      - Expandable to show individual sets with weights and reps

   d. Exercise Progress (charts, like DashboardPage SVG charts)
      - Dropdown to select exercise
      - Max weight over time (line chart)
      - Total volume per session (bar chart)

   Style: Match existing pages (Tailwind, same card styles, same color palette).
   Mobile-first with the same responsive patterns as DashboardPage.

3. frontend/src/pages/MealLogPage.tsx — MODIFY to handle <REP_CHECK> tag:
   - Parse <REP_CHECK exercises='[...]'/> from assistant messages
   - Render a RepCompletionCard component inline in the chat:
     - For each exercise: radio buttons (full/partial/failed) + rep count inputs per set
     - Submit button that sends a follow-up chat message with the completion data
   - Style the card similar to ProposedItemsCard / SavedMealCard

4. frontend/src/App.tsx — Add route: /workout → WorkoutPage

5. frontend/src/components/Layout.tsx — Add "Workout" nav tab between Dashboard and Chat.
   Use a dumbbell or flexed bicep icon (or just text).

IMPORTANT:
- Follow existing code patterns exactly (no new dependencies unless essential)
- Use the same SVG chart approach as DashboardPage (no charting library)
- Mobile-first responsive design matching existing pages
- Run: cd frontend && npm run build — must compile without errors
```
