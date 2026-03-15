"""Auto-generating workout planner.

Selects which muscle groups to train today based on tissue readiness,
recovery check-ins, and injury status. Matches exercises to regions by
querying ExerciseTissue → Tissue → region. Supports saving plans to DB
and tracking progress through a workout.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    Tissue,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)
from app.training_model import build_exercise_strength, build_training_model_summary

# ── Tissue clusters ──────────────────────────────────────────────────

TISSUE_CLUSTERS: list[dict] = [
    {"label": "Push", "regions": ["chest", "shoulders", "triceps"]},
    {"label": "Pull", "regions": ["upper_back", "biceps", "forearms"]},
    {"label": "Legs", "regions": ["quads", "hamstrings", "glutes", "calves", "tibs"]},
    {"label": "Core & Posterior", "regions": ["core", "lower_back", "hips"]},
]

REST_DAY_THRESHOLD = 0.35
MAX_CANDIDATES = 16
DEFAULT_SELECTED = 8
AUTO_PROGRAM_NAME = "__auto_plan__"


# ── Main entry point ─────────────────────────────────────────────────


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

    # Load check-ins and build region state
    todays_checkins = _load_todays_checkins(session, today)
    region_state = _build_region_state(tissues_data, todays_checkins)

    # Find blocked regions (injured or substantial pain/soreness)
    blocked_regions = _blocked_regions(region_state, todays_checkins)

    # Build exercise → region mapping from DB (not from model summary)
    exercise_region_map = _build_exercise_region_map(session)

    # Find when each region was last trained
    region_last_trained = _region_last_trained_by_exercise(
        session, exercise_region_map, today
    )

    # Score each cluster using only AVAILABLE (non-blocked) regions
    scored_clusters = _score_clusters(region_state, blocked_regions, region_last_trained)
    scored_clusters.sort(key=lambda x: x["score"], reverse=True)

    if not scored_clusters or scored_clusters[0]["readiness"] < REST_DAY_THRESHOLD:
        return {
            "as_of": today.isoformat(),
            "suggestion": None,
            "alternatives": [],
            "message": "All tissue groups are fatigued. Rest day recommended.",
        }

    best = scored_clusters[0]

    # Collect adjacent cluster regions for secondary candidates
    adjacent_regions: set[str] = set()
    for sc in scored_clusters[1:]:
        if sc["readiness"] >= REST_DAY_THRESHOLD:
            adjacent_regions |= sc["available_regions"]
    adjacent_regions -= best["available_regions"]
    adjacent_regions -= blocked_regions

    # Select candidate exercises (up to MAX_CANDIDATES)
    candidates = _select_exercises(
        exercises_data,
        best["available_regions"],
        adjacent_regions,
        blocked_regions,
        exercise_region_map,
    )

    # Prescribe rep schemes
    prescribed = _prescribe_all(session, candidates, tissues_data, as_of=as_of)

    # Alternatives
    alternatives = [
        _cluster_brief(sc) for sc in scored_clusters[1:]
        if sc["readiness"] >= REST_DAY_THRESHOLD
    ]

    tomorrow_outlook = _tomorrow_outlook(scored_clusters, best)

    suggestion = {
        "day_label": best["label"],
        "readiness_score": best["readiness"],
        "days_since_last": best["avg_days_since"],
        "target_regions": list(best["available_regions"]),
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


# ── Plan persistence ─────────────────────────────────────────────────


def save_plan(
    session: Session,
    plan_date: date,
    day_label: str,
    target_regions: list[str],
    exercises: list[dict],
) -> dict:
    """Save today's plan to the database.

    Creates/reuses a TrainingProgram, creates a ProgramDay with exercises,
    and a PlannedSession for the date.
    """
    # Get or create the auto program
    program = session.exec(
        select(TrainingProgram).where(TrainingProgram.name == AUTO_PROGRAM_NAME)
    ).first()
    if not program:
        program = TrainingProgram(name=AUTO_PROGRAM_NAME, notes="Auto-generated daily plans")
        session.add(program)
        session.commit()
        session.refresh(program)

    # Delete any existing planned session for this date
    existing = session.exec(
        select(PlannedSession).where(PlannedSession.date == plan_date)
    ).all()
    for ps in existing:
        # Clean up the old program day and its exercises
        old_day = session.get(ProgramDay, ps.program_day_id)
        if old_day and old_day.program_id == program.id:
            old_exercises = session.exec(
                select(ProgramDayExercise).where(
                    ProgramDayExercise.program_day_id == old_day.id
                )
            ).all()
            for oe in old_exercises:
                session.delete(oe)
            session.delete(old_day)
        session.delete(ps)
    session.commit()

    # Create program day
    day = ProgramDay(
        program_id=program.id,
        day_label=day_label,
        target_regions=json.dumps(target_regions),
        sort_order=0,
    )
    session.add(day)
    session.commit()
    session.refresh(day)

    # Create exercises
    for i, ex in enumerate(exercises):
        rep_range = ex.get("target_reps", "8-12")
        parts = rep_range.split("-")
        rep_min = int(parts[0]) if parts else None
        rep_max = int(parts[-1]) if parts else None

        pde = ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=ex["exercise_id"],
            target_sets=ex.get("target_sets", 3),
            target_rep_min=rep_min,
            target_rep_max=rep_max,
            sort_order=i,
            notes=json.dumps({
                "rep_scheme": ex.get("rep_scheme"),
                "target_weight": ex.get("target_weight"),
            }),
        )
        session.add(pde)

    # Create planned session
    planned = PlannedSession(
        program_day_id=day.id,
        date=plan_date,
        status="planned",
    )
    session.add(planned)
    session.commit()
    session.refresh(planned)

    return _serialize_saved_plan(session, planned)


def add_exercises_to_plan(
    session: Session,
    plan_date: date,
    exercises: list[dict],
) -> dict:
    """Add exercises to an existing saved plan for the given date.

    Each entry in ``exercises`` must have ``exercise_id`` and optionally
    ``target_sets``, ``target_reps`` (e.g. "8-12"), ``rep_scheme``,
    ``target_weight``.  Returns the updated serialized plan.
    """
    planned = session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    day = session.get(ProgramDay, planned.program_day_id)
    if not day:
        raise ValueError("Plan day not found")

    # Determine next sort_order
    existing = session.exec(
        select(ProgramDayExercise)
        .where(ProgramDayExercise.program_day_id == day.id)
        .order_by(col(ProgramDayExercise.sort_order).desc())
        .limit(1)
    ).first()
    next_order = (existing.sort_order + 1) if existing else 0

    for i, ex in enumerate(exercises):
        rep_range = ex.get("target_reps", "8-12")
        parts = rep_range.split("-")
        rep_min = int(parts[0]) if parts else None
        rep_max = int(parts[-1]) if parts else None

        pde = ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=ex["exercise_id"],
            target_sets=ex.get("target_sets", 3),
            target_rep_min=rep_min,
            target_rep_max=rep_max,
            sort_order=next_order + i,
            notes=json.dumps({
                "rep_scheme": ex.get("rep_scheme"),
                "target_weight": ex.get("target_weight"),
            }),
        )
        session.add(pde)

    session.commit()
    session.refresh(planned)
    return _serialize_saved_plan(session, planned)


def remove_exercises_from_plan(
    session: Session,
    plan_date: date,
    exercise_ids: list[int],
) -> dict:
    """Remove exercises from an existing saved plan by exercise_id."""
    planned = session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    day = session.get(ProgramDay, planned.program_day_id)
    if not day:
        raise ValueError("Plan day not found")

    to_remove = session.exec(
        select(ProgramDayExercise).where(
            ProgramDayExercise.program_day_id == day.id,
            col(ProgramDayExercise.exercise_id).in_(exercise_ids),
        )
    ).all()
    for pde in to_remove:
        session.delete(pde)

    session.commit()
    session.refresh(planned)
    return _serialize_saved_plan(session, planned)


def get_saved_plan(session: Session, plan_date: date) -> dict | None:
    """Get today's saved plan with progress."""
    planned = session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()

    if not planned:
        return None

    return _serialize_saved_plan(session, planned)


def start_workout(session: Session, planned_session_id: int) -> dict:
    """Create a WorkoutSession and link it to the PlannedSession."""
    planned = session.get(PlannedSession, planned_session_id)
    if not planned:
        raise ValueError("Planned session not found")

    if planned.workout_session_id:
        # Already started — return the existing session
        ws = session.get(WorkoutSession, planned.workout_session_id)
        return {"workout_session_id": ws.id if ws else None, "already_started": True}

    ws = WorkoutSession(date=planned.date)
    session.add(ws)
    session.commit()
    session.refresh(ws)

    planned.workout_session_id = ws.id
    planned.status = "in_progress"
    session.add(planned)
    session.commit()

    return {"workout_session_id": ws.id, "already_started": False}


def complete_workout(session: Session, planned_session_id: int) -> dict:
    """Mark a planned session as completed."""
    planned = session.get(PlannedSession, planned_session_id)
    if not planned:
        raise ValueError("Planned session not found")

    planned.status = "completed"
    session.add(planned)
    session.commit()

    return {"id": planned.id, "status": "completed"}


def _serialize_saved_plan(session: Session, planned: PlannedSession) -> dict:
    """Serialize a saved plan with exercise details and progress."""
    day = session.get(ProgramDay, planned.program_day_id)
    if not day:
        return {"id": planned.id, "error": "Program day not found"}

    day_exercises = list(session.exec(
        select(ProgramDayExercise)
        .where(ProgramDayExercise.program_day_id == day.id)
        .order_by(ProgramDayExercise.sort_order)
    ).all())

    # Get logged sets for this workout session
    logged_sets: dict[int, list[dict]] = defaultdict(list)
    if planned.workout_session_id:
        sets = session.exec(
            select(WorkoutSet)
            .where(WorkoutSet.session_id == planned.workout_session_id)
            .order_by(WorkoutSet.set_order)
        ).all()
        for s in sets:
            logged_sets[s.exercise_id].append({
                "id": s.id,
                "set_order": s.set_order,
                "reps": s.reps,
                "weight": s.weight,
                "rpe": s.rpe,
                "rep_completion": s.rep_completion,
                "notes": s.notes,
            })

    exercises = []
    for pde in day_exercises:
        exercise = session.get(Exercise, pde.exercise_id)
        meta = {}
        if pde.notes:
            try:
                meta = json.loads(pde.notes)
            except (json.JSONDecodeError, TypeError):
                pass

        completed_sets = logged_sets.get(pde.exercise_id, [])
        exercises.append({
            "exercise_id": pde.exercise_id,
            "exercise_name": exercise.name if exercise else "Unknown",
            "equipment": exercise.equipment if exercise else None,
            "target_sets": pde.target_sets,
            "target_rep_min": pde.target_rep_min,
            "target_rep_max": pde.target_rep_max,
            "rep_scheme": meta.get("rep_scheme"),
            "target_weight": meta.get("target_weight"),
            "completed_sets": completed_sets,
            "sets_done": len(completed_sets),
            "done": len(completed_sets) >= pde.target_sets,
        })

    return {
        "id": planned.id,
        "date": planned.date.isoformat(),
        "status": planned.status,
        "day_label": day.day_label,
        "target_regions": json.loads(day.target_regions) if day.target_regions else [],
        "workout_session_id": planned.workout_session_id,
        "exercises": exercises,
    }


# ── Region state ─────────────────────────────────────────────────────


def _load_todays_checkins(session: Session, today: date) -> dict[str, dict]:
    rows = session.exec(
        select(RecoveryCheckIn).where(RecoveryCheckIn.date == today)
    ).all()
    result: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.id or 0):
        result[row.region] = {
            "pain_0_10": row.pain_0_10,
            "soreness_0_10": row.soreness_0_10,
            "stiffness_0_10": row.stiffness_0_10,
            "readiness_0_10": row.readiness_0_10,
        }
    return result


def _build_region_state(
    tissues_data: list[dict],
    checkins: dict[str, dict],
) -> dict[str, dict]:
    """Build per-region aggregate readiness and risk from model + check-ins."""
    region_recovery: dict[str, list[float]] = defaultdict(list)
    region_risk: dict[str, list[int]] = defaultdict(list)

    for t in tissues_data:
        tissue_info = t["tissue"]
        region = tissue_info.get("region", "other")
        recovery = t.get("recovery_estimate", 0.5)
        risk = t.get("risk_7d", 0)

        condition = t.get("current_condition")
        if condition:
            status = condition.get("status", "")
            if status == "injured":
                recovery = 0.0
                risk = 100
            elif status == "tender":
                recovery = min(recovery, 0.3)
                risk = max(risk, 70)

        region_recovery[region].append(recovery)
        region_risk[region].append(risk)

    # Apply check-in overrides
    for region, ci in checkins.items():
        if region not in region_recovery:
            continue
        pain = ci["pain_0_10"]
        sore = ci["soreness_0_10"]
        stiffness = ci["stiffness_0_10"]
        readiness = ci.get("readiness_0_10", 5)
        if pain >= 7:
            region_recovery[region] = [min(v, 0.15) for v in region_recovery[region]]
            region_risk[region] = [max(v, 80) for v in region_risk[region]]
        elif pain >= 4:
            region_recovery[region] = [min(v, 0.4) for v in region_recovery[region]]
            region_risk[region] = [max(v, 60) for v in region_risk[region]]
        if sore >= 7:
            region_recovery[region] = [min(v, 0.35) for v in region_recovery[region]]
        elif sore >= 4:
            region_recovery[region] = [v * 0.7 for v in region_recovery[region]]
        if stiffness >= 7:
            region_recovery[region] = [min(v, 0.45) for v in region_recovery[region]]
            region_risk[region] = [max(v, 70) for v in region_risk[region]]
        elif stiffness >= 4:
            region_recovery[region] = [v * 0.8 for v in region_recovery[region]]
            region_risk[region] = [max(v, 50) for v in region_risk[region]]
        if readiness <= 2:
            region_recovery[region] = [min(v, 0.25) for v in region_recovery[region]]
            region_risk[region] = [max(v, 75) for v in region_risk[region]]
        elif readiness <= 4:
            region_recovery[region] = [min(v, 0.55) for v in region_recovery[region]]
            region_risk[region] = [max(v, 55) for v in region_risk[region]]
        elif readiness >= 8:
            region_recovery[region] = [min(1.0, v * 1.05) for v in region_recovery[region]]

    result = {}
    for region in region_recovery:
        vals = region_recovery[region]
        risks = region_risk[region]
        result[region] = {
            "readiness": sum(vals) / len(vals) if vals else 0.5,
            "risk": sum(risks) / len(risks) if risks else 0,
        }
    return result


def _blocked_regions(
    region_state: dict[str, dict],
    checkins: dict[str, dict],
) -> set[str]:
    """Regions that should be excluded from exercise selection."""
    blocked = set()
    for region, state in region_state.items():
        if state["readiness"] <= 0.15:  # injured or severe pain
            blocked.add(region)
    for region, ci in checkins.items():
        if (
            ci["pain_0_10"] >= 7
            or ci["soreness_0_10"] >= 7
            or ci.get("readiness_0_10", 5) <= 2
        ):
            blocked.add(region)
    return blocked


# ── Exercise → region mapping ────────────────────────────────────────


def _build_exercise_region_map(session: Session) -> dict[int, list[dict]]:
    """Build exercise_id → list of {region, role, routing_factor} from DB."""
    rows = session.exec(
        select(
            ExerciseTissue.exercise_id,
            Tissue.region,
            ExerciseTissue.role,
            ExerciseTissue.routing_factor,
        )
        .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
    ).all()

    result: dict[int, list[dict]] = defaultdict(list)
    for exercise_id, region, role, routing in rows:
        result[exercise_id].append({
            "region": region,
            "role": role,
            "routing": routing,
        })
    return result


def _region_last_trained_by_exercise(
    session: Session,
    exercise_region_map: dict[int, list[dict]],
    today: date,
) -> dict[str, int]:
    """Find days since each region was last trained, using exercise→region map."""
    cutoff = today - timedelta(days=30)
    stmt = (
        select(WorkoutSession.date, WorkoutSet.exercise_id)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(col(WorkoutSession.date) >= cutoff, col(WorkoutSession.date) <= today)
        .distinct()
    )
    rows = session.exec(stmt).all()

    region_latest: dict[str, date] = {}
    for session_date, exercise_id in rows:
        for mapping in exercise_region_map.get(exercise_id, []):
            region = mapping["region"]
            if region not in region_latest or session_date > region_latest[region]:
                region_latest[region] = session_date

    return {
        region: (today - d).days for region, d in region_latest.items()
    }


# ── Cluster scoring ──────────────────────────────────────────────────


def _score_clusters(
    region_state: dict[str, dict],
    blocked_regions: set[str],
    region_last_trained: dict[str, int],
) -> list[dict]:
    """Score clusters using only their AVAILABLE (non-blocked) regions."""
    scored = []
    for cluster in TISSUE_CLUSTERS:
        available = [r for r in cluster["regions"] if r not in blocked_regions]
        if not available:
            continue  # All regions blocked — skip cluster

        readiness_vals = [region_state.get(r, {"readiness": 0.7})["readiness"] for r in available]
        risk_vals = [region_state.get(r, {"risk": 0})["risk"] for r in available]
        days_since_vals = [region_last_trained.get(r, 14) for r in available]

        avg_readiness = sum(readiness_vals) / len(readiness_vals)
        avg_risk = sum(risk_vals) / len(risk_vals)
        avg_days_since = sum(days_since_vals) / len(days_since_vals)

        risk_penalty = min(avg_risk / 100, 0.5) * 0.3
        rotation_score = min(avg_days_since / 7.0, 1.0)
        total_score = (avg_readiness - risk_penalty) * 0.6 + rotation_score * 0.4

        # Label shows blocked regions
        blocked_in_cluster = [r for r in cluster["regions"] if r in blocked_regions]
        label = cluster["label"]
        if blocked_in_cluster:
            label += f" (skip: {', '.join(blocked_in_cluster)})"

        scored.append({
            "cluster": cluster,
            "label": label,
            "available_regions": set(available),
            "blocked_in_cluster": blocked_in_cluster,
            "score": round(total_score, 3),
            "readiness": round(avg_readiness, 3),
            "rotation": round(rotation_score, 3),
            "avg_days_since": round(avg_days_since, 1),
            "avg_risk": round(avg_risk, 1),
        })

    return scored


# ── Exercise selection ───────────────────────────────────────────────


def _select_exercises(
    exercises_data: list[dict],
    target_regions: set[str],
    adjacent_regions: set[str],
    blocked_regions: set[str],
    exercise_region_map: dict[int, list[dict]],
) -> list[dict]:
    """Select up to MAX_CANDIDATES exercises, prioritizing target regions.

    Returns candidates sorted by relevance. First DEFAULT_SELECTED are marked
    ``selected=True``; the rest are ``selected=False`` so the UI can show
    checkboxes.
    """
    primary_candidates: list[dict] = []
    adjacent_candidates: list[dict] = []

    for ex in exercises_data:
        rec = ex.get("recommendation", "good")
        if rec == "avoid":
            continue

        exercise_id = ex.get("exercise_id") or ex.get("id")
        if not exercise_id:
            continue

        region_mappings = exercise_region_map.get(exercise_id, [])
        if not region_mappings:
            continue

        # Skip if any PRIMARY mapping is in a blocked region
        has_blocked_primary = any(
            m["region"] in blocked_regions and m["role"] == "primary"
            for m in region_mappings
        )
        if has_blocked_primary:
            continue

        # Score hits on target regions
        target_hits = []
        target_routing = 0.0
        for m in region_mappings:
            if m["region"] in target_regions and m["role"] in ("primary", "secondary"):
                target_hits.append(m["region"])
                target_routing += m["routing"]

        # Score hits on adjacent regions (secondary pool)
        adj_hits = []
        adj_routing = 0.0
        for m in region_mappings:
            if m["region"] in adjacent_regions and m["role"] in ("primary", "secondary"):
                adj_hits.append(m["region"])
                adj_routing += m["routing"]

        if not target_hits and not adj_hits:
            continue

        rec_bonus = 1.0 if rec == "good" else 0.5

        if target_hits:
            coverage = len(set(target_hits)) / max(len(target_regions), 1)
            score = coverage * 0.4 + rec_bonus * 0.3 + min(target_routing, 1.0) * 0.3
            primary_candidates.append({
                **ex,
                "target_hits": set(target_hits),
                "selection_score": score,
            })
        else:
            coverage = len(set(adj_hits)) / max(len(adjacent_regions), 1)
            score = coverage * 0.3 + rec_bonus * 0.3 + min(adj_routing, 1.0) * 0.2
            adjacent_candidates.append({
                **ex,
                "target_hits": set(adj_hits),
                "selection_score": score,
            })

    # Sort each pool by score descending
    primary_candidates.sort(key=lambda x: x["selection_score"], reverse=True)
    adjacent_candidates.sort(key=lambda x: x["selection_score"], reverse=True)

    # Fill from primary first, then adjacent, up to MAX_CANDIDATES
    combined = primary_candidates[:MAX_CANDIDATES]
    remaining = MAX_CANDIDATES - len(combined)
    if remaining > 0:
        combined.extend(adjacent_candidates[:remaining])

    # Mark first DEFAULT_SELECTED as selected
    for i, c in enumerate(combined):
        c["selected"] = i < DEFAULT_SELECTED

    return combined


# ── Rep scheme prescription ──────────────────────────────────────────


def _prescribe_all(
    session: Session,
    exercises: list[dict],
    tissues_data: list[dict],
    *,
    as_of: date | None = None,
) -> list[dict]:
    today = as_of or date.today()
    tissue_readiness: dict[int, float] = {}
    for t in tissues_data:
        tissue_readiness[t["tissue"]["id"]] = t.get("recovery_estimate", 0.5)

    results = []
    for ex in exercises:
        exercise_id = ex.get("exercise_id") or ex.get("id")
        exercise = session.get(Exercise, exercise_id)
        if not exercise:
            continue

        current_e1rm = 0.0
        try:
            strength = build_exercise_strength(session, exercise_id, as_of=as_of)
            current_e1rm = strength.get("current_e1rm", 0.0)
        except Exception:
            pass

        suitability_value = ex.get("suitability_score", ex.get("suitability", 70))
        suitability = suitability_value / 100 if suitability_value > 1 else suitability_value
        recommendation = ex.get("recommendation", "good")
        weighted_risk = ex.get("weighted_risk_7d", 0.0)
        if recommendation == "caution":
            suitability = min(suitability, 0.74)
        days_since_heavy = _days_since_heavy_work(session, exercise_id, today)
        rep_scheme, target_reps, intensity_range, rationale = _select_rep_scheme(
            suitability,
            days_since_heavy,
            recommendation=recommendation,
            weighted_risk_7d=weighted_risk,
        )

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

        last_perf = _get_last_performance(session, exercise_id)
        overload_note = None
        if last_perf and target_weight and target_weight > 0:
            last_weight = last_perf.get("max_weight", 0)
            all_full = last_perf.get("all_full", False)
            if all_full and last_weight and last_weight >= target_weight:
                increment = 5.0 if exercise.equipment == "barbell" else 2.5
                target_weight = last_weight + increment
                overload_note = f"+{increment} lbs (progressive overload)"
            elif not all_full and last_weight:
                target_weight = last_weight
                overload_note = "Same weight, aim for full completion"

        if rep_scheme == "heavy":
            target_sets = 3
        elif rep_scheme == "volume":
            target_sets = 3 if recommendation == "caution" else 4
        else:
            target_sets = 2 if weighted_risk >= 55 else 3

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
            "selected": ex.get("selected", True),
        })

    return results


def _select_rep_scheme(
    suitability: float,
    days_since_heavy: int,
    *,
    recommendation: str = "good",
    weighted_risk_7d: float = 0.0,
) -> tuple[str, str, tuple[float, float], str]:
    if recommendation == "avoid":
        return ("light", "15-20", (0.50, 0.60), "Exercise is currently in the avoid band.")
    if recommendation == "caution" and weighted_risk_7d >= 50:
        return ("light", "12-15", (0.50, 0.60), "Caution flag and elevated risk; use a light dose.")
    if suitability >= 0.8 and days_since_heavy >= 5 and recommendation == "good":
        return ("heavy", "3-5", (0.80, 0.85), "Well-recovered; strength focus.")
    elif suitability >= 0.6:
        return ("volume", "8-12", (0.65, 0.75), "Moderate recovery; hypertrophy.")
    else:
        return ("light", "15-20", (0.50, 0.60), "Low readiness; light work.")


def _days_since_heavy_work(session: Session, exercise_id: int, today: date) -> int:
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
    return (today - result).days if result else 999


def _get_last_performance(session: Session, exercise_id: int) -> dict | None:
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
    return {
        "date": str(last_date),
        "sets": [
            {"reps": s.reps, "weight": s.weight, "rpe": s.rpe, "rep_completion": s.rep_completion}
            for s in last_sets
        ],
        "all_full": all(s.rep_completion == "full" for s in last_sets),
        "max_weight": max((s.weight or 0) for s in last_sets),
    }


# ── Helpers ──────────────────────────────────────────────────────────


def _build_rationale(scored: dict) -> str:
    parts = [f"{round(scored['readiness'] * 100)}% readiness"]
    days = scored["avg_days_since"]
    if days >= 7:
        parts.append(f"not trained in {days:.0f} days")
    elif days >= 3:
        parts.append(f"last trained {days:.0f} days ago")
    if scored.get("blocked_in_cluster"):
        parts.append(f"avoiding: {', '.join(scored['blocked_in_cluster'])}")
    return "; ".join(parts)


def _cluster_brief(scored: dict) -> dict:
    return {
        "day_label": scored["label"],
        "readiness_score": scored["readiness"],
        "days_since_last": scored["avg_days_since"],
        "target_regions": list(scored["available_regions"]),
        "rationale": _build_rationale(scored),
    }


def _tomorrow_outlook(scored_clusters: list[dict], chosen_today: dict) -> str:
    today_regions = chosen_today["available_regions"]
    for sc in scored_clusters:
        if sc["available_regions"] & today_regions:
            continue
        if sc["readiness"] >= REST_DAY_THRESHOLD:
            return f"Tomorrow: {sc['label']} ({round(sc['readiness'] * 100)}% ready)"
    return "Tomorrow: rest day recommended"
