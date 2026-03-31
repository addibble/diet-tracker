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

from app.exercise_loads import bodyweight_by_date, latest_bodyweight
from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    Tissue,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.tracked_tissues import (
    default_performed_side,
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_tracked_tissue_lookup,
    tracked_tissue_side_weights,
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
_MAX_HEAVY_EXERCISES_PER_SESSION = 3
_MAX_HEAVY_EXERCISES_PER_PRIMARY_REGION = 1
_HIGH_REP_STRENGTH_ANCHOR_THRESHOLD = 8
_HEAVY_WEIGHT_BLEND_RATIO = 0.5

# Per-tissue fatigue gates used in exercise selection.
# Tissues with routing_factor >= this threshold are considered "significantly
# loaded" for the purposes of fatigue checking (catches primary muscles and
# meaningful secondary muscles, while ignoring distant stabilizers).
_SIGNIFICANT_ROUTING = 0.3
# Below HARD floor → exercise is skipped entirely (tissue too fatigued).
_TISSUE_FATIGUE_HARD_FLOOR = 0.4
# Below SOFT floor → selection score is linearly discounted to zero at
# the HARD floor, so fresher alternatives sort ahead.
_TISSUE_FATIGUE_SOFT_FLOOR = 0.7
_TRACKED_DIRECT_PROTECTION_THRESHOLD = 0.25
_TRACKED_CROSS_SUPPORT_THRESHOLD = 0.1
_EARLY_REHAB_STAGES = {
    "calm-and-isometric",
    "protected-range",
    "tolerance-building",
    "neural-calming",
}
_MID_REHAB_STAGES = {
    "rebuild-capacity",
    "controlled-dynamic",
    "activation-and-control",
    "eccentric-concentric",
}
_LATE_REHAB_STAGES = {
    "return-to-heavy-slow",
    "return-to-overhead",
    "return-to-grip-load",
    "strength-rebuild",
}


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
                "performed_side": ex.get("performed_side"),
                "side_explanation": ex.get("side_explanation"),
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
                "performed_side": ex.get("performed_side"),
                "side_explanation": ex.get("side_explanation"),
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
    """Create a WorkoutSession, pre-fill sets from plan, and link to PlannedSession."""
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

    # Pre-create sets from plan targets
    day = session.get(ProgramDay, planned.program_day_id)
    if day:
        pdes = session.exec(
            select(ProgramDayExercise)
            .where(ProgramDayExercise.program_day_id == day.id)
            .order_by(ProgramDayExercise.sort_order)
        ).all()
        set_order = 0
        for pde in pdes:
            meta = {}
            if pde.notes:
                try:
                    meta = json.loads(pde.notes)
                except (json.JSONDecodeError, TypeError):
                    pass
            target_weight = meta.get("target_weight")
            performed_side = meta.get("performed_side")
            target_reps = pde.target_rep_max or pde.target_rep_min
            for _ in range(pde.target_sets):
                s = WorkoutSet(
                    session_id=ws.id,
                    exercise_id=pde.exercise_id,
                    set_order=set_order,
                    performed_side=default_performed_side(
                        exercise_name=session.get(Exercise, pde.exercise_id).name if session.get(Exercise, pde.exercise_id) else "",
                        exercise_laterality=session.get(Exercise, pde.exercise_id).laterality if session.get(Exercise, pde.exercise_id) else "bilateral",
                        provided_side=performed_side,
                    ),
                    weight=target_weight,
                    reps=target_reps,
                )
                session.add(s)
                set_order += 1

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


def delete_plan(session: Session, plan_date: date) -> None:
    """Delete a planned session and its ProgramDay + exercises."""
    planned = session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    day = session.get(ProgramDay, planned.program_day_id)
    if day:
        pdes = session.exec(
            select(ProgramDayExercise)
            .where(ProgramDayExercise.program_day_id == day.id)
        ).all()
        for pde in pdes:
            session.delete(pde)
        session.delete(day)

    session.delete(planned)
    session.commit()


def reorder_plan_exercises(
    session: Session,
    plan_date: date,
    pde_ids: list[int],
) -> dict:
    """Reorder exercises in a saved plan by setting sort_order."""
    planned = session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    for i, pde_id in enumerate(pde_ids):
        pde = session.get(ProgramDayExercise, pde_id)
        if pde:
            pde.sort_order = i
            session.add(pde)

    session.commit()
    return _serialize_saved_plan(session, planned)


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
                "performed_side": s.performed_side,
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
            "pde_id": pde.id,
            "exercise_id": pde.exercise_id,
            "exercise_name": exercise.name if exercise else "Unknown",
            "equipment": exercise.equipment if exercise else None,
            "load_input_mode": (
                exercise.load_input_mode if exercise else "external_weight"
            ),
            "laterality": exercise.laterality if exercise else "bilateral",
            "target_sets": pde.target_sets,
            "target_rep_min": pde.target_rep_min,
            "target_rep_max": pde.target_rep_max,
            "rep_scheme": meta.get("rep_scheme"),
            "target_weight": meta.get("target_weight"),
            "performed_side": meta.get("performed_side"),
            "side_explanation": meta.get("side_explanation"),
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

        # Per-tissue fatigue gate: look at recovery_state for every tissue
        # that is significantly loaded by this exercise.  Averaging across
        # all tissues masks recently-trained muscles (e.g. adductors trained
        # yesterday drag down Leg Press even though hamstrings are fresh).
        min_significant_recovery = min(
            (
                tm.get("recovery_state", 1.0)
                for tm in ex.get("tissues", [])
                if tm.get("routing_factor", 0.0) >= _SIGNIFICANT_ROUTING
            ),
            default=1.0,
        )
        if min_significant_recovery < _TISSUE_FATIGUE_HARD_FLOOR:
            continue  # Skip: one or more heavily-loaded tissues are too fatigued
        # Linear discount between hard and soft floor: 0.0 at HARD_FLOOR,
        # 1.0 at SOFT_FLOOR and above.
        fatigue_factor = min(
            (min_significant_recovery - _TISSUE_FATIGUE_HARD_FLOOR)
            / (_TISSUE_FATIGUE_SOFT_FLOOR - _TISSUE_FATIGUE_HARD_FLOOR),
            1.0,
        )

        # Score hits on target regions
        target_hits = []
        target_primary_hits = []
        target_routing = 0.0
        for m in region_mappings:
            if m["region"] in target_regions and m["role"] in ("primary", "secondary"):
                target_hits.append(m["region"])
                target_routing += m["routing"]
                if m["role"] == "primary":
                    target_primary_hits.append(m["region"])

        # Score hits on adjacent regions (secondary pool)
        adj_hits = []
        adj_primary_hits = []
        adj_routing = 0.0
        for m in region_mappings:
            if m["region"] in adjacent_regions and m["role"] in ("primary", "secondary"):
                adj_hits.append(m["region"])
                adj_routing += m["routing"]
                if m["role"] == "primary":
                    adj_primary_hits.append(m["region"])

        if not target_hits and not adj_hits:
            continue

        rec_bonus = 1.0 if rec == "good" else 0.5
        # Normalize suitability to [0, 1] to deprioritize exercises that load
        # fatigued or at-risk tissues (lower suitability = more tissue risk).
        suitability_norm = min(ex.get("suitability_score", 70) / 100.0, 1.0)

        if target_hits:
            coverage = len(set(target_hits)) / max(len(target_regions), 1)
            score = (
                coverage * 0.35
                + rec_bonus * 0.25
                + min(target_routing, 1.0) * 0.25
                + suitability_norm * 0.15
            ) * fatigue_factor
            primary_candidates.append({
                **ex,
                "target_hits": set(target_hits),
                "primary_regions": set(target_primary_hits) or set(target_hits),
                "selection_score": score,
            })
        else:
            coverage = len(set(adj_hits)) / max(len(adjacent_regions), 1)
            score = (
                coverage * 0.25
                + rec_bonus * 0.25
                + min(adj_routing, 1.0) * 0.20
                + suitability_norm * 0.10
            ) * fatigue_factor
            adjacent_candidates.append({
                **ex,
                "target_hits": set(adj_hits),
                "primary_regions": set(adj_primary_hits) or set(adj_hits),
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
    tissue_condition_by_id: dict[int, dict] = {}
    for t in tissues_data:
        tid = t["tissue"]["id"]
        tissue_readiness[tid] = t.get("recovery_estimate", 0.5)
        cond = t.get("current_condition")
        if cond:
            tissue_condition_by_id[tid] = cond

    tracked_conditions = get_all_current_tracked_conditions(session)
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    tracked_lookup = {
        tracked.id: tracked
        for tracked in get_tracked_tissue_lookup(session).values()
    }

    # Load current bodyweight for mixed/bodyweight exercise adjustments
    weight_rows = list(
        session.exec(select(WeightLog).order_by(col(WeightLog.logged_at).asc())).all()
    )
    bw_by_date_map = bodyweight_by_date(
        [r for r in weight_rows if r.logged_at.date() <= today]
    )
    current_bw = latest_bodyweight(bw_by_date_map, today)
    session_primary_regions = {
        region
        for ex in exercises
        for region in ex.get("primary_regions", ex.get("target_hits", set()))
    }
    session_heavy_limit = min(
        _MAX_HEAVY_EXERCISES_PER_SESSION,
        max(1, len(session_primary_regions)),
    )
    heavy_primary_region_counts: defaultdict[str, int] = defaultdict(int)
    heavy_session_count = 0

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

        preferred_side, side_explanation, rehab_stage, prescription_mode = _planner_preferred_side(
            exercise=exercise,
            exercise_summary=ex,
            tracked_lookup=tracked_lookup,
            tracked_conditions=tracked_conditions,
            active_rehab_plans=active_rehab_plans,
        )
        if preferred_side == "block":
            continue

        suitability_value = ex.get("suitability_score", ex.get("suitability", 70))
        suitability = suitability_value / 100 if suitability_value > 1 else suitability_value
        recommendation = ex.get("recommendation", "good")
        weighted_risk = ex.get("weighted_risk_7d", 0.0)
        if prescription_mode == "direct_rehab" and rehab_stage in _EARLY_REHAB_STAGES:
            recommendation = "caution"
            suitability = min(suitability, 0.55)
            weighted_risk = max(weighted_risk, 55.0)
        elif prescription_mode == "direct_rehab" and rehab_stage in _MID_REHAB_STAGES:
            recommendation = "caution"
            suitability = min(suitability, 0.7)
            weighted_risk = max(weighted_risk, 40.0)
        if recommendation == "caution":
            suitability = min(suitability, 0.74)
        days_since_heavy = _days_since_heavy_work(session, exercise_id, today)
        rep_scheme, target_reps, intensity_range, rationale = _select_rep_scheme(
            suitability,
            days_since_heavy,
            recommendation=recommendation,
            weighted_risk_7d=weighted_risk,
        )
        rep_scheme, target_reps, intensity_range, rationale = _apply_rehab_stage_prescription(
            rep_scheme=rep_scheme,
            target_reps=target_reps,
            intensity_range=intensity_range,
            rationale=rationale,
            rehab_stage=rehab_stage,
            side_explanation=side_explanation,
            prescription_mode=prescription_mode,
        )
        primary_regions = set(ex.get("primary_regions", ex.get("target_hits", set())))
        rep_scheme, target_reps, intensity_range, rationale = _apply_session_heavy_budget(
            rep_scheme=rep_scheme,
            target_reps=target_reps,
            intensity_range=intensity_range,
            rationale=rationale,
            primary_regions=primary_regions,
            heavy_session_count=heavy_session_count,
            session_heavy_limit=session_heavy_limit,
            heavy_primary_region_counts=heavy_primary_region_counts,
        )
        if rep_scheme == "heavy":
            heavy_session_count += 1
            for region in primary_regions:
                heavy_primary_region_counts[region] += 1

        # Find the most restrictive loading factor from tender/rehabbing tissues.
        # Only tissues with a significant routing factor (>= 0.3) are considered
        # so distant stabilizers don't unnecessarily suppress the weight.
        min_condition_factor = 1.0
        condition_label = None
        for tm in ex.get("tissues", []):
            tid = tm.get("tissue_id")
            if not tid:
                continue
            if tm.get("routing_factor", 0.0) < 0.3:
                continue
            cond = tissue_condition_by_id.get(tid)
            if not cond:
                continue
            status = cond.get("status", "healthy")
            max_lf = cond.get("max_loading_factor")
            if status == "tender":
                factor = min(max_lf, 0.6) if max_lf is not None else 0.6
            elif status == "rehabbing":
                factor = max_lf if max_lf is not None else 0.7
            else:
                continue
            if factor < min_condition_factor:
                min_condition_factor = factor
                condition_label = f"{status} {tm.get('tissue_display_name', '')}"

        weight_adjustment_note = None
        if min_condition_factor < 1.0 and condition_label:
            pct = round(min_condition_factor * 100)
            weight_adjustment_note = f"Reduced to {pct}% load due to {condition_label}"
        if prescription_mode == "direct_rehab" and rehab_stage in _EARLY_REHAB_STAGES:
            min_condition_factor = min(min_condition_factor, 0.6)
            if weight_adjustment_note is None and side_explanation:
                weight_adjustment_note = f"Reduced to 60% load during {rehab_stage} ({side_explanation})"
        elif prescription_mode == "direct_rehab" and rehab_stage in _MID_REHAB_STAGES:
            min_condition_factor = min(min_condition_factor, 0.75)
            if weight_adjustment_note is None and side_explanation:
                weight_adjustment_note = f"Reduced to 75% load during {rehab_stage} ({side_explanation})"

        target_weight = None
        if current_e1rm > 0:
            intensity = (intensity_range[0] + intensity_range[1]) / 2
            raw_weight = current_e1rm * intensity * min_condition_factor
            # For mixed exercises the e1RM includes bodyweight, but target_weight
            # must represent the *external* load only — subtract the bodyweight
            # component so the suggestion isn't inflated by the user's body mass.
            if exercise.load_input_mode == "mixed" and current_bw > 0:
                bw_component = current_bw * (exercise.bodyweight_fraction or 0.0)
                raw_weight = max(0.0, raw_weight - bw_component)
            if exercise.equipment == "barbell":
                target_weight = round(raw_weight / 5) * 5
            elif exercise.equipment == "dumbbell":
                target_weight = round(raw_weight / 2.5) * 2.5
            else:
                target_weight = round(raw_weight / 5) * 5
            target_weight = max(target_weight, 0)

        last_perf = _get_last_performance(session, exercise_id)
        last_weight = float(last_perf.get("max_weight", 0.0)) if last_perf else 0.0
        last_session_peak_reps = _last_session_peak_reps(last_perf)
        overload_note = None
        if (
            rep_scheme == "heavy"
            and target_weight
            and target_weight > 0
            and last_weight > 0
            and last_session_peak_reps >= _HIGH_REP_STRENGTH_ANCHOR_THRESHOLD
            and weight_adjustment_note is None
        ):
            target_weight = _round_target_weight(
                exercise.equipment,
                _blend_heavy_weight_target(
                    heavy_target=target_weight,
                    recent_weight=last_weight,
                ),
            )
            overload_note = (
                f"Heavy target blends e1RM with your recent {last_session_peak_reps}-rep working weight"
            )
        # Skip progressive overload when a tissue condition restricts loading.
        if not weight_adjustment_note and last_perf and target_weight and target_weight > 0:
            all_full = last_perf.get("all_full", False)
            if (
                rep_scheme == "heavy"
                and last_session_peak_reps >= _HIGH_REP_STRENGTH_ANCHOR_THRESHOLD
            ):
                pass
            elif all_full and last_weight and last_weight >= target_weight:
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
        if prescription_mode == "direct_rehab" and rehab_stage in _EARLY_REHAB_STAGES:
            target_sets = min(target_sets, 3)
        elif prescription_mode == "direct_rehab" and rehab_stage in _MID_REHAB_STAGES:
            target_sets = min(target_sets, 4)

        results.append({
            "exercise_id": exercise.id,
            "exercise_name": ex.get("exercise_name") or ex.get("name") or exercise.name,
            "equipment": exercise.equipment,
            "laterality": exercise.laterality,
            "performed_side": preferred_side,
            "rep_scheme": rep_scheme,
            "target_sets": target_sets,
            "target_reps": target_reps,
            "target_weight": target_weight,
            "rationale": rationale,
            "overload_note": overload_note,
            "weight_adjustment_note": weight_adjustment_note,
            "side_explanation": side_explanation,
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


def _apply_session_heavy_budget(
    *,
    rep_scheme: str,
    target_reps: str,
    intensity_range: tuple[float, float],
    rationale: str,
    primary_regions: set[str],
    heavy_session_count: int,
    session_heavy_limit: int,
    heavy_primary_region_counts: dict[str, int],
) -> tuple[str, str, tuple[float, float], str]:
    if rep_scheme != "heavy":
        return rep_scheme, target_reps, intensity_range, rationale

    over_session_limit = heavy_session_count >= session_heavy_limit
    saturated_regions = sorted(
        region for region in primary_regions
        if heavy_primary_region_counts.get(region, 0) >= _MAX_HEAVY_EXERCISES_PER_PRIMARY_REGION
    )
    if not over_session_limit and not saturated_regions:
        return rep_scheme, target_reps, intensity_range, rationale

    if saturated_regions:
        heavy_note = (
            "Heavy slot already used for "
            + ", ".join(saturated_regions)
            + "; shifting this exercise to a volume prescription."
        )
    else:
        heavy_note = "Session heavy budget is already full; shifting this exercise to a volume prescription."
    return ("volume", "8-12", (0.65, 0.75), f"{rationale} {heavy_note}")


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


def _last_session_peak_reps(last_perf: dict | None) -> int:
    if not last_perf:
        return 0
    return max(
        (
            int(set_data.get("reps") or 0)
            for set_data in last_perf.get("sets", [])
        ),
        default=0,
    )


def _blend_heavy_weight_target(*, heavy_target: float, recent_weight: float) -> float:
    return max(
        recent_weight,
        recent_weight + (heavy_target - recent_weight) * _HEAVY_WEIGHT_BLEND_RATIO,
    )


def _round_target_weight(equipment: str | None, raw_weight: float) -> float:
    if equipment == "barbell":
        return max(round(raw_weight / 5) * 5, 0)
    if equipment == "dumbbell":
        return max(round(raw_weight / 2.5) * 2.5, 0)
    return max(round(raw_weight / 5) * 5, 0)


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


def _planner_preferred_side(
    *,
    exercise: Exercise,
    exercise_summary: dict,
    tracked_lookup: dict[int, object],
    tracked_conditions: dict[int, object],
    active_rehab_plans: dict[int, object],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Pick a preferred performed_side for planner prescriptions.

    Returns ``(preferred_side, explanation, rehab_stage)``.
    ``preferred_side`` may be ``"block"`` when the exercise should be excluded.
    """
    if exercise.laterality == "bilateral":
        return (
            default_performed_side(
                exercise_name=exercise.name,
                exercise_laterality=exercise.laterality,
                provided_side=None,
            ),
            None,
            None,
            None,
        )

    explicit = default_performed_side(
        exercise_name=exercise.name,
        exercise_laterality=exercise.laterality,
        provided_side=None,
    )

    rehab_targets: list[tuple[int, str, str | None]] = []
    for tracked_id, tracked in tracked_lookup.items():
        if getattr(tracked, "side", None) not in {"left", "right"}:
            continue
        condition = tracked_conditions.get(tracked_id)
        rehab_plan = active_rehab_plans.get(tracked_id)
        if rehab_plan is None and not (
            condition and getattr(condition, "status", None) in {"injured", "rehabbing"}
        ):
            continue
        rehab_targets.append((
            getattr(tracked, "tissue_id"),
            getattr(tracked, "side"),
            getattr(rehab_plan, "stage_id", None) if rehab_plan is not None else None,
        ))

    if not rehab_targets:
        if explicit in {"left", "right"}:
            return explicit, f"exercise name indicates the {explicit} side", None, None
        return None, None, None, None

    candidate_sides = [explicit] if explicit in {"left", "right"} else ["left", "right"]
    candidate_metrics: dict[str, dict[str, float | str | None | bool]] = {}

    for candidate_side in candidate_sides:
        candidate_metrics[candidate_side] = {
            "direct": 0.0,
            "cross": 0.0,
            "blocked": False,
            "target_side": None,
            "stage": None,
            "matched": False,
        }
        for tissue_map in exercise_summary.get("tissues", []):
            tissue_id = tissue_map.get("tissue_id")
            if not tissue_id:
                continue
            laterality_mode = tissue_map.get("laterality_mode") or "bilateral_equal"
            routing = float(tissue_map.get("routing_factor") or 0.0)
            for rehab_tissue_id, rehab_side, rehab_stage in rehab_targets:
                if rehab_tissue_id != tissue_id:
                    continue
                load_weights, cross_weights = tracked_tissue_side_weights(
                    exercise_laterality=exercise.laterality,
                    laterality_mode=laterality_mode,
                    performed_side=candidate_side,
                    tissue_tracking_mode="paired",
                )
                direct = routing * float(load_weights.get(rehab_side, 0.0))
                cross = routing * float(cross_weights.get(rehab_side, 0.0))
                candidate_metrics[candidate_side]["matched"] = True
                candidate_metrics[candidate_side]["direct"] = float(candidate_metrics[candidate_side]["direct"]) + direct
                candidate_metrics[candidate_side]["cross"] = float(candidate_metrics[candidate_side]["cross"]) + cross
                if direct >= cross:
                    candidate_metrics[candidate_side]["target_side"] = rehab_side
                    candidate_metrics[candidate_side]["stage"] = rehab_stage
                elif candidate_metrics[candidate_side]["target_side"] is None:
                    candidate_metrics[candidate_side]["target_side"] = rehab_side
                    candidate_metrics[candidate_side]["stage"] = rehab_stage
                if (
                    rehab_stage in _EARLY_REHAB_STAGES
                    and direct >= _TRACKED_DIRECT_PROTECTION_THRESHOLD
                ):
                    candidate_metrics[candidate_side]["blocked"] = True

    relevant_candidates = {
        side: metrics
        for side, metrics in candidate_metrics.items()
        if metrics["matched"] and not metrics["blocked"]
    }

    if not relevant_candidates:
        blocked_target = next(
            ((side, stage) for _tid, side, stage in rehab_targets),
            ("left", None),
        )
        return (
            "block",
            f"{blocked_target[0].title()} rehab tissue is still in a protected stage",
            blocked_target[1],
            "direct_rehab",
        )

    cross_candidates = [
        (side, metrics)
        for side, metrics in relevant_candidates.items()
        if float(metrics["cross"]) >= _TRACKED_CROSS_SUPPORT_THRESHOLD
    ]
    if cross_candidates:
        chosen_side, metrics = max(
            cross_candidates,
            key=lambda item: float(item[1]["cross"]) - float(item[1]["direct"]),
        )
        protected_side = str(metrics["target_side"] or "affected")
        return (
            chosen_side,
            f"uses {chosen_side}-side work for {protected_side}-side cross-education",
            metrics["stage"],
            "cross_education",
        )

    direct_candidates = [
        (side, metrics)
        for side, metrics in relevant_candidates.items()
        if float(metrics["direct"]) >= _TRACKED_DIRECT_PROTECTION_THRESHOLD
    ]
    if direct_candidates:
        chosen_side, metrics = max(
            direct_candidates,
            key=lambda item: float(item[1]["direct"]),
        )
        protected_side = str(metrics["target_side"] or chosen_side)
        explanation = (
            f"exercise name indicates the {chosen_side} side"
            if explicit == chosen_side
            else f"targets the protected {protected_side} side directly"
        )
        return chosen_side, explanation, metrics["stage"], "direct_rehab"

    if explicit in relevant_candidates:
        return explicit, f"exercise name indicates the {explicit} side", relevant_candidates[explicit]["stage"], None
    return None, None, None, None


def _apply_rehab_stage_prescription(
    *,
    rep_scheme: str,
    target_reps: str,
    intensity_range: tuple[float, float],
    rationale: str,
    rehab_stage: str | None,
    side_explanation: str | None,
    prescription_mode: str | None,
) -> tuple[str, str, tuple[float, float], str]:
    if prescription_mode == "direct_rehab" and rehab_stage in _EARLY_REHAB_STAGES:
        note = "Protected rehab stage; keep the dose light and symptom-gated."
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("light", "12-20", (0.45, 0.6), note)
    if prescription_mode == "direct_rehab" and rehab_stage in _MID_REHAB_STAGES:
        note = "Rehab stage favors controlled rep progression before load."
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("volume", "8-15", (0.55, 0.7), note)
    if prescription_mode == "direct_rehab" and rehab_stage in _LATE_REHAB_STAGES and rep_scheme == "heavy":
        note = rationale
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("volume", "6-10", (0.65, 0.8), note)
    if prescription_mode == "cross_education":
        note = "Cross-education support should stay high-intent without counting as local rehab loading."
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("heavy", "3-8", (0.75, 0.85), note)
    if side_explanation:
        return rep_scheme, target_reps, intensity_range, f"{rationale} {side_explanation.capitalize()}."
    return rep_scheme, target_reps, intensity_range, rationale


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
