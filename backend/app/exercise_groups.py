"""Cosine-centroid exercise group classifier with freshness-based availability.

Assigns every exercise to one of five training groups (Push, Pull, Legs,
Shoulders, Core) based on the cosine similarity of its region-level
load profile to predefined group centroids.  Groups become available when
all exercises in the group were last trained >= a cooldown threshold ago.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from sqlmodel import Session, func, select

from app.config import user_today
from app.models import Exercise, ExerciseTissue, Tissue, WorkoutSession, WorkoutSet
from app.planner_groups import SIGNIFICANT_GROUP_LOAD, significant_mapping_load
from app.tissue_regions import canonicalize_region

# ---------------------------------------------------------------------------
# Group centroids — each group is an ideal region-weight vector.
# Exercises are classified to whichever centroid they most closely resemble.
#
# Triceps-dominant exercises land in Push (triceps: 0.8).
# Bicep/forearm exercises land in Pull (biceps: 0.7, forearms: 0.5).
# Shrug/carry exercises land in Shoulders (upper_back: 0.5, forearms: 0.3).
# ---------------------------------------------------------------------------

GROUP_CENTROIDS: dict[str, dict[str, float]] = {
    "Push": {"chest": 1.0, "triceps": 0.8, "shoulders": 0.3},
    "Pull": {"upper_back": 0.8, "biceps": 0.7, "lower_back": 0.7, "forearms": 0.5},
    "Legs": {
        "quads": 0.8,
        "hamstrings": 0.8,
        "glutes": 0.8,
        "calves": 0.4,
        "shins": 0.3,
        "inner_leg_adductor": 0.3,
        "outer_leg_abductor": 0.3,
    },
    "Shoulders": {"shoulders": 1.0, "upper_back": 0.7, "forearms": 0.3},
    "Core": {"core": 1.0, "lower_back": 0.3},
}

ALL_GROUPS = list(GROUP_CENTROIDS.keys())

# Pre-compute centroid norms (constant).
_CENTROID_NORMS: dict[str, float] = {
    name: math.sqrt(sum(v * v for v in vec.values()))
    for name, vec in GROUP_CENTROIDS.items()
}

# ---------------------------------------------------------------------------
# Freshness-based availability — groups are available when all exercises
# in the group were last trained >= cooldown_days ago.
# ---------------------------------------------------------------------------

GROUP_COOLDOWN_DAYS: dict[str, int] = {
    "Push": 3,
    "Pull": 3,
    "Legs": 3,
    "Shoulders": 2,
    "Core": 2,
}

MIN_CONFIDENCE = 0.15  # below this, exercise is "Uncategorized"

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse region-weight vectors."""
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def classify_exercise(
    region_loads: dict[str, float],
) -> tuple[str, float]:
    """Classify an exercise into the best-matching group.

    Returns (group_name, confidence).  If confidence is below
    MIN_CONFIDENCE the group is ``"Uncategorized"``.
    """
    if not region_loads:
        return "Uncategorized", 0.0

    best_group = "Uncategorized"
    best_score = 0.0
    norm_a = math.sqrt(sum(v * v for v in region_loads.values()))
    if norm_a == 0.0:
        return "Uncategorized", 0.0

    for group_name, centroid in GROUP_CENTROIDS.items():
        keys = set(region_loads) | set(centroid)
        dot = sum(region_loads.get(k, 0.0) * centroid.get(k, 0.0) for k in keys)
        score = dot / (norm_a * _CENTROID_NORMS[group_name])
        if score > best_score:
            best_score = score
            best_group = group_name

    if best_score < MIN_CONFIDENCE:
        return "Uncategorized", best_score
    return best_group, round(best_score, 3)


def build_exercise_region_profile(
    mappings: list[dict],
) -> dict[str, float]:
    """Aggregate tissue mappings into a canonical-region load profile.

    Each mapping dict must contain ``tissue_region`` (raw region string from
    the Tissue row) and the four factor columns.  Only significant loads
    (>= SIGNIFICANT_GROUP_LOAD) are included.
    """
    profile: dict[str, float] = {}
    for m in mappings:
        load = significant_mapping_load(m)
        if load < SIGNIFICANT_GROUP_LOAD:
            continue
        raw_region = m.get("tissue_region") or ""
        region = canonicalize_region(raw_region)
        if not region:
            continue
        profile[region] = max(profile.get(region, 0.0), load)
    return profile


# ---------------------------------------------------------------------------
# Main API: weekly exercise menu
# ---------------------------------------------------------------------------


def get_group_exercise_menu(session: Session) -> dict:
    """Return exercises grouped by training group with freshness-based availability.

    Response shape::

        {
            "groups": [
                {
                    "name": "Push",
                    "available": true,
                    "cooldown_days": 4,
                    "days_since_freshest": 5,
                    "exercises": [...]
                },
                ...
            ]
        }
    """
    today = user_today()

    # 1. Load all exercises
    exercises = session.exec(select(Exercise)).all()
    exercise_by_id: dict[int, Exercise] = {
        ex.id: ex for ex in exercises if ex.id is not None
    }

    # 2. Load all tissues (for region lookup)
    tissues = session.exec(select(Tissue)).all()
    tissue_by_id: dict[int, Tissue] = {
        t.id: t for t in tissues if t.id is not None
    }

    # 3. Load all exercise-tissue mappings
    all_et = session.exec(select(ExerciseTissue)).all()
    mappings_by_exercise: dict[int, list[dict]] = defaultdict(list)
    for et in all_et:
        tissue = tissue_by_id.get(et.tissue_id)
        if tissue is None:
            continue
        mappings_by_exercise[et.exercise_id].append({
            "tissue_region": tissue.region,
            "loading_factor": et.loading_factor,
            "routing_factor": et.routing_factor,
            "joint_strain_factor": et.joint_strain_factor,
            "tendon_strain_factor": et.tendon_strain_factor,
        })

    # 4. Classify each exercise
    exercise_groups: dict[int, tuple[str, float]] = {}
    for ex_id, ex in exercise_by_id.items():
        mappings = mappings_by_exercise.get(ex_id, [])
        profile = build_exercise_region_profile(mappings)
        group, confidence = classify_exercise(profile)
        exercise_groups[ex_id] = (group, confidence)

    # 5. Batch-load freshness: last trained date per exercise
    #    Only count sets that were actually completed (not abandoned plans).
    last_trained_stmt = (
        select(
            WorkoutSet.exercise_id,
            func.max(WorkoutSession.date).label("last_date"),
        )
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.completed_at.is_not(None))
        .group_by(WorkoutSet.exercise_id)
    )
    last_trained_rows = session.exec(last_trained_stmt).all()
    last_trained_map: dict[int, int | None] = {}
    for ex_id, last_date in last_trained_rows:
        if last_date is not None:
            last_trained_map[ex_id] = (today - last_date).days
        else:
            last_trained_map[ex_id] = None

    # 6. Batch-load recent RPE set counts (last 30 days)
    cutoff = today - timedelta(days=30)
    rpe_stmt = (
        select(
            WorkoutSet.exercise_id,
            func.count(WorkoutSet.id).label("cnt"),
        )
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.rpe.is_not(None),
            WorkoutSession.date >= cutoff,
        )
        .group_by(WorkoutSet.exercise_id)
    )
    rpe_rows = session.exec(rpe_stmt).all()
    rpe_map: dict[int, int] = {ex_id: cnt for ex_id, cnt in rpe_rows}

    # 7. Build per-exercise dicts and group them
    bodyweight_modes = {"bodyweight", "assisted_bodyweight"}
    min_sets_curve = 6  # minimum RPE sets for a curve fit

    exercises_by_group: dict[str, list[dict]] = defaultdict(list)
    for ex_id, ex in exercise_by_id.items():
        group, confidence = exercise_groups.get(ex_id, ("Uncategorized", 0.0))
        is_bw = (ex.load_input_mode or "external_weight") in bodyweight_modes
        days_since = last_trained_map.get(ex_id)
        rpe_count = rpe_map.get(ex_id, 0)
        has_curve = not is_bw and rpe_count >= min_sets_curve

        exercises_by_group[group].append({
            "exercise_id": ex_id,
            "name": ex.name,
            "group": group,
            "confidence": confidence,
            "days_since_trained": days_since,
            "allow_heavy_loading": ex.allow_heavy_loading,
            "load_input_mode": ex.load_input_mode or "external_weight",
            "set_metric_mode": ex.set_metric_mode or "reps",
            "is_bodyweight": is_bw,
            "recent_rpe_sets": rpe_count,
            "has_curve_fit": has_curve,
        })

    # 8. Compute per-group availability and build response
    def _sort_key(item: dict) -> tuple:
        """Sort: recently done first, never-trained last."""
        dst = item["days_since_trained"]
        if dst is None:
            return (1, 0)  # never-trained → bottom
        return (0, dst)  # lower days_since → higher in list

    groups = []
    for group_name in ALL_GROUPS:
        group_exercises = exercises_by_group.get(group_name, [])
        group_exercises.sort(key=_sort_key)
        cooldown = GROUP_COOLDOWN_DAYS.get(group_name, 4)

        # Freshest (most recently trained) exercise in the group
        trained_days = [
            e["days_since_trained"] for e in group_exercises
            if e["days_since_trained"] is not None
        ]
        days_since_freshest = min(trained_days) if trained_days else None

        # Available if ALL trained exercises are past cooldown
        # (never-trained exercises don't block availability)
        available = days_since_freshest is None or days_since_freshest >= cooldown

        groups.append({
            "name": group_name,
            "available": available,
            "cooldown_days": cooldown,
            "days_since_freshest": days_since_freshest,
            "exercises": group_exercises,
        })

    # Also include Uncategorized if any exist
    uncategorized = exercises_by_group.get("Uncategorized", [])
    if uncategorized:
        uncategorized.sort(key=_sort_key)
        groups.append({
            "name": "Uncategorized",
            "available": True,
            "cooldown_days": 0,
            "days_since_freshest": None,
            "exercises": uncategorized,
        })

    # Sort groups: least recently worked first (highest days_since_freshest),
    # never-trained groups at the end.
    def _group_sort_key(g: dict) -> tuple:
        dsf = g["days_since_freshest"]
        if dsf is None:
            return (1, 0)  # never trained → end
        return (0, -dsf)  # higher days_since → earlier

    groups.sort(key=_group_sort_key)

    return {"groups": groups}
