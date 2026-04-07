"""Auto-generating workout planner.

Selects which muscle groups to train today based on tissue readiness,
recovery check-ins, and injury status. Matches exercises to recovery
regions through tissue-region associations. Supports saving plans to DB
and tracking progress through a workout.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.exercise_history import (
    REP_SCHEME_VERSION,
    build_scheme_history,
    empty_scheme_history,
    get_exercise_history_map,
    get_exercise_scheme_history_map,
)
from app.exercise_loads import bodyweight_by_date, entered_weight_for_effective_weight, latest_bodyweight, load_progression_direction
from app.exercise_protection import (
    EARLY_REHAB_STAGES as _EARLY_REHAB_STAGES,
)
from app.exercise_protection import (
    LATE_REHAB_STAGES as _LATE_REHAB_STAGES,
)
from app.exercise_protection import (
    MID_REHAB_STAGES as _MID_REHAB_STAGES,
)
from app.exercise_protection import (
    build_tracked_protection_profiles,
    evaluate_exercise_protection,
)
from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    RegionSorenessCheckIn,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
    WorkoutSetTissueFeedback,
)
from app.planner_groups import significant_mapping_load
from app.recovery_check_ins import aggregate_recovery_checkins_for_day
from app.tissue_regions import load_tissue_regions
from app.tracked_tissues import (
    default_performed_side,
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_tracked_tissue_lookup,
    tracked_tissue_side_weights,
)
from app.training_model import build_exercise_strength

# ── Tissue clusters ──────────────────────────────────────────────────

TISSUE_CLUSTERS: list[dict] = [
    {"label": "Push", "regions": ["chest", "shoulders", "triceps"]},
    {"label": "Pull", "regions": ["upper_back", "biceps", "forearms"]},
    {"label": "Legs", "regions": ["quads", "hamstrings", "glutes", "calves", "shins"]},
    {"label": "Core & Posterior", "regions": ["core", "lower_back", "glutes"]},
]

REST_DAY_THRESHOLD = 0.35
MAX_CANDIDATES = 16
DEFAULT_SELECTED = 8
AUTO_PROGRAM_NAME = "__auto_plan__"
_MAX_REHAB_PRIORITY_CANDIDATES = 3
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
_CROSS_EDUCATION_ALLOWED_STAGES = _EARLY_REHAB_STAGES | _MID_REHAB_STAGES | {"high-intent-support"}


# ── Main entry point ─────────────────────────────────────────────────


def suggest_today(session: Session, *, as_of: date | None = None) -> dict:
    """Return the workflow-based workout planner for today and tomorrow."""
    from app.planner_workflow import suggest_today_workflow

    return suggest_today_workflow(session, as_of=as_of)


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
                "rep_scheme_version": (
                    REP_SCHEME_VERSION if ex.get("rep_scheme") else None
                ),
                "target_weight": ex.get("target_weight"),
                "performed_side": ex.get("performed_side"),
                "side_explanation": ex.get("side_explanation"),
                "selection_note": ex.get("selection_note"),
                "blocked_variant": ex.get("blocked_variant"),
                "protected_tissues": ex.get("protected_tissues"),
                "workflow_role": ex.get("workflow_role"),
                "group_label": ex.get("group_label"),
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
                "rep_scheme_version": (
                    REP_SCHEME_VERSION if ex.get("rep_scheme") else None
                ),
                "target_weight": ex.get("target_weight"),
                "performed_side": ex.get("performed_side"),
                "side_explanation": ex.get("side_explanation"),
                "selection_note": ex.get("selection_note"),
                "blocked_variant": ex.get("blocked_variant"),
                "protected_tissues": ex.get("protected_tissues"),
                "workflow_role": ex.get("workflow_role"),
                "group_label": ex.get("group_label"),
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
    scheme_history_by_exercise = get_exercise_scheme_history_map(
        session,
        [pde.exercise_id for pde in day_exercises],
        limit=40,
    )
    if planned.workout_session_id:
        sets = session.exec(
            select(WorkoutSet)
            .where(WorkoutSet.session_id == planned.workout_session_id)
            .order_by(WorkoutSet.set_order)
        ).all()
        feedback_rows = session.exec(select(WorkoutSetTissueFeedback)).all()
        feedback_by_set: dict[int, list[WorkoutSetTissueFeedback]] = defaultdict(list)
        for row in feedback_rows:
            feedback_by_set[row.workout_set_id].append(row)
        for s in sets:
            logged_sets[s.exercise_id].append({
                "id": s.id,
                "set_order": s.set_order,
                "performed_side": s.performed_side,
                "reps": s.reps,
                "weight": s.weight,
                "duration_secs": s.duration_secs,
                "distance_steps": s.distance_steps,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "rpe": s.rpe,
                "rep_completion": s.rep_completion,
                "notes": s.notes,
                "tissue_feedback": [
                    {
                        "tracked_tissue_id": row.tracked_tissue_id,
                        "pain_0_10": row.pain_0_10,
                        "symptom_note": row.symptom_note,
                        "recorded_at": row.recorded_at,
                    }
                    for row in feedback_by_set.get(s.id or 0, [])
                ],
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
            "selection_note": meta.get("selection_note"),
            "blocked_variant": meta.get("blocked_variant"),
            "protected_tissues": meta.get("protected_tissues") or [],
            "workflow_role": meta.get("workflow_role"),
            "group_label": meta.get("group_label"),
            "scheme_history": scheme_history_by_exercise.get(
                pde.exercise_id,
                empty_scheme_history(),
            ),
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
    soreness_rows = session.exec(
        select(RegionSorenessCheckIn).where(RegionSorenessCheckIn.date == today)
    ).all()
    return aggregate_recovery_checkins_for_day([*rows, *soreness_rows], today)


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
        sore = ci["soreness_0_10"]
        if sore >= 7:
            region_recovery[region] = [min(v * 0.6, 0.5) for v in region_recovery[region]]
            region_risk[region] = [max(v, 60) for v in region_risk[region]]
        elif sore >= 4:
            region_recovery[region] = [min(v * 0.8, 0.72) for v in region_recovery[region]]
            region_risk[region] = [max(v, 45) for v in region_risk[region]]
        elif sore >= 2:
            region_recovery[region] = [min(v * 0.92, 0.9) for v in region_recovery[region]]

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
    return blocked


def _soft_blocked_regions(checkins: dict[str, dict]) -> set[str]:
    blocked = set()
    for region, ci in checkins.items():
        if ci["soreness_0_10"] >= 7:
            blocked.add(region)
    return blocked


# ── Exercise → region mapping ────────────────────────────────────────


def _build_exercise_region_map(session: Session) -> dict[int, list[dict]]:
    """Build exercise_id → list of {region, role, routing_factor} from DB."""
    rows = session.exec(
        select(
            ExerciseTissue.exercise_id,
            ExerciseTissue.tissue_id,
            ExerciseTissue.role,
            ExerciseTissue.routing_factor,
        )
    ).all()
    regions_by_tissue = load_tissue_regions(
        session,
        tissue_ids={tissue_id for _, tissue_id, _, _ in rows},
    )

    result: dict[int, list[dict]] = defaultdict(list)
    for exercise_id, tissue_id, role, routing in rows:
        for region in regions_by_tissue.get(tissue_id, ()):
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
    rehab_priorities: dict[int, dict] | None = None,
    protection_profiles: dict[int, list[object]] | None = None,
    soft_blocked_regions: set[str] | None = None,
) -> list[dict]:
    """Select up to MAX_CANDIDATES exercises, prioritizing target regions.

    Returns candidates sorted by relevance. First DEFAULT_SELECTED are marked
    ``selected=True``; the rest are ``selected=False`` so the UI can show
    checkboxes.
    """
    primary_candidates: list[dict] = []
    adjacent_candidates: list[dict] = []
    rehab_candidates: list[dict] = []
    rehab_priorities = rehab_priorities or {}
    protection_profiles = protection_profiles or {}
    soft_blocked_regions = soft_blocked_regions or set()
    best_blocked_by_variant_group: dict[str, dict] = {}

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
        primary_region_hits = {
            m["region"] for m in region_mappings if m["role"] == "primary"
        }
        mapped_regions = {m["region"] for m in region_mappings}
        rehab_priority = rehab_priorities.get(exercise_id)
        protection_eval = evaluate_exercise_protection(
            ex,
            ex,
            protection_profiles,
            preferred_side=(
                rehab_priority.get("preferred_side")
                if rehab_priority
                else None
            ),
        )

        # Skip if any PRIMARY mapping is in a blocked region
        has_blocked_primary = any(
            m["region"] in blocked_regions and m["role"] == "primary"
            for m in region_mappings
        )
        if has_blocked_primary:
            continue
        has_soft_blocked_primary = any(
            m["region"] in soft_blocked_regions and m["role"] == "primary"
            for m in region_mappings
        )
        if has_soft_blocked_primary and not rehab_priority:
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
        fatigue_factor = max(fatigue_factor, 0.0)

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

        rec_bonus = 1.0 if rec == "good" else 0.5
        # Normalize suitability to [0, 1] to deprioritize exercises that load
        # fatigued or at-risk tissues (lower suitability = more tissue risk).
        suitability_norm = min(ex.get("suitability_score", 70) / 100.0, 1.0)
        if not target_hits and not adj_hits and not rehab_priority:
            continue
        selection_reason = protection_eval.get("gating_reason")
        protected_tissues = protection_eval.get("protected_tissues", [])
        if protection_eval["blocked"]:
            variant_group = ex.get("variant_group")
            if variant_group:
                blocked_record = {
                    **ex,
                    "target_hits": set(target_hits) if target_hits else set(adj_hits),
                    "primary_regions": set(target_primary_hits) or set(adj_primary_hits) or primary_region_hits or mapped_regions,
                    "blocked_variant": ex.get("name"),
                    "protected_tissues": protected_tissues,
                    "gating_reason": selection_reason,
                    "gating_code": protection_eval.get("gating_code"),
                    "selection_mode": rehab_priority.get("mode") if rehab_priority else None,
                    "performed_side": protection_eval.get("preferred_side"),
                }
                current = best_blocked_by_variant_group.get(variant_group)
                if current is None or _blocked_reason_priority(blocked_record) > _blocked_reason_priority(current):
                    best_blocked_by_variant_group[variant_group] = blocked_record
            continue

        protection_bonus = float(protection_eval.get("score_bonus") or 0.0)
        if rehab_priority:
            priority_load = min(float(rehab_priority.get("priority_load") or 0.0), 1.0)
            priority_bonus = 0.6 if rehab_priority.get("mode") == "direct_rehab" else 0.48
            score = (
                priority_bonus
                + rec_bonus * 0.15
                + priority_load * 0.2
                + suitability_norm * 0.15
                + protection_bonus
            ) * fatigue_factor
            rehab_candidates.append({
                **ex,
                "target_hits": primary_region_hits or mapped_regions,
                "primary_regions": primary_region_hits or mapped_regions,
                "selection_score": score,
                "selection_mode": rehab_priority.get("mode"),
                "gating_reason": selection_reason,
                "gating_code": protection_eval.get("gating_code"),
                "protected_tissues": protected_tissues,
                "performed_side": (
                    rehab_priority.get("preferred_side")
                    or protection_eval.get("preferred_side")
                ),
            })

        if target_hits:
            coverage = len(set(target_hits)) / max(len(target_regions), 1)
            score = (
                coverage * 0.35
                + rec_bonus * 0.25
                + min(target_routing, 1.0) * 0.25
                + suitability_norm * 0.15
                + protection_bonus
            ) * fatigue_factor
            primary_candidates.append({
                **ex,
                "target_hits": set(target_hits),
                "primary_regions": set(target_primary_hits) or set(target_hits),
                "selection_score": score,
                "gating_reason": selection_reason,
                "gating_code": protection_eval.get("gating_code"),
                "protected_tissues": protected_tissues,
                "performed_side": protection_eval.get("preferred_side"),
            })
        else:
            coverage = len(set(adj_hits)) / max(len(adjacent_regions), 1)
            score = (
                coverage * 0.25
                + rec_bonus * 0.25
                + min(adj_routing, 1.0) * 0.20
                + suitability_norm * 0.10
                + protection_bonus
            ) * fatigue_factor
            adjacent_candidates.append({
                **ex,
                "target_hits": set(adj_hits),
                "primary_regions": set(adj_primary_hits) or set(adj_hits),
                "selection_score": score,
                "gating_reason": selection_reason,
                "gating_code": protection_eval.get("gating_code"),
                "protected_tissues": protected_tissues,
                "performed_side": protection_eval.get("preferred_side"),
            })

    # Sort each pool by score descending
    rehab_candidates.sort(key=lambda x: x["selection_score"], reverse=True)
    primary_candidates.sort(key=lambda x: x["selection_score"], reverse=True)
    adjacent_candidates.sort(key=lambda x: x["selection_score"], reverse=True)

    # Fill rehab-first, then primary, then adjacent, up to MAX_CANDIDATES.
    combined: list[dict] = []
    seen_exercise_ids: set[int] = set()

    def append_pool(candidates: list[dict], *, limit: int | None = None) -> None:
        taken = 0
        for candidate in candidates:
            candidate_id = candidate.get("exercise_id") or candidate.get("id")
            if not candidate_id or candidate_id in seen_exercise_ids:
                continue
            combined.append(candidate)
            seen_exercise_ids.add(candidate_id)
            taken += 1
            if len(combined) >= MAX_CANDIDATES:
                break
            if limit is not None and taken >= limit:
                break

    append_pool(rehab_candidates, limit=_MAX_REHAB_PRIORITY_CANDIDATES)
    if len(combined) < MAX_CANDIDATES:
        append_pool(primary_candidates)
    if len(combined) < MAX_CANDIDATES:
        append_pool(adjacent_candidates)

    for variant_group, blocked in best_blocked_by_variant_group.items():
        substitute = next(
            (
                candidate
                for candidate in combined
                if candidate.get("variant_group") == variant_group
                and (candidate.get("exercise_id") or candidate.get("id"))
                != (blocked.get("exercise_id") or blocked.get("id"))
            ),
            None,
        )
        if not substitute:
            continue
        substitute["blocked_variant"] = blocked.get("blocked_variant")
        substitute["gating_reason"] = blocked.get("gating_reason") or substitute.get("gating_reason")
        substitute["gating_code"] = blocked.get("gating_code") or substitute.get("gating_code")
        merged_tissues = list(
            dict.fromkeys(
                list(substitute.get("protected_tissues", []))
                + list(blocked.get("protected_tissues", []))
            )
        )
        substitute["protected_tissues"] = merged_tissues[:5]
        substitute["selection_note"] = _build_selection_note(
            blocked_variant=blocked.get("blocked_variant"),
            substitute_variant=substitute.get("name"),
            gating_reason=substitute.get("gating_reason"),
            protected_tissues=substitute.get("protected_tissues", []),
        )

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
    protection_profiles = build_tracked_protection_profiles(session, as_of=today)

    # Load current bodyweight for mixed/bodyweight exercise adjustments
    weight_rows = list(
        session.exec(select(WeightLog).order_by(col(WeightLog.logged_at).asc())).all()
    )
    bw_by_date_map = bodyweight_by_date(
        [r for r in weight_rows if r.logged_at.date() <= today]
    )
    current_bw = latest_bodyweight(bw_by_date_map, today)
    exercise_ids = [
        int(exercise_id)
        for exercise in exercises
        for exercise_id in [exercise.get("exercise_id") or exercise.get("id")]
        if exercise_id is not None
    ]
    history_by_exercise = get_exercise_history_map(
        session,
        exercise_ids,
        limit=40,
    )
    scheme_history_by_exercise = {
        exercise_id: build_scheme_history(session_rows)
        for exercise_id, session_rows in history_by_exercise.items()
    }
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
    heavy_tissue_counts: defaultdict[int, int] = defaultdict(int)
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
        if preferred_side is None and ex.get("performed_side") in {"left", "right", "center", "bilateral"}:
            preferred_side = ex.get("performed_side")
        if side_explanation is None and ex.get("side_explanation"):
            side_explanation = ex.get("side_explanation")
        if preferred_side == "block":
            continue
        protection_eval = evaluate_exercise_protection(
            exercise,
            ex,
            protection_profiles,
            preferred_side=preferred_side,
            estimated_sets=3,
        )
        if protection_eval["blocked"]:
            continue
        if side_explanation is None and protection_eval.get("side_explanation"):
            side_explanation = protection_eval.get("side_explanation")
        selection_note = ex.get("selection_note")
        if not selection_note and (ex.get("blocked_variant") or ex.get("gating_reason")):
            selection_note = _build_selection_note(
                blocked_variant=ex.get("blocked_variant"),
                substitute_variant=ex.get("name") or exercise.name,
                gating_reason=ex.get("gating_reason"),
                protected_tissues=list(ex.get("protected_tissues", [])),
            )

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
        rep_scheme, target_reps, intensity_range, rationale = _apply_exercise_heavy_gate(
            rep_scheme=rep_scheme,
            target_reps=target_reps,
            intensity_range=intensity_range,
            rationale=rationale,
            exercise=exercise,
        )
        primary_regions = set(ex.get("primary_regions", ex.get("target_hits", set())))
        significant_tissue_ids = {
            int(tm["tissue_id"])
            for tm in ex.get("tissues", [])
            if tm.get("tissue_id") is not None and significant_mapping_load(tm) >= 0.3
        }
        rep_scheme, target_reps, intensity_range, rationale = _apply_session_heavy_budget(
            rep_scheme=rep_scheme,
            target_reps=target_reps,
            intensity_range=intensity_range,
            rationale=rationale,
            primary_regions=primary_regions,
            significant_tissue_ids=significant_tissue_ids,
            heavy_session_count=heavy_session_count,
            session_heavy_limit=session_heavy_limit,
            heavy_primary_region_counts=heavy_primary_region_counts,
            heavy_tissue_counts=heavy_tissue_counts,
        )
        if rep_scheme == "heavy":
            heavy_session_count += 1
            for region in primary_regions:
                heavy_primary_region_counts[region] += 1
            for tissue_id in significant_tissue_ids:
                heavy_tissue_counts[tissue_id] += 1

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
            effective_target_weight = current_e1rm * intensity * min_condition_factor
            entered_target = entered_weight_for_effective_weight(
                exercise,
                effective_weight_lb=effective_target_weight,
                bodyweight_lb=current_bw,
            )
            if entered_target is not None:
                target_weight = _round_target_weight(
                    exercise.equipment,
                    entered_target,
                )

        last_perf = (history_by_exercise.get(exercise_id) or [None])[0]
        last_weight = _last_session_peak_weight(last_perf)
        last_session_peak_reps = _last_session_peak_reps(last_perf)
        overload_note = None
        progression_direction = load_progression_direction(exercise)
        used_high_rep_blend = False
        if (
            rep_scheme == "heavy"
            and target_weight
            and target_weight > 0
            and last_weight > 0
            and last_session_peak_reps >= _HIGH_REP_STRENGTH_ANCHOR_THRESHOLD
            and weight_adjustment_note is None
        ):
            used_high_rep_blend = True
            blended_target = _blend_heavy_weight_target(
                heavy_target=target_weight,
                recent_weight=last_weight,
                progression_direction=progression_direction,
            )
            target_weight = _round_target_weight(
                exercise.equipment,
                blended_target,
            )
            increment = _weight_increment(exercise.equipment)
            if progression_direction < 0 and target_weight >= last_weight and blended_target < last_weight:
                target_weight = max(0.0, last_weight - increment)
            elif progression_direction > 0 and target_weight <= last_weight and blended_target > last_weight:
                target_weight = last_weight + increment
            overload_note = (
                f"Heavy target blends e1RM with your recent {last_session_peak_reps}-rep working weight"
            )
        # Skip progressive overload when a tissue condition restricts loading.
        if not weight_adjustment_note and last_perf and target_weight and target_weight > 0:
            all_full = last_perf.get("all_full", False)
            progressed_past_last = (
                (progression_direction > 0 and target_weight > last_weight)
                or (progression_direction < 0 and target_weight < last_weight)
            )
            if (
                used_high_rep_blend
                and all_full
                and last_weight
                and not progressed_past_last
            ):
                increment = _weight_increment(exercise.equipment)
                target_weight = max(0.0, last_weight + increment * progression_direction)
                overload_note = (
                    f"-{increment} lbs assist (progressive overload)"
                    if progression_direction < 0
                    else f"+{increment} lbs (progressive overload)"
                )
            elif used_high_rep_blend:
                pass
            elif (
                all_full
                and last_weight
                and (
                    (progression_direction > 0 and last_weight >= target_weight)
                    or (progression_direction < 0 and last_weight <= target_weight)
                )
            ):
                increment = _weight_increment(exercise.equipment)
                target_weight = max(0.0, last_weight + increment * progression_direction)
                overload_note = (
                    f"-{increment} lbs assist (progressive overload)"
                    if progression_direction < 0
                    else f"+{increment} lbs (progressive overload)"
                )
            elif not all_full and last_weight:
                target_weight = last_weight
                overload_note = (
                    "Same assist, aim for full completion"
                    if progression_direction < 0
                    else "Same weight, aim for full completion"
                )

        if rep_scheme == "heavy":
            target_sets = 3
        elif rep_scheme == "medium":
            target_sets = 2 if weighted_risk >= 55 else 3
        else:
            target_sets = 3 if recommendation == "caution" else 4
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
            "selection_note": selection_note,
            "blocked_variant": ex.get("blocked_variant"),
            "protected_tissues": ex.get("protected_tissues", []),
            "workflow_role": ex.get("workflow_role"),
            "group_label": ex.get("group_label"),
            "current_e1rm": round(current_e1rm, 2) if current_e1rm else None,
            "selected": ex.get("selected", True),
            "last_performance": last_perf,
            "scheme_history": scheme_history_by_exercise.get(
                exercise_id,
                empty_scheme_history(),
            ),
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
        return ("volume", "15-20", (0.50, 0.60), "Exercise is currently in the avoid band.")
    if recommendation == "caution" and weighted_risk_7d >= 50:
        return ("volume", "12-15", (0.50, 0.60), "Caution flag and elevated risk; use a controlled volume dose.")
    if suitability >= 0.8 and days_since_heavy >= 5 and recommendation == "good":
        return ("heavy", "3-5", (0.80, 0.85), "Well-recovered; strength focus.")
    if suitability >= 0.6:
        return ("medium", "8-12", (0.65, 0.75), "Moderate recovery; moderate loading.")
    return ("volume", "12-20", (0.50, 0.65), "Low readiness; use a higher-rep volume dose.")


def _apply_exercise_heavy_gate(
    *,
    rep_scheme: str,
    target_reps: str,
    intensity_range: tuple[float, float],
    rationale: str,
    exercise: Exercise,
) -> tuple[str, str, tuple[float, float], str]:
    if rep_scheme != "heavy" or exercise.allow_heavy_loading:
        return rep_scheme, target_reps, intensity_range, rationale
    return (
        "medium",
        "8-12",
        (0.65, 0.75),
        f"{rationale} Heavy loading is disabled for this exercise, so it shifts to a medium prescription.",
    )


def _apply_session_heavy_budget(
    *,
    rep_scheme: str,
    target_reps: str,
    intensity_range: tuple[float, float],
    rationale: str,
    primary_regions: set[str],
    significant_tissue_ids: set[int],
    heavy_session_count: int,
    session_heavy_limit: int,
    heavy_primary_region_counts: dict[str, int],
    heavy_tissue_counts: dict[int, int],
) -> tuple[str, str, tuple[float, float], str]:
    if rep_scheme != "heavy":
        return rep_scheme, target_reps, intensity_range, rationale

    over_session_limit = heavy_session_count >= session_heavy_limit
    saturated_regions = sorted(
        region for region in primary_regions
        if heavy_primary_region_counts.get(region, 0) >= _MAX_HEAVY_EXERCISES_PER_PRIMARY_REGION
    )
    saturated_tissues = [
        tissue_id
        for tissue_id in significant_tissue_ids
        if heavy_tissue_counts.get(tissue_id, 0) >= 1
    ]
    if not over_session_limit and not saturated_regions and not saturated_tissues:
        return rep_scheme, target_reps, intensity_range, rationale

    if saturated_regions:
        heavy_note = (
            "Heavy slot already used for "
            + ", ".join(saturated_regions)
            + "; shifting this exercise to a medium prescription."
        )
    elif saturated_tissues:
        heavy_note = (
            "Heavy slot already used on shared tissues in this session; shifting this exercise to a medium prescription."
        )
    else:
        heavy_note = "Session heavy budget is already full; shifting this exercise to a medium prescription."
    return ("medium", "8-12", (0.65, 0.75), f"{rationale} {heavy_note}")


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


def _last_session_peak_weight(last_perf: dict | None) -> float:
    if not last_perf:
        return 0.0
    return max(
        (
            float(set_data.get("weight") or 0.0)
            for set_data in last_perf.get("sets", [])
        ),
        default=0.0,
    )


def _blend_heavy_weight_target(*, heavy_target: float, recent_weight: float, progression_direction: int = 1) -> float:
    blended = recent_weight + (heavy_target - recent_weight) * _HEAVY_WEIGHT_BLEND_RATIO
    if progression_direction < 0:
        return min(recent_weight, blended)
    return max(recent_weight, blended)


def _weight_increment(equipment: str | None) -> float:
    return 2.5 if equipment == "dumbbell" else 5.0


def _round_target_weight(
    equipment: str | None,
    raw_weight: float,
) -> float:
    increment = _weight_increment(equipment)
    return max(round(raw_weight / increment) * increment, 0)


def _blocked_reason_priority(candidate: dict) -> tuple[int, float]:
    reason = str(candidate.get("gating_code") or "")
    priority = {
        "session_budget_exhausted": 5,
        "during_workout_pain_threshold": 4,
        "symptom_ceiling": 3,
        "loading_cap": 2,
        "protected_variant_required": 1,
    }.get(reason, 0)
    score = float(candidate.get("selection_score") or 0.0)
    return priority, score


def _build_selection_note(
    *,
    blocked_variant: str | None,
    substitute_variant: str | None,
    gating_reason: str | None,
    protected_tissues: list[str],
) -> str | None:
    if not blocked_variant and not gating_reason:
        return None
    tissue_note = ""
    if protected_tissues:
        tissue_note = f" for {', '.join(protected_tissues[:2])}"
    if blocked_variant and substitute_variant and blocked_variant != substitute_variant:
        if gating_reason:
            return f"Swapped {blocked_variant} to {substitute_variant} because {gating_reason}{tissue_note}."
        return f"Swapped {blocked_variant} to {substitute_variant}{tissue_note}."
    if gating_reason:
        return f"Adjusted selection because {gating_reason}{tissue_note}."
    return None


def _get_last_performance(session: Session, exercise_id: int) -> dict | None:
    return (get_exercise_history_map(session, [exercise_id], limit=1).get(exercise_id) or [None])[0]


# ── Helpers ──────────────────────────────────────────────────────────


def _collect_rehab_targets(
    *,
    tracked_lookup: dict[int, object],
    tracked_conditions: dict[int, object],
    active_rehab_plans: dict[int, object],
) -> list[dict[str, object]]:
    rehab_targets: list[dict[str, object]] = []
    for tracked_id, tracked in tracked_lookup.items():
        side = getattr(tracked, "side", None)
        if side not in {"left", "right"}:
            continue
        condition = tracked_conditions.get(tracked_id)
        rehab_plan = active_rehab_plans.get(tracked_id)
        if rehab_plan is None and not (
            condition and getattr(condition, "status", None) in {"injured", "rehabbing"}
        ):
            continue
        rehab_targets.append({
            "tracked_id": tracked_id,
            "tissue_id": getattr(tracked, "tissue_id"),
            "side": side,
            "stage": getattr(rehab_plan, "stage_id", None) if rehab_plan is not None else None,
            "display_name": getattr(tracked, "display_name", None),
        })
    return rehab_targets


def _rehab_candidate_metrics(
    *,
    exercise: Exercise,
    exercise_summary: dict,
    rehab_targets: list[dict[str, object]],
) -> tuple[str | None, dict[str, dict[str, object]]]:
    explicit_side = default_performed_side(
        exercise_name=exercise.name,
        exercise_laterality=exercise.laterality,
        provided_side=None,
    )
    candidate_sides = [explicit_side] if explicit_side in {"left", "right"} else ["left", "right"]
    candidate_metrics: dict[str, dict[str, object]] = {}

    for candidate_side in candidate_sides:
        candidate_metrics[candidate_side] = {
            "direct": 0.0,
            "cross": 0.0,
            "blocked": False,
            "direct_target_side": None,
            "direct_stage": None,
            "direct_target_load": 0.0,
            "cross_target_side": None,
            "cross_stage": None,
            "cross_target_load": 0.0,
            "matched": False,
        }
        for tissue_map in exercise_summary.get("tissues", []):
            tissue_id = tissue_map.get("tissue_id")
            if not tissue_id:
                continue
            laterality_mode = tissue_map.get("laterality_mode") or "bilateral_equal"
            routing = float(tissue_map.get("routing_factor") or 0.0)
            for rehab_target in rehab_targets:
                if rehab_target["tissue_id"] != tissue_id:
                    continue
                rehab_side = str(rehab_target["side"])
                rehab_stage = rehab_target["stage"]
                load_weights, cross_weights = tracked_tissue_side_weights(
                    exercise_laterality=exercise.laterality,
                    laterality_mode=laterality_mode,
                    performed_side=candidate_side,
                    tissue_tracking_mode="paired",
                )
                direct = routing * float(load_weights.get(rehab_side, 0.0))
                cross = routing * float(cross_weights.get(rehab_side, 0.0))
                metrics = candidate_metrics[candidate_side]
                metrics["matched"] = True
                metrics["direct"] = float(metrics["direct"]) + direct
                metrics["cross"] = float(metrics["cross"]) + cross
                if direct > float(metrics["direct_target_load"]):
                    metrics["direct_target_side"] = rehab_side
                    metrics["direct_stage"] = rehab_stage
                    metrics["direct_target_load"] = direct
                if cross > float(metrics["cross_target_load"]):
                    metrics["cross_target_side"] = rehab_side
                    metrics["cross_stage"] = rehab_stage
                    metrics["cross_target_load"] = cross
                if (
                    rehab_stage in _EARLY_REHAB_STAGES
                    and direct >= _TRACKED_DIRECT_PROTECTION_THRESHOLD
                ):
                    metrics["blocked"] = True

    return explicit_side, candidate_metrics


def _choose_rehab_preferred_side(
    *,
    explicit_side: str | None,
    candidate_metrics: dict[str, dict[str, object]],
    rehab_targets: list[dict[str, object]],
) -> tuple[str | None, str | None, str | None, str | None]:
    relevant_candidates = {
        side: metrics
        for side, metrics in candidate_metrics.items()
        if metrics["matched"] and not metrics["blocked"]
    }

    if not relevant_candidates:
        blocked_target = next(
            ((str(target["side"]), target["stage"]) for target in rehab_targets),
            ("left", None),
        )
        return (
            "block",
            f"{blocked_target[0].title()} rehab tissue is still in a protected stage",
            blocked_target[1],
            "direct_rehab",
        )

    explicit_cross_support = any(
        target["stage"] == "high-intent-support" for target in rehab_targets
    )
    if explicit_cross_support:
        cross_candidates = [
            (side, metrics)
            for side, metrics in relevant_candidates.items()
            if (
                float(metrics["cross"]) >= _TRACKED_CROSS_SUPPORT_THRESHOLD
                and metrics["cross_target_side"] is not None
                and metrics["cross_target_side"] != side
            )
        ]
        if cross_candidates:
            chosen_side, metrics = max(
                cross_candidates,
                key=lambda item: float(item[1]["cross"]) - float(item[1]["direct"]),
            )
            protected_side = str(metrics["cross_target_side"] or "affected")
            return (
                chosen_side,
                f"uses {chosen_side}-side work for {protected_side}-side cross-education",
                metrics["cross_stage"],
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
        protected_side = str(metrics["direct_target_side"] or chosen_side)
        explanation = (
            f"exercise name indicates the {chosen_side} side"
            if explicit_side == chosen_side
            else f"targets the protected {protected_side} side directly"
        )
        return chosen_side, explanation, metrics["direct_stage"], "direct_rehab"

    cross_candidates = [
        (side, metrics)
        for side, metrics in relevant_candidates.items()
        if (
            float(metrics["cross"]) >= _TRACKED_CROSS_SUPPORT_THRESHOLD
            and metrics["cross_target_side"] is not None
            and metrics["cross_target_side"] != side
            and metrics["cross_stage"] in _CROSS_EDUCATION_ALLOWED_STAGES
        )
    ]
    if cross_candidates:
        chosen_side, metrics = max(
            cross_candidates,
            key=lambda item: float(item[1]["cross"]) - float(item[1]["direct"]),
        )
        protected_side = str(metrics["cross_target_side"] or "affected")
        return (
            chosen_side,
            f"uses {chosen_side}-side work for {protected_side}-side cross-education",
            metrics["cross_stage"],
            "cross_education",
        )

    if explicit_side in relevant_candidates:
        return (
            explicit_side,
            f"exercise name indicates the {explicit_side} side",
            relevant_candidates[explicit_side]["direct_stage"]
            or relevant_candidates[explicit_side]["cross_stage"],
            None,
        )
    return None, None, None, None


def _build_rehab_priority_map(
    *,
    session: Session,
    exercises_data: list[dict],
    tracked_lookup: dict[int, object],
    tracked_conditions: dict[int, object],
    active_rehab_plans: dict[int, object],
) -> dict[int, dict]:
    rehab_targets = _collect_rehab_targets(
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )
    if not rehab_targets:
        return {}

    priorities: dict[int, dict] = {}
    for ex in exercises_data:
        exercise_id = ex.get("exercise_id") or ex.get("id")
        if not exercise_id:
            continue
        exercise = session.get(Exercise, exercise_id)
        if not exercise or exercise.laterality == "bilateral":
            continue

        explicit_side, candidate_metrics = _rehab_candidate_metrics(
            exercise=exercise,
            exercise_summary=ex,
            rehab_targets=rehab_targets,
        )
        preferred_side, side_explanation, rehab_stage, prescription_mode = _choose_rehab_preferred_side(
            explicit_side=explicit_side,
            candidate_metrics=candidate_metrics,
            rehab_targets=rehab_targets,
        )
        if preferred_side in {None, "block"} or prescription_mode not in {
            "direct_rehab",
            "cross_education",
        }:
            continue

        chosen_metrics = candidate_metrics.get(preferred_side)
        if not chosen_metrics:
            continue
        priority_load = float(
            chosen_metrics["direct"]
            if prescription_mode == "direct_rehab"
            else chosen_metrics["cross"]
        )
        if priority_load <= 0:
            continue

        priorities[exercise_id] = {
            "mode": prescription_mode,
            "preferred_side": preferred_side,
            "side_explanation": side_explanation,
            "rehab_stage": rehab_stage,
            "priority_load": priority_load,
        }
    return priorities


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

    rehab_targets = _collect_rehab_targets(
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )
    if not rehab_targets:
        explicit = default_performed_side(
            exercise_name=exercise.name,
            exercise_laterality=exercise.laterality,
            provided_side=None,
        )
        if explicit in {"left", "right"}:
            return explicit, f"exercise name indicates the {explicit} side", None, None
        return None, None, None, None

    explicit_side, candidate_metrics = _rehab_candidate_metrics(
        exercise=exercise,
        exercise_summary=exercise_summary,
        rehab_targets=rehab_targets,
    )
    return _choose_rehab_preferred_side(
        explicit_side=explicit_side,
        candidate_metrics=candidate_metrics,
        rehab_targets=rehab_targets,
    )


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
        return ("volume", "12-20", (0.45, 0.6), note)
    if prescription_mode == "direct_rehab" and rehab_stage in _MID_REHAB_STAGES:
        note = "Rehab stage favors controlled rep progression before load."
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("medium", "10-15", (0.55, 0.7), note)
    if prescription_mode == "direct_rehab" and rehab_stage in _LATE_REHAB_STAGES and rep_scheme == "heavy":
        note = rationale
        if side_explanation:
            note = f"{note} {side_explanation.capitalize()}."
        return ("medium", "6-10", (0.65, 0.8), note)
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
