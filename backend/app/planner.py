"""Auto-generating workout planner.

Instead of requiring pre-configured program templates, this planner:
1. Groups tissues into trainable clusters (regions that naturally train together)
2. Scores each cluster by readiness + time since last trained
3. Selects exercises that cover the chosen cluster's tissues
4. Prescribes rep schemes based on tissue recovery state and e1RM
5. Predicts whether tomorrow should be a rest day
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    RecoveryCheckIn,
    WorkoutSession,
    WorkoutSet,
)
from app.training_model import build_exercise_strength, build_training_model_summary

# ── Tissue clusters ──────────────────────────────────────────────────
# Regions that naturally train together.  Each cluster is a list of
# tissue regions.  The planner picks the best cluster per day.

TISSUE_CLUSTERS: list[dict] = [
    {
        "label": "Push",
        "regions": ["chest", "shoulders", "triceps"],
    },
    {
        "label": "Pull",
        "regions": ["upper_back", "biceps", "forearms"],
    },
    {
        "label": "Legs",
        "regions": ["quads", "hamstrings", "glutes", "calves", "tibs"],
    },
    {
        "label": "Core & Posterior",
        "regions": ["core", "lower_back", "hips"],
    },
]

# Minimum average readiness to suggest training (below = rest day)
REST_DAY_THRESHOLD = 0.35
# Max exercises per session
MAX_EXERCISES = 6
MIN_EXERCISES = 3


def suggest_today(session: Session, *, as_of: date | None = None) -> dict:
    """Return auto-generated workout suggestion for today."""
    today = as_of or date.today()

    summary = build_training_model_summary(session, as_of=as_of, include_exercises=True)
    tissues_data = summary.get("tissues", [])
    exercises_data = summary.get("exercises", [])

    if not tissues_data:
        return {
            "as_of": today.isoformat(),
            "suggestion": None,
            "alternatives": [],
            "message": "No training data yet. Log some workouts first.",
        }

    # Load today's check-ins for immediate readiness adjustment
    todays_checkins = _load_todays_checkins(session, today)

    # Build region -> tissue readiness map
    region_readiness: dict[str, list[float]] = defaultdict(list)
    region_risk: dict[str, list[int]] = defaultdict(list)
    region_conditions: dict[str, list[str]] = defaultdict(list)
    tissue_id_by_region: dict[str, list[int]] = defaultdict(list)
    for t in tissues_data:
        tissue_info = t["tissue"]
        region = tissue_info.get("region", "other")
        recovery = t.get("recovery_estimate", 0.5)
        risk = t.get("risk_7d", 0)

        # Check for active tissue conditions (injury, tender)
        condition = t.get("current_condition")
        if condition:
            status = condition.get("status", "")
            if status == "injured":
                recovery = 0.0  # Force zero readiness for injured tissues
                risk = 100
            elif status == "tender":
                recovery = min(recovery, 0.3)  # Cap readiness for tender tissues
                risk = max(risk, 70)
            region_conditions[region].append(status)

        region_readiness[region].append(recovery)
        region_risk[region].append(risk)
        tissue_id_by_region[region].append(tissue_info["id"])

    # Apply today's check-in data as immediate readiness overrides.
    # The training model's recovery_estimate is based on historical load patterns
    # and doesn't react to same-day check-ins, so we apply these directly.
    for region, checkin in todays_checkins.items():
        pain = checkin["pain_0_10"]
        soreness = checkin["soreness_0_10"]
        stiffness = checkin["stiffness_0_10"]

        if pain >= 7:  # substantial or severe pain
            # Cap readiness at 0.15 — essentially blocked
            region_readiness[region] = [min(v, 0.15) for v in region_readiness[region]]
            region_risk[region] = [max(v, 80) for v in region_risk[region]]
        elif pain >= 4:  # some pain
            region_readiness[region] = [min(v, 0.4) for v in region_readiness[region]]
            region_risk[region] = [max(v, 60) for v in region_risk[region]]

        if soreness >= 7:  # substantial or severe soreness
            region_readiness[region] = [min(v, 0.35) for v in region_readiness[region]]
        elif soreness >= 4:  # some soreness
            region_readiness[region] = [v * 0.7 for v in region_readiness[region]]

        if stiffness >= 7:
            region_readiness[region] = [min(v, 0.5) for v in region_readiness[region]]

    # Find when each region was last trained
    region_last_trained = _region_last_trained(session, tissue_id_by_region, today)

    # Score each cluster
    scored_clusters = []
    for cluster in TISSUE_CLUSTERS:
        readiness_vals = []
        risk_vals = []
        days_since_vals = []
        for region in cluster["regions"]:
            readiness_vals.extend(region_readiness.get(region, [0.7]))
            risk_vals.extend(region_risk.get(region, [0]))
            days_since_vals.append(region_last_trained.get(region, 14))

        avg_readiness = sum(readiness_vals) / len(readiness_vals) if readiness_vals else 0.7
        avg_risk = sum(risk_vals) / len(risk_vals) if risk_vals else 0
        avg_days_since = sum(days_since_vals) / len(days_since_vals) if days_since_vals else 14

        # Penalize if high risk
        risk_penalty = min(avg_risk / 100, 0.5) * 0.3

        # Rotation score: more days since last = higher score, cap at 7
        rotation_score = min(avg_days_since / 7.0, 1.0)

        total_score = (avg_readiness - risk_penalty) * 0.6 + rotation_score * 0.4

        scored_clusters.append({
            "cluster": cluster,
            "score": round(total_score, 3),
            "readiness": round(avg_readiness, 3),
            "rotation": round(rotation_score, 3),
            "avg_days_since": round(avg_days_since, 1),
            "avg_risk": round(avg_risk, 1),
        })

    scored_clusters.sort(key=lambda x: x["score"], reverse=True)
    best = scored_clusters[0]

    # Check if best cluster is below rest threshold
    if best["readiness"] < REST_DAY_THRESHOLD:
        return {
            "as_of": today.isoformat(),
            "suggestion": None,
            "alternatives": [],
            "message": "All tissue groups are fatigued. Rest day recommended.",
        }

    # Collect blocked (injured) tissue IDs
    blocked_tissue_ids: set[int] = set()
    for t in tissues_data:
        condition = t.get("current_condition")
        if condition and condition.get("status") == "injured":
            blocked_tissue_ids.add(t["tissue"]["id"])

    # Select exercises for the best cluster
    target_regions = set(best["cluster"]["regions"])
    target_tissue_ids = set()
    for region in target_regions:
        target_tissue_ids.update(tissue_id_by_region.get(region, []))

    exercises = _select_exercises(
        session, exercises_data, target_tissue_ids, target_regions, today,
        blocked_tissue_ids=blocked_tissue_ids,
    )

    # Prescribe rep schemes
    prescribed = _prescribe_all(session, exercises, tissues_data, as_of=as_of)

    # Build alternatives from other clusters above rest threshold
    alternatives = []
    for sc in scored_clusters[1:]:
        if sc["readiness"] >= REST_DAY_THRESHOLD:
            alternatives.append(_cluster_to_suggestion_brief(sc))

    # Check tomorrow's outlook
    tomorrow_outlook = _tomorrow_outlook(scored_clusters, best)

    suggestion = {
        "day_label": best["cluster"]["label"],
        "readiness_score": best["readiness"],
        "days_since_last": best["avg_days_since"],
        "target_regions": list(target_regions),
        "exercises": prescribed,
        "rationale": _build_rationale(best),
        "tomorrow_outlook": tomorrow_outlook,
    }

    return {
        "as_of": today.isoformat(),
        "suggestion": suggestion,
        "alternatives": alternatives,
        "message": None,
    }


def _load_todays_checkins(session: Session, today: date) -> dict[str, dict]:
    """Load today's recovery check-ins keyed by region."""
    rows = session.exec(
        select(RecoveryCheckIn).where(RecoveryCheckIn.date == today)
    ).all()
    # If multiple check-ins for same region today, use most recent (highest id)
    result: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.id or 0):
        result[row.region] = {
            "pain_0_10": row.pain_0_10,
            "soreness_0_10": row.soreness_0_10,
            "stiffness_0_10": row.stiffness_0_10,
        }
    return result


def _region_last_trained(
    session: Session,
    tissue_id_by_region: dict[str, list[int]],
    today: date,
) -> dict[str, int]:
    """Find how many days ago each region was last trained."""
    all_tissue_ids = []
    for ids in tissue_id_by_region.values():
        all_tissue_ids.extend(ids)

    if not all_tissue_ids:
        return {}

    # Get recent workout sets with their exercise-tissue mappings
    cutoff = today - timedelta(days=30)
    stmt = (
        select(WorkoutSession.date, ExerciseTissue.tissue_id)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .join(ExerciseTissue, ExerciseTissue.exercise_id == WorkoutSet.exercise_id)
        .where(
            col(ExerciseTissue.tissue_id).in_(all_tissue_ids),
            col(WorkoutSession.date) >= cutoff,
            col(WorkoutSession.date) <= today,
        )
        .distinct()
    )
    rows = session.exec(stmt).all()

    # Map tissue_id -> latest date
    tissue_last: dict[int, date] = {}
    for session_date, tissue_id in rows:
        if tissue_id not in tissue_last or session_date > tissue_last[tissue_id]:
            tissue_last[tissue_id] = session_date

    # Aggregate to region level (use most recent tissue in region)
    result: dict[str, int] = {}
    for region, tissue_ids in tissue_id_by_region.items():
        latest = None
        for tid in tissue_ids:
            d = tissue_last.get(tid)
            if d and (latest is None or d > latest):
                latest = d
        result[region] = (today - latest).days if latest else 14

    return result


def _select_exercises(
    session: Session,
    exercises_data: list[dict],
    target_tissue_ids: set[int],
    target_regions: set[str],
    today: date,
    blocked_tissue_ids: set[int] | None = None,
) -> list[dict]:
    """Select a coherent subset of exercises that covers target tissues.

    Strategy:
    - Filter to exercises that are "good" or "caution" (not "avoid")
    - Skip exercises that primarily load injured/blocked tissues
    - Prefer exercises that hit more target tissues (compounds first)
    - Use greedy set-cover to maximize tissue coverage
    - Limit to MAX_EXERCISES
    """
    blocked = blocked_tissue_ids or set()

    # Build exercise -> target tissue coverage
    candidates = []
    for ex in exercises_data:
        rec = ex.get("recommendation", "good")
        if rec == "avoid":
            continue

        # Find which target tissues this exercise hits
        exercise_id = ex.get("exercise_id") or ex.get("id")
        if not exercise_id:
            continue
        mappings = session.exec(
            select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
        ).all()

        # Skip exercises that primarily load blocked (injured) tissues
        has_blocked_primary = any(
            m.tissue_id in blocked and m.role == "primary"
            for m in mappings
        )
        if has_blocked_primary:
            continue

        covered = set()
        total_routing = 0.0
        for m in mappings:
            if m.tissue_id in blocked:
                continue  # Don't count blocked tissues as coverage
            if m.tissue_id in target_tissue_ids and m.role in ("primary", "secondary"):
                covered.add(m.tissue_id)
                total_routing += m.routing_factor

        if not covered:
            continue

        # Preference: compounds (more tissues) + good recommendation + higher routing
        compound_bonus = len(covered) / max(len(target_tissue_ids), 1)
        rec_bonus = 1.0 if rec == "good" else 0.5
        score = compound_bonus * 0.4 + rec_bonus * 0.3 + min(total_routing, 1.0) * 0.3

        candidates.append({
            **ex,
            "covered_tissues": covered,
            "selection_score": score,
        })

    # Greedy set-cover: pick exercises that cover the most uncovered tissues
    selected = []
    covered_so_far: set[int] = set()

    # Sort by selection score descending
    candidates.sort(key=lambda x: x["selection_score"], reverse=True)

    for candidate in candidates:
        if len(selected) >= MAX_EXERCISES:
            break

        new_coverage = candidate["covered_tissues"] - covered_so_far
        if not new_coverage and len(selected) >= MIN_EXERCISES:
            continue  # Already have enough, skip redundant exercises

        # Prefer exercises that add new coverage
        if new_coverage or len(selected) < MIN_EXERCISES:
            selected.append(candidate)
            covered_so_far.update(candidate["covered_tissues"])

    return selected


def _prescribe_all(
    session: Session,
    exercises: list[dict],
    tissues_data: list[dict],
    *,
    as_of: date | None = None,
) -> list[dict]:
    """Prescribe sets/reps/weight for each selected exercise."""
    today = as_of or date.today()

    # Build tissue readiness lookup
    tissue_readiness: dict[int, float] = {}
    for t in tissues_data:
        tissue_readiness[t["tissue"]["id"]] = t.get("recovery_estimate", 0.5)

    results = []
    for ex in exercises:
        exercise_id = ex.get("exercise_id") or ex.get("id")

        # Get exercise object for equipment info
        exercise = session.get(Exercise, exercise_id)
        if not exercise:
            continue

        # Get e1RM
        current_e1rm = 0.0
        try:
            strength = build_exercise_strength(session, exercise_id, as_of=as_of)
            current_e1rm = strength.get("current_e1rm", 0.0)
        except Exception:
            pass

        # Compute avg readiness of tissues this exercise hits
        covered = ex.get("covered_tissues", set())
        readiness_vals = [tissue_readiness.get(tid, 0.7) for tid in covered]
        avg_readiness = sum(readiness_vals) / len(readiness_vals) if readiness_vals else 0.7

        # Days since heavy work
        days_since_heavy = _days_since_heavy_work(session, exercise_id, today)

        # Select rep scheme
        rep_scheme, target_reps, intensity_range, rationale = _select_rep_scheme(
            avg_readiness, days_since_heavy
        )

        # Compute target weight from e1RM
        target_weight = None
        if current_e1rm > 0:
            intensity = (intensity_range[0] + intensity_range[1]) / 2
            raw_weight = current_e1rm * intensity
            if exercise.equipment == "barbell":
                target_weight = round(raw_weight / 5) * 5
            elif exercise.equipment == "dumbbell":
                target_weight = round(raw_weight / 2.5) * 2.5
            else:
                target_weight = round(raw_weight / 5) * 5
            target_weight = max(target_weight, 0)

        # Get last performance for overload logic
        last_perf = _get_last_performance(session, exercise_id)
        overload_note = None
        if last_perf and target_weight and target_weight > 0:
            last_weight = last_perf.get("max_weight", 0)
            all_full = last_perf.get("all_full", False)
            if all_full and last_weight and last_weight >= target_weight:
                increment = 5.0 if exercise.equipment == "barbell" else 2.5
                target_weight = last_weight + increment
                overload_note = f"Progressive overload: +{increment} lbs"
            elif not all_full and last_weight:
                target_weight = last_weight
                overload_note = "Repeat weight, aim for full completion"

        target_sets = 3 if rep_scheme == "heavy" else 4 if rep_scheme == "volume" else 3

        results.append({
            "exercise_id": exercise.id,
            "exercise_name": ex.get("exercise_name") or ex.get("name") or exercise.name,
            "equipment": exercise.equipment,
            "rep_scheme": rep_scheme,
            "target_sets": target_sets,
            "target_reps": target_reps,
            "target_weight": target_weight,
            "rationale": rationale,
            "overload_note": overload_note,
            "current_e1rm": round(current_e1rm, 2) if current_e1rm else None,
            "avg_tissue_readiness": round(avg_readiness, 3),
            "last_performance": last_perf,
        })

    return results


def _select_rep_scheme(
    avg_readiness: float, days_since_heavy: int
) -> tuple[str, str, tuple[float, float], str]:
    """Select rep scheme based on tissue readiness and training history."""
    if avg_readiness >= 0.8 and days_since_heavy >= 5:
        return (
            "heavy",
            "3-5",
            (0.80, 0.85),
            "Tissues well-recovered and no recent heavy work; strength focus.",
        )
    elif avg_readiness >= 0.6:
        return (
            "volume",
            "8-12",
            (0.65, 0.75),
            "Moderate recovery; hypertrophy-focused volume work.",
        )
    else:
        return (
            "light",
            "15-20",
            (0.50, 0.60),
            "Low tissue readiness; light recovery work recommended.",
        )


def _days_since_heavy_work(session: Session, exercise_id: int, today: date) -> int:
    """Find how many days since last heavy set (<=5 reps) for this exercise."""
    stmt = (
        select(WorkoutSession.date)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.reps != None,  # noqa: E711
            WorkoutSet.reps <= 5,
        )
        .order_by(col(WorkoutSession.date).desc())
        .limit(1)
    )
    result = session.exec(stmt).first()
    if result is None:
        return 999
    return (today - result).days


def _get_last_performance(session: Session, exercise_id: int) -> dict | None:
    """Get last session's performance for an exercise."""
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    rows = session.exec(stmt).all()
    if not rows:
        return None

    last_date = rows[0][1]
    last_sets = [s for s, d in rows if d == last_date]
    all_full = all(s.rep_completion == "full" for s in last_sets)
    max_weight = max((s.weight or 0) for s in last_sets)

    return {
        "date": str(last_date),
        "sets": [
            {
                "reps": s.reps,
                "weight": s.weight,
                "rpe": s.rpe,
                "rep_completion": s.rep_completion,
            }
            for s in last_sets
        ],
        "all_full": all_full,
        "max_weight": max_weight,
    }


def _build_rationale(scored: dict) -> str:
    """Build human-readable rationale for why this cluster was chosen."""
    parts = []
    readiness_pct = round(scored["readiness"] * 100)
    parts.append(f"{readiness_pct}% tissue readiness")

    days = scored["avg_days_since"]
    if days >= 7:
        parts.append(f"not trained in {days:.0f} days")
    elif days >= 3:
        parts.append(f"last trained {days:.0f} days ago")
    else:
        parts.append(f"trained {days:.0f} days ago")

    if scored["avg_risk"] > 30:
        parts.append(f"moderate risk ({scored['avg_risk']:.0f}%)")

    return "; ".join(parts)


def _cluster_to_suggestion_brief(scored: dict) -> dict:
    """Convert a scored cluster to a brief alternative suggestion."""
    return {
        "day_label": scored["cluster"]["label"],
        "readiness_score": scored["readiness"],
        "days_since_last": scored["avg_days_since"],
        "target_regions": scored["cluster"]["regions"],
        "rationale": _build_rationale(scored),
    }


def _tomorrow_outlook(scored_clusters: list[dict], chosen_today: dict) -> str:
    """Predict what tomorrow looks like after today's session."""
    today_regions = set(chosen_today["cluster"]["regions"])

    # Find best non-overlapping cluster for tomorrow
    best_tomorrow = None
    for sc in scored_clusters:
        tomorrow_regions = set(sc["cluster"]["regions"])
        if tomorrow_regions & today_regions:
            continue  # Overlaps with today
        if sc["readiness"] >= REST_DAY_THRESHOLD:
            best_tomorrow = sc
            break

    if best_tomorrow:
        return f"Tomorrow: {best_tomorrow['cluster']['label']} ({round(best_tomorrow['readiness'] * 100)}% ready)"
    else:
        return "Tomorrow: rest day recommended"
