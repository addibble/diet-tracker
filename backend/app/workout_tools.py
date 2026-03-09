"""Workout tracking tools for the LLM chat system."""

import difflib
import json
import logging
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    RoutineExercise,
    Tissue,
    TissueCondition,
    WorkoutSession,
    WorkoutSet,
)
from app.workout_queries import (
    get_all_current_conditions,
    get_current_exercise_tissues,
    get_current_tissues,
    get_tissue_tree,
)

logger = logging.getLogger("parse")


# ── Tool Definitions (OpenRouter function calling format) ──

WORKOUT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "log_workout",
            "description": (
                "Log a workout session with exercises, sets, reps, and weights. "
                "Creates exercises if they don't exist. "
                "Returns session_id for rep completion follow-up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "exercises": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string"},
                                "sets": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "reps": {"type": "integer"},
                                            "weight": {"type": "number", "description": "lbs"},
                                            "duration_secs": {"type": "integer"},
                                            "distance_steps": {"type": "integer"},
                                            "rpe": {"type": "number"},
                                            "notes": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            "required": ["exercise_name", "sets"],
                        },
                    },
                    "notes": {"type": "string"},
                },
                "required": ["date", "exercises"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_rep_completion",
            "description": "Set rep_completion and actual reps for exercises in a session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "integer"},
                    "completions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string"},
                                "rep_completion": {
                                    "type": "string",
                                    "enum": ["full", "partial", "failed"],
                                },
                                "reps_per_set": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                            "required": ["exercise_name", "rep_completion"],
                        },
                    },
                },
                "required": ["session_id", "completions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_workout_history",
            "description": "Query past workout sessions with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "exercise_name": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_workout_session",
            "description": "Edit or delete a workout session or individual sets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "integer"},
                    "action": {"type": "string", "enum": ["update", "delete"]},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "notes": {"type": "string"},
                    "add_sets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string"},
                                "reps": {"type": "integer"},
                                "weight": {"type": "number"},
                                "duration_secs": {"type": "integer"},
                                "rpe": {"type": "number"},
                                "notes": {"type": "string"},
                            },
                            "required": ["exercise_name"],
                        },
                    },
                    "remove_set_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["session_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_exercise",
            "description": (
                "Create, update, merge, delete, list, or get exercises. "
                "Use 'get' with a name to see an exercise's tissue mappings. "
                "Use 'list' to see all exercises with their tissue mappings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "merge", "delete", "list", "get"],
                    },
                    "name": {"type": "string"},
                    "new_name": {"type": "string"},
                    "equipment": {"type": "string"},
                    "notes": {"type": "string"},
                    "tissues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {
                                    "type": "string",
                                    "enum": ["primary", "secondary", "stabilizer"],
                                },
                                "loading_factor": {"type": "number"},
                            },
                            "required": ["name", "role", "loading_factor"],
                        },
                    },
                    "merge_into": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_set_exercise_tissues",
            "description": "Set tissue mappings for multiple exercises at once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string"},
                                "tissues": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "role": {"type": "string"},
                                            "loading_factor": {"type": "number"},
                                        },
                                        "required": ["name", "role", "loading_factor"],
                                    },
                                },
                            },
                            "required": ["exercise_name", "tissues"],
                        },
                    },
                },
                "required": ["mappings"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tissue",
            "description": "Create, update, list, or get tree of tissues in the hierarchy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update", "list", "tree"],
                    },
                    "name": {"type": "string"},
                    "display_name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["muscle", "tendon", "joint", "tissue_group"],
                    },
                    "parent_name": {"type": "string"},
                    "recovery_hours": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_tissue_recovery",
            "description": "Update recovery time or notes for a tissue (appends to log).",
            "parameters": {
                "type": "object",
                "properties": {
                    "tissue_name": {"type": "string"},
                    "recovery_hours": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["tissue_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_tissue_condition",
            "description": "Record current condition of a tissue. Drives the injury state machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tissue_name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["healthy", "tender", "injured", "rehabbing"],
                    },
                    "severity": {"type": "integer", "description": "0-4"},
                    "max_loading_factor": {"type": "number"},
                    "recovery_hours_override": {"type": "number"},
                    "rehab_protocol": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["tissue_name", "status", "severity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_tissue_condition",
            "description": "Get condition history for a tissue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tissue_name": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 10"},
                },
                "required": ["tissue_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tissue_readiness",
            "description": "Check which tissues are recovered and ready to train.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_workout",
            "description": (
                "Suggest exercises from routine based on tissue readiness, "
                "conditions, and rep completion history. Includes rehab work."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_routine",
            "description": (
                "Add, update, remove, reorder, or list exercises "
                "in the training routine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "update", "remove", "list", "reorder"],
                    },
                    "exercise_name": {"type": "string"},
                    "target_sets": {"type": "integer"},
                    "target_rep_min": {"type": "integer"},
                    "target_rep_max": {"type": "integer"},
                    "active": {"type": "boolean"},
                    "notes": {"type": "string"},
                    "sort_order": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_exercise_history",
            "description": (
                "Get performance history: max weight, volume trends, "
                "rep completion streaks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default 10 sessions"},
                },
                "required": ["exercise_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_tissue_volume",
            "description": (
                "Analyze weekly training volume (sets x reps x weight x loading_factor) "
                "per tissue over time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tissue_name": {"type": "string", "description": "null = all tissues"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "group_by": {"type": "string", "enum": ["week", "month"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_progression",
            "description": (
                "Suggest weight/rep progression based on rep_completion history "
                "and tissue conditions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {"type": "string"},
                },
                "required": ["exercise_name"],
            },
        },
    },
]


# ── Helpers ──


def _fuzzy_match_exercise(name: str, session: Session) -> Exercise | None:
    """Fuzzy-match an exercise name to the database."""
    exercises = session.exec(select(Exercise)).all()
    names = [e.name for e in exercises]
    matches = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    if matches:
        return session.exec(
            select(Exercise).where(Exercise.name == matches[0])
        ).first()
    # Try case-insensitive exact match
    lower = name.lower().strip()
    for e in exercises:
        if e.name.lower().strip() == lower:
            return e
    return None


def _get_or_create_exercise(name: str, session: Session) -> Exercise:
    """Get existing exercise by fuzzy match or create a new one."""
    existing = _fuzzy_match_exercise(name, session)
    if existing:
        return existing
    exercise = Exercise(name=name)
    session.add(exercise)
    session.flush()
    return exercise


def _find_tissue_by_name(name: str, session: Session) -> Tissue | None:
    """Find a tissue by name (exact or fuzzy)."""
    tissue = session.exec(select(Tissue).where(Tissue.name == name)).first()
    if tissue:
        return tissue
    # Fuzzy match
    tissues = get_current_tissues(session)
    names = [t.name for t in tissues]
    matches = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    if matches:
        return session.exec(
            select(Tissue).where(Tissue.name == matches[0])
        ).first()
    return None


def _set_exercise_tissues(
    exercise: Exercise,
    tissue_mappings: list[dict],
    session: Session,
) -> list[str]:
    """Set tissue mappings for an exercise. Returns list of warnings."""
    warnings = []
    for tm in tissue_mappings:
        tissue = _find_tissue_by_name(tm["name"], session)
        if not tissue:
            warnings.append(f"Tissue '{tm['name']}' not found, skipping")
            continue
        session.add(ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=tissue.id,
            role=tm.get("role", "primary"),
            loading_factor=tm.get("loading_factor", 1.0),
        ))
    return warnings


def _build_session_summary(ws: WorkoutSession, session: Session) -> dict:
    """Build a summary dict for a workout session."""
    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == ws.id)
        .order_by(WorkoutSet.set_order)
    ).all()
    exercises_map: dict[int, list] = {}
    for s in sets:
        exercises_map.setdefault(s.exercise_id, []).append(s)

    exercise_summaries = []
    for eid, ex_sets in exercises_map.items():
        exercise = session.get(Exercise, eid)
        exercise_summaries.append({
            "exercise_name": exercise.name if exercise else f"id:{eid}",
            "sets": [
                {
                    "set_order": s.set_order,
                    "reps": s.reps,
                    "weight": s.weight,
                    "duration_secs": s.duration_secs,
                    "rpe": s.rpe,
                    "rep_completion": s.rep_completion,
                }
                for s in ex_sets
            ],
        })
    return {
        "session_id": ws.id,
        "date": str(ws.date),
        "notes": ws.notes,
        "exercises": exercise_summaries,
    }


def _compute_tissue_readiness(session: Session) -> list[dict]:
    """Compute readiness for all tissues."""
    now = datetime.now(UTC)
    tissues = get_current_tissues(session)
    conditions = {c.tissue_id: c for c in get_all_current_conditions(session)}

    # Get current exercise-tissue mappings
    et_sub = (
        select(
            ExerciseTissue.exercise_id,
            ExerciseTissue.tissue_id,
            func.max(ExerciseTissue.updated_at).label("max_updated"),
        )
        .group_by(ExerciseTissue.exercise_id, ExerciseTissue.tissue_id)
        .subquery()
    )
    current_ets = session.exec(
        select(ExerciseTissue).join(
            et_sub,
            (ExerciseTissue.exercise_id == et_sub.c.exercise_id)
            & (ExerciseTissue.tissue_id == et_sub.c.tissue_id)
            & (ExerciseTissue.updated_at == et_sub.c.max_updated),
        )
    ).all()

    exercise_tissues: dict[int, list[int]] = {}
    for et in current_ets:
        exercise_tissues.setdefault(et.exercise_id, []).append(et.tissue_id)

    # Last trained per tissue
    last_trained_map: dict[int, datetime] = {}
    stmt = (
        select(WorkoutSet.exercise_id, func.max(WorkoutSession.date).label("last_date"))
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .group_by(WorkoutSet.exercise_id)
    )
    for row in session.exec(stmt).all():
        exercise_id, last_date = row
        if exercise_id in exercise_tissues:
            last_dt = datetime(last_date.year, last_date.month, last_date.day, tzinfo=UTC)
            for tissue_id in exercise_tissues[exercise_id]:
                existing = last_trained_map.get(tissue_id)
                if existing is None or last_dt > existing:
                    last_trained_map[tissue_id] = last_dt

    # Propagate to parents
    tissue_by_id = {t.id: t for t in tissues}
    for tissue_id, last_dt in list(last_trained_map.items()):
        t = tissue_by_id.get(tissue_id)
        while t and t.parent_id:
            parent_dt = last_trained_map.get(t.parent_id)
            if parent_dt is None or last_dt > parent_dt:
                last_trained_map[t.parent_id] = last_dt
            t = tissue_by_id.get(t.parent_id)

    result = []
    for t in tissues:
        condition = conditions.get(t.id)
        last_trained = last_trained_map.get(t.id)
        effective_recovery = t.recovery_hours
        if condition and condition.recovery_hours_override is not None:
            effective_recovery = condition.recovery_hours_override

        hours_since = None
        recovery_pct = 100.0
        ready = True
        if last_trained:
            hours_since = (now - last_trained).total_seconds() / 3600
            if effective_recovery > 0:
                recovery_pct = min(100.0, (hours_since / effective_recovery) * 100)
            else:
                recovery_pct = 100.0
            ready = recovery_pct >= 100.0

        if condition and condition.status == "injured":
            ready = False

        result.append({
            "tissue_name": t.name,
            "display_name": t.display_name,
            "type": t.type,
            "recovery_pct": round(recovery_pct, 1),
            "ready": ready,
            "hours_since": round(hours_since, 1) if hours_since is not None else None,
            "effective_recovery_hours": effective_recovery,
            "condition": condition.status if condition else "healthy",
            "severity": condition.severity if condition else 0,
            "max_loading_factor": condition.max_loading_factor if condition else None,
        })
    return result


# ── Tool Handlers ──


def handle_log_workout(args: dict, session: Session) -> dict:
    d = date.fromisoformat(args["date"])
    ws = WorkoutSession(date=d, notes=args.get("notes"))
    session.add(ws)
    session.flush()

    rep_check_exercises = []
    set_order = 1
    for ex_data in args["exercises"]:
        exercise = _get_or_create_exercise(ex_data["exercise_name"], session)
        for s in ex_data["sets"]:
            session.add(WorkoutSet(
                session_id=ws.id,
                exercise_id=exercise.id,
                set_order=set_order,
                reps=s.get("reps"),
                weight=s.get("weight"),
                duration_secs=s.get("duration_secs"),
                distance_steps=s.get("distance_steps"),
                rpe=s.get("rpe"),
                notes=s.get("notes"),
            ))
            set_order += 1

        # Check if exercise has a routine entry for rep check
        routine_entry = session.exec(
            select(RoutineExercise)
            .where(RoutineExercise.exercise_id == exercise.id)
            .where(RoutineExercise.active == 1)
        ).first()
        if routine_entry and routine_entry.target_rep_min is not None:
            rep_check_exercises.append({
                "exercise_name": exercise.name,
                "weight": ex_data["sets"][0].get("weight") if ex_data["sets"] else None,
                "target_sets": routine_entry.target_sets,
                "target_rep_min": routine_entry.target_rep_min,
                "target_rep_max": routine_entry.target_rep_max,
            })

    session.commit()
    result = _build_session_summary(ws, session)
    if rep_check_exercises:
        result["rep_check"] = rep_check_exercises
    return result


def handle_update_rep_completion(args: dict, session: Session) -> dict:
    ws = session.get(WorkoutSession, args["session_id"])
    if not ws:
        return {"error": f"Session {args['session_id']} not found"}

    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == ws.id)
        .order_by(WorkoutSet.set_order)
    ).all()

    updated = []
    for comp in args["completions"]:
        exercise = _fuzzy_match_exercise(comp["exercise_name"], session)
        if not exercise:
            updated.append({"exercise_name": comp["exercise_name"], "error": "not found"})
            continue

        ex_sets = [s for s in sets if s.exercise_id == exercise.id]
        reps_per_set = comp.get("reps_per_set", [])
        for i, s in enumerate(ex_sets):
            s.rep_completion = comp["rep_completion"]
            if i < len(reps_per_set):
                s.reps = reps_per_set[i]
            session.add(s)
        updated.append({
            "exercise_name": exercise.name,
            "rep_completion": comp["rep_completion"],
            "sets_updated": len(ex_sets),
        })

    session.commit()
    return {"success": True, "updated": updated}


def handle_query_workout_history(args: dict, session: Session) -> dict:
    limit = args.get("limit", 20)
    stmt = select(WorkoutSession)
    if args.get("start_date"):
        stmt = stmt.where(WorkoutSession.date >= date.fromisoformat(args["start_date"]))
    if args.get("end_date"):
        stmt = stmt.where(WorkoutSession.date <= date.fromisoformat(args["end_date"]))

    stmt = stmt.order_by(col(WorkoutSession.date).desc()).limit(limit)
    sessions = session.exec(stmt).all()

    # Filter by exercise if specified
    exercise_name = args.get("exercise_name")
    if exercise_name:
        exercise = _fuzzy_match_exercise(exercise_name, session)
        if not exercise:
            return {"sessions": [], "note": f"Exercise '{exercise_name}' not found"}
        filtered = []
        for ws in sessions:
            ex_sets = session.exec(
                select(WorkoutSet)
                .where(WorkoutSet.session_id == ws.id)
                .where(WorkoutSet.exercise_id == exercise.id)
            ).all()
            if ex_sets:
                filtered.append(ws)
        sessions = filtered

    return {"sessions": [_build_session_summary(ws, session) for ws in sessions]}


def handle_edit_workout_session(args: dict, session: Session) -> dict:
    ws = session.get(WorkoutSession, args["session_id"])
    if not ws:
        return {"error": f"Session {args['session_id']} not found"}

    if args["action"] == "delete":
        for s in session.exec(select(WorkoutSet).where(WorkoutSet.session_id == ws.id)).all():
            session.delete(s)
        session.delete(ws)
        session.commit()
        return {"success": True, "deleted_session_id": args["session_id"]}

    # update
    if args.get("date"):
        ws.date = date.fromisoformat(args["date"])
    if args.get("notes") is not None:
        ws.notes = args["notes"]
    session.add(ws)

    if args.get("remove_set_ids"):
        for set_id in args["remove_set_ids"]:
            s = session.get(WorkoutSet, set_id)
            if s and s.session_id == ws.id:
                session.delete(s)

    if args.get("add_sets"):
        max_order = session.exec(
            select(func.max(WorkoutSet.set_order))
            .where(WorkoutSet.session_id == ws.id)
        ).first() or 0
        for i, s_data in enumerate(args["add_sets"]):
            exercise = _get_or_create_exercise(s_data["exercise_name"], session)
            session.add(WorkoutSet(
                session_id=ws.id,
                exercise_id=exercise.id,
                set_order=max_order + i + 1,
                reps=s_data.get("reps"),
                weight=s_data.get("weight"),
                duration_secs=s_data.get("duration_secs"),
                rpe=s_data.get("rpe"),
                notes=s_data.get("notes"),
            ))

    session.commit()
    return _build_session_summary(ws, session)


def _build_exercise_detail(exercise: Exercise, session: Session) -> dict:
    """Build exercise dict with current tissue mappings."""
    tissue_mappings = get_current_exercise_tissues(session, exercise.id)
    tissues = []
    for tm in tissue_mappings:
        tissue = session.get(Tissue, tm.tissue_id)
        if tissue:
            tissues.append({
                "tissue_id": tissue.id,
                "name": tissue.name,
                "display_name": tissue.display_name,
                "role": tm.role,
                "loading_factor": tm.loading_factor,
            })
    return {
        "id": exercise.id,
        "name": exercise.name,
        "equipment": exercise.equipment,
        "notes": exercise.notes,
        "tissues": tissues,
    }


def handle_manage_exercise(args: dict, session: Session) -> dict:
    action = args["action"]

    if action == "list":
        exercises = session.exec(select(Exercise).order_by(Exercise.name)).all()
        return {
            "exercises": [
                _build_exercise_detail(e, session) for e in exercises
            ]
        }

    if action == "get":
        name = args.get("name", "").strip()
        if not name:
            return {"error": "name is required for get"}
        exercise = _fuzzy_match_exercise(name, session)
        if not exercise:
            return {"error": f"Exercise '{name}' not found"}
        return _build_exercise_detail(exercise, session)

    if action == "create":
        name = args.get("name", "").strip()
        if not name:
            return {"error": "name is required"}
        existing = _fuzzy_match_exercise(name, session)
        if existing and existing.name.lower() == name.lower():
            return {"error": f"Exercise '{existing.name}' already exists"}
        exercise = Exercise(
            name=name,
            equipment=args.get("equipment"),
            notes=args.get("notes"),
        )
        session.add(exercise)
        session.flush()
        warnings = []
        if args.get("tissues"):
            warnings = _set_exercise_tissues(exercise, args["tissues"], session)
        session.commit()
        return {"success": True, "id": exercise.id, "name": exercise.name, "warnings": warnings}

    if action == "update":
        exercise = _fuzzy_match_exercise(args.get("name", ""), session)
        if not exercise:
            return {"error": f"Exercise '{args.get('name')}' not found"}
        if args.get("new_name"):
            exercise.name = args["new_name"]
        if args.get("equipment") is not None:
            exercise.equipment = args["equipment"]
        if args.get("notes") is not None:
            exercise.notes = args["notes"]
        session.add(exercise)
        warnings = []
        if args.get("tissues"):
            warnings = _set_exercise_tissues(exercise, args["tissues"], session)
        session.commit()
        return {"success": True, "id": exercise.id, "name": exercise.name, "warnings": warnings}

    if action == "merge":
        source = _fuzzy_match_exercise(args.get("name", ""), session)
        target = _fuzzy_match_exercise(args.get("merge_into", ""), session)
        if not source:
            return {"error": f"Source exercise '{args.get('name')}' not found"}
        if not target:
            return {"error": f"Target exercise '{args.get('merge_into')}' not found"}
        # Move all workout sets from source to target
        sets_moved = 0
        for s in session.exec(select(WorkoutSet).where(WorkoutSet.exercise_id == source.id)).all():
            s.exercise_id = target.id
            session.add(s)
            sets_moved += 1
        # Move routine entries
        routine_entries = session.exec(
            select(RoutineExercise)
            .where(RoutineExercise.exercise_id == source.id)
        ).all()
        for re in routine_entries:
            re.exercise_id = target.id
            session.add(re)
        # Delete source exercise tissue mappings and the exercise
        src_tissues = session.exec(
            select(ExerciseTissue)
            .where(ExerciseTissue.exercise_id == source.id)
        ).all()
        for et in src_tissues:
            session.delete(et)
        session.delete(source)
        session.commit()
        return {
            "success": True,
            "merged": source.name,
            "into": target.name,
            "sets_moved": sets_moved,
        }

    if action == "delete":
        exercise = _fuzzy_match_exercise(args.get("name", ""), session)
        if not exercise:
            return {"error": f"Exercise '{args.get('name')}' not found"}
        eid = exercise.id
        for s in session.exec(
            select(WorkoutSet).where(WorkoutSet.exercise_id == eid)
        ).all():
            session.delete(s)
        for et in session.exec(
            select(ExerciseTissue).where(ExerciseTissue.exercise_id == eid)
        ).all():
            session.delete(et)
        for re in session.exec(
            select(RoutineExercise).where(RoutineExercise.exercise_id == eid)
        ).all():
            session.delete(re)
        session.delete(exercise)
        session.commit()
        return {"success": True, "deleted": exercise.name}

    return {"error": f"Unknown action: {action}"}


def handle_bulk_set_exercise_tissues(args: dict, session: Session) -> dict:
    results = []
    for mapping in args["mappings"]:
        exercise = _get_or_create_exercise(mapping["exercise_name"], session)
        warnings = _set_exercise_tissues(exercise, mapping["tissues"], session)
        results.append({
            "exercise_name": exercise.name,
            "tissues_set": len(mapping["tissues"]) - len(warnings),
            "warnings": warnings,
        })
    session.commit()
    return {"results": results}


def handle_manage_tissue(args: dict, session: Session) -> dict:
    action = args["action"]

    if action == "list":
        tissues = get_current_tissues(session)
        return {
            "tissues": [
                {
                    "id": t.id, "name": t.name, "display_name": t.display_name,
                    "type": t.type, "recovery_hours": t.recovery_hours,
                }
                for t in tissues
            ]
        }

    if action == "tree":
        return {"tree": get_tissue_tree(session)}

    if action == "create":
        parent = None
        if args.get("parent_name"):
            parent = _find_tissue_by_name(args["parent_name"], session)
            if not parent:
                return {"error": f"Parent tissue '{args['parent_name']}' not found"}
        name = args.get("name", "").strip()
        display_name = args.get("display_name", name.replace("_", " ").title())
        tissue = Tissue(
            name=name,
            display_name=display_name,
            type=args.get("type", "muscle"),
            parent_id=parent.id if parent else None,
            recovery_hours=args.get("recovery_hours", 48),
            notes=args.get("notes"),
        )
        session.add(tissue)
        session.commit()
        session.refresh(tissue)
        return {"success": True, "id": tissue.id, "name": tissue.name}

    if action == "update":
        tissue = _find_tissue_by_name(args.get("name", ""), session)
        if not tissue:
            return {"error": f"Tissue '{args.get('name')}' not found"}
        # Append new log row
        new_tissue = Tissue(
            name=tissue.name,
            display_name=tissue.display_name,
            type=tissue.type,
            parent_id=tissue.parent_id,
            recovery_hours=args.get("recovery_hours", tissue.recovery_hours),
            notes=args.get("notes", tissue.notes),
        )
        session.add(new_tissue)
        session.commit()
        session.refresh(new_tissue)
        return {"success": True, "id": new_tissue.id, "name": new_tissue.name}

    return {"error": f"Unknown action: {action}"}


def handle_update_tissue_recovery(args: dict, session: Session) -> dict:
    tissue = _find_tissue_by_name(args["tissue_name"], session)
    if not tissue:
        return {"error": f"Tissue '{args['tissue_name']}' not found"}
    new_tissue = Tissue(
        name=tissue.name,
        display_name=tissue.display_name,
        type=tissue.type,
        parent_id=tissue.parent_id,
        recovery_hours=args.get("recovery_hours", tissue.recovery_hours),
        notes=args.get("notes", tissue.notes),
    )
    session.add(new_tissue)
    session.commit()
    return {"success": True, "name": tissue.name, "recovery_hours": new_tissue.recovery_hours}


def handle_log_tissue_condition(args: dict, session: Session) -> dict:
    tissue = _find_tissue_by_name(args["tissue_name"], session)
    if not tissue:
        return {"error": f"Tissue '{args['tissue_name']}' not found"}
    condition = TissueCondition(
        tissue_id=tissue.id,
        status=args["status"],
        severity=args["severity"],
        max_loading_factor=args.get("max_loading_factor"),
        recovery_hours_override=args.get("recovery_hours_override"),
        rehab_protocol=args.get("rehab_protocol"),
        notes=args.get("notes"),
    )
    session.add(condition)
    session.commit()
    session.refresh(condition)
    return {
        "success": True,
        "tissue_name": tissue.name,
        "status": condition.status,
        "severity": condition.severity,
    }


def handle_query_tissue_condition(args: dict, session: Session) -> dict:
    tissue = _find_tissue_by_name(args["tissue_name"], session)
    if not tissue:
        return {"error": f"Tissue '{args['tissue_name']}' not found"}
    limit = args.get("limit", 10)
    conditions = session.exec(
        select(TissueCondition)
        .where(TissueCondition.tissue_id == tissue.id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(limit)
    ).all()
    return {
        "tissue_name": tissue.name,
        "conditions": [
            {
                "status": c.status,
                "severity": c.severity,
                "max_loading_factor": c.max_loading_factor,
                "recovery_hours_override": c.recovery_hours_override,
                "rehab_protocol": c.rehab_protocol,
                "notes": c.notes,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in conditions
        ],
    }


def handle_check_tissue_readiness(args: dict, session: Session) -> dict:
    readiness = _compute_tissue_readiness(session)
    # Filter to just leaf tissues and non-100% or non-healthy for conciseness
    summary = [
        r for r in readiness
        if not r["ready"] or r["condition"] != "healthy"
    ]
    ready_count = sum(1 for r in readiness if r["ready"])
    return {
        "total_tissues": len(readiness),
        "ready_count": ready_count,
        "not_ready_or_conditions": summary,
        "all_ready": ready_count == len(readiness),
    }


def handle_suggest_workout(args: dict, session: Session) -> dict:
    readiness = _compute_tissue_readiness(session)
    readiness_by_name = {r["tissue_name"]: r for r in readiness}
    conditions = {c.tissue_id: c for c in get_all_current_conditions(session)}

    routine = session.exec(
        select(RoutineExercise)
        .where(RoutineExercise.active == 1)
        .order_by(RoutineExercise.sort_order)
    ).all()

    available = []
    excluded = []
    rehab = []

    for re in routine:
        exercise = session.get(Exercise, re.exercise_id)
        if not exercise:
            continue

        tissue_mappings = get_current_exercise_tissues(session, exercise.id)
        blocked_by = []

        for tm in tissue_mappings:
            tissue = session.get(Tissue, tm.tissue_id)
            if not tissue:
                continue
            r = readiness_by_name.get(tissue.name, {})
            cond = conditions.get(tissue.id)

            # Check if tissue condition blocks this exercise
            if cond and cond.max_loading_factor is not None:
                if tm.loading_factor > cond.max_loading_factor:
                    blocked_by.append(
                        f"{tissue.display_name} ({cond.status}, "
                        f"max_load={cond.max_loading_factor})"
                    )

            # Check recovery
            if not r.get("ready", True) and tm.role == "primary":
                pct = r.get('recovery_pct', 0)
                blocked_by.append(
                    f"{tissue.display_name} (recovering, {pct:.0f}%)"
                )

        # Get last performance
        last_set = session.exec(
            select(WorkoutSet)
            .where(WorkoutSet.exercise_id == exercise.id)
            .order_by(col(WorkoutSet.created_at).desc())
            .limit(1)
        ).first()

        entry = {
            "exercise_name": exercise.name,
            "target_sets": re.target_sets,
            "target_rep_min": re.target_rep_min,
            "target_rep_max": re.target_rep_max,
            "last_weight": last_set.weight if last_set else None,
            "notes": re.notes,
        }

        if blocked_by:
            entry["blocked_by"] = blocked_by
            excluded.append(entry)
        else:
            available.append(entry)

    # Add rehab exercises for injured/tender tissues
    for tissue_id, cond in conditions.items():
        if cond.status in ("tender", "rehabbing") and cond.rehab_protocol:
            tissue = session.get(Tissue, tissue_id)
            rehab.append({
                "tissue_name": tissue.display_name if tissue else f"id:{tissue_id}",
                "status": cond.status,
                "severity": cond.severity,
                "rehab_protocol": cond.rehab_protocol,
            })

    return {
        "available": available,
        "excluded": excluded,
        "rehab": rehab,
    }


def handle_manage_routine(args: dict, session: Session) -> dict:
    action = args["action"]

    if action == "list":
        routine = session.exec(
            select(RoutineExercise).order_by(RoutineExercise.sort_order)
        ).all()
        entries = []
        for re in routine:
            exercise = session.get(Exercise, re.exercise_id)
            entries.append({
                "id": re.id,
                "exercise_name": exercise.name if exercise else f"id:{re.exercise_id}",
                "target_sets": re.target_sets,
                "target_rep_min": re.target_rep_min,
                "target_rep_max": re.target_rep_max,
                "sort_order": re.sort_order,
                "active": bool(re.active),
                "notes": re.notes,
            })
        return {"routine": entries}

    if action == "add":
        exercise = _get_or_create_exercise(args.get("exercise_name", ""), session)
        existing = session.exec(
            select(RoutineExercise).where(RoutineExercise.exercise_id == exercise.id)
        ).first()
        if existing:
            return {"error": f"'{exercise.name}' already in routine"}
        re = RoutineExercise(
            exercise_id=exercise.id,
            target_sets=args.get("target_sets", 3),
            target_rep_min=args.get("target_rep_min"),
            target_rep_max=args.get("target_rep_max"),
            sort_order=args.get("sort_order", 0),
            active=1 if args.get("active", True) else 0,
            notes=args.get("notes"),
        )
        session.add(re)
        session.commit()
        return {"success": True, "exercise_name": exercise.name, "routine_id": re.id}

    if action == "update":
        exercise = _fuzzy_match_exercise(args.get("exercise_name", ""), session)
        if not exercise:
            return {"error": f"Exercise '{args.get('exercise_name')}' not found"}
        re = session.exec(
            select(RoutineExercise).where(RoutineExercise.exercise_id == exercise.id)
        ).first()
        if not re:
            return {"error": f"'{exercise.name}' not in routine"}
        if args.get("target_sets") is not None:
            re.target_sets = args["target_sets"]
        if args.get("target_rep_min") is not None:
            re.target_rep_min = args["target_rep_min"]
        if args.get("target_rep_max") is not None:
            re.target_rep_max = args["target_rep_max"]
        if args.get("sort_order") is not None:
            re.sort_order = args["sort_order"]
        if args.get("active") is not None:
            re.active = 1 if args["active"] else 0
        if args.get("notes") is not None:
            re.notes = args["notes"]
        session.add(re)
        session.commit()
        return {"success": True, "exercise_name": exercise.name}

    if action == "remove":
        exercise = _fuzzy_match_exercise(args.get("exercise_name", ""), session)
        if not exercise:
            return {"error": f"Exercise '{args.get('exercise_name')}' not found"}
        re = session.exec(
            select(RoutineExercise).where(RoutineExercise.exercise_id == exercise.id)
        ).first()
        if not re:
            return {"error": f"'{exercise.name}' not in routine"}
        session.delete(re)
        session.commit()
        return {"success": True, "removed": exercise.name}

    if action == "reorder":
        exercise = _fuzzy_match_exercise(args.get("exercise_name", ""), session)
        if not exercise:
            return {"error": f"Exercise '{args.get('exercise_name')}' not found"}
        re = session.exec(
            select(RoutineExercise).where(RoutineExercise.exercise_id == exercise.id)
        ).first()
        if not re:
            return {"error": f"'{exercise.name}' not in routine"}
        re.sort_order = args.get("sort_order", 0)
        session.add(re)
        session.commit()
        return {"success": True, "exercise_name": exercise.name, "sort_order": re.sort_order}

    return {"error": f"Unknown action: {action}"}


def handle_query_exercise_history(args: dict, session: Session) -> dict:
    exercise = _fuzzy_match_exercise(args["exercise_name"], session)
    if not exercise:
        return {"error": f"Exercise '{args['exercise_name']}' not found"}

    limit = args.get("limit", 10)
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise.id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    results = session.exec(stmt).all()

    sessions_map: dict[date, list] = {}
    for ws, d in results:
        sessions_map.setdefault(d, []).append(ws)

    sessions_out = []
    for d in sorted(sessions_map.keys(), reverse=True)[:limit]:
        sets = sessions_map[d]
        max_weight = max((s.weight or 0) for s in sets)
        total_volume = sum((s.reps or 0) * (s.weight or 0) for s in sets)
        completions = [s.rep_completion for s in sets if s.rep_completion]
        sessions_out.append({
            "date": str(d),
            "sets": [
                {"reps": s.reps, "weight": s.weight, "rep_completion": s.rep_completion}
                for s in sets
            ],
            "max_weight": max_weight,
            "total_volume": total_volume,
            "rep_completions": completions,
        })

    return {"exercise_name": exercise.name, "sessions": sessions_out}


def handle_analyze_tissue_volume(args: dict, session: Session) -> dict:
    group_by = args.get("group_by", "week")

    # Get all workout sets with dates and exercise-tissue mappings
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
    )
    if args.get("start_date"):
        stmt = stmt.where(WorkoutSession.date >= date.fromisoformat(args["start_date"]))
    if args.get("end_date"):
        stmt = stmt.where(WorkoutSession.date <= date.fromisoformat(args["end_date"]))

    results = session.exec(stmt).all()

    # Get current exercise-tissue mappings
    et_sub = (
        select(
            ExerciseTissue.exercise_id,
            ExerciseTissue.tissue_id,
            func.max(ExerciseTissue.updated_at).label("max_updated"),
        )
        .group_by(ExerciseTissue.exercise_id, ExerciseTissue.tissue_id)
        .subquery()
    )
    current_ets = session.exec(
        select(ExerciseTissue).join(
            et_sub,
            (ExerciseTissue.exercise_id == et_sub.c.exercise_id)
            & (ExerciseTissue.tissue_id == et_sub.c.tissue_id)
            & (ExerciseTissue.updated_at == et_sub.c.max_updated),
        )
    ).all()

    # Build exercise → tissue loadings
    exercise_loadings: dict[int, list[tuple[int, str, float]]] = {}
    for et in current_ets:
        tissue = session.get(Tissue, et.tissue_id)
        if tissue:
            exercise_loadings.setdefault(et.exercise_id, []).append(
                (et.tissue_id, tissue.name, et.loading_factor)
            )

    # Filter by tissue if specified
    target_tissue = None
    if args.get("tissue_name"):
        target_tissue = _find_tissue_by_name(args["tissue_name"], session)
        if not target_tissue:
            return {"error": f"Tissue '{args['tissue_name']}' not found"}

    # Compute volume per period per tissue
    volume_data: dict[str, dict[str, float]] = {}  # period → tissue_name → volume
    for ws, d in results:
        if group_by == "week":
            period = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        else:
            period = f"{d.year}-{d.month:02d}"

        loadings = exercise_loadings.get(ws.exercise_id, [])
        for tissue_id, tissue_name, loading_factor in loadings:
            if target_tissue and tissue_id != target_tissue.id:
                continue
            vol = (ws.reps or 0) * (ws.weight or 0) * loading_factor
            volume_data.setdefault(period, {}).setdefault(tissue_name, 0)
            volume_data[period][tissue_name] += vol

    # Format output
    periods = []
    for period in sorted(volume_data.keys()):
        tissues_vol = volume_data[period]
        periods.append({
            "period": period,
            "tissues": {
                name: round(vol, 1)
                for name, vol in sorted(tissues_vol.items(), key=lambda x: -x[1])
            },
        })

    return {"group_by": group_by, "periods": periods}


def handle_suggest_progression(args: dict, session: Session) -> dict:
    exercise = _fuzzy_match_exercise(args["exercise_name"], session)
    if not exercise:
        return {"error": f"Exercise '{args['exercise_name']}' not found"}

    # Get last N sessions
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise.id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    results = session.exec(stmt).all()

    if not results:
        return {"exercise_name": exercise.name, "suggestion": "No history yet."}

    # Group by date
    sessions_map: dict[date, list] = {}
    for ws, d in results:
        sessions_map.setdefault(d, []).append(ws)

    dates = sorted(sessions_map.keys(), reverse=True)[:5]
    recent_completions = []
    recent_weights = []
    for d in dates:
        sets = sessions_map[d]
        completions = [s.rep_completion for s in sets if s.rep_completion]
        weights = [s.weight for s in sets if s.weight]
        if completions:
            recent_completions.append(completions[0])  # use first set's completion
        if weights:
            recent_weights.append(max(weights))

    # Check routine for targets
    routine_entry = session.exec(
        select(RoutineExercise).where(RoutineExercise.exercise_id == exercise.id)
    ).first()

    current_weight = recent_weights[0] if recent_weights else None

    # Progression logic
    consecutive_full = 0
    for c in recent_completions:
        if c == "full":
            consecutive_full += 1
        else:
            break

    if consecutive_full >= 2:
        suggestion = (
            f"You've hit full completion {consecutive_full} sessions in a row"
            f"{f' at {current_weight} lbs' if current_weight else ''}. "
            f"Ready to increase weight by 5-10%."
        )
    elif recent_completions and recent_completions[0] == "failed":
        suggestion = (
            f"Last session was a fail{f' at {current_weight} lbs' if current_weight else ''}. "
            "Consider deloading 10% or checking form. "
            "Also check tissue conditions for any issues."
        )
    elif recent_completions and recent_completions[0] == "partial":
        suggestion = (
            f"Within range{f' at {current_weight} lbs' if current_weight else ''}. "
            "Stay at current weight and aim for top of rep range."
        )
    else:
        suggestion = "Not enough rep completion data. Log your sets and rate them after."

    # Check tissue conditions that might affect this exercise
    tissue_warnings = []
    tissue_mappings = get_current_exercise_tissues(session, exercise.id)
    conditions = {c.tissue_id: c for c in get_all_current_conditions(session)}
    for tm in tissue_mappings:
        cond = conditions.get(tm.tissue_id)
        if cond and cond.status != "healthy":
            tissue = session.get(Tissue, tm.tissue_id)
            tissue_warnings.append(
                f"{tissue.display_name if tissue else 'unknown'}: "
                f"{cond.status} (severity {cond.severity})"
            )

    return {
        "exercise_name": exercise.name,
        "current_weight": current_weight,
        "consecutive_full": consecutive_full,
        "recent_completions": recent_completions,
        "suggestion": suggestion,
        "tissue_warnings": tissue_warnings,
        "routine_target": {
            "sets": routine_entry.target_sets,
            "rep_min": routine_entry.target_rep_min,
            "rep_max": routine_entry.target_rep_max,
        } if routine_entry else None,
    }


# ── Dispatcher ──

WORKOUT_TOOL_HANDLERS = {
    "log_workout": handle_log_workout,
    "update_rep_completion": handle_update_rep_completion,
    "query_workout_history": handle_query_workout_history,
    "edit_workout_session": handle_edit_workout_session,
    "manage_exercise": handle_manage_exercise,
    "bulk_set_exercise_tissues": handle_bulk_set_exercise_tissues,
    "manage_tissue": handle_manage_tissue,
    "update_tissue_recovery": handle_update_tissue_recovery,
    "log_tissue_condition": handle_log_tissue_condition,
    "query_tissue_condition": handle_query_tissue_condition,
    "check_tissue_readiness": handle_check_tissue_readiness,
    "suggest_workout": handle_suggest_workout,
    "manage_routine": handle_manage_routine,
    "query_exercise_history": handle_query_exercise_history,
    "analyze_tissue_volume": handle_analyze_tissue_volume,
    "suggest_progression": handle_suggest_progression,
}


def get_workout_context(session: Session) -> dict[str, str]:
    """Build workout context strings for the system prompt."""
    # Exercise list
    exercises = session.exec(select(Exercise).order_by(Exercise.name)).all()
    exercise_list = json.dumps(
        [{"id": e.id, "name": e.name, "equipment": e.equipment} for e in exercises],
        separators=(",", ":"),
    ) if exercises else "[]"

    # Routine summary
    routine = session.exec(
        select(RoutineExercise)
        .where(RoutineExercise.active == 1)
        .order_by(RoutineExercise.sort_order)
    ).all()
    routine_lines = []
    for re in routine:
        exercise = session.get(Exercise, re.exercise_id)
        name = exercise.name if exercise else f"id:{re.exercise_id}"
        rep_range = ""
        if re.target_rep_min and re.target_rep_max:
            rep_range = f"{re.target_sets}x{re.target_rep_min}-{re.target_rep_max}"
        elif re.target_rep_min:
            rep_range = f"{re.target_sets}x{re.target_rep_min}+"
        else:
            rep_range = f"{re.target_sets} sets"
        routine_lines.append(f"  - {name}: {rep_range}")
    routine_summary = "\n".join(routine_lines) if routine_lines else "  (no routine set)"

    # Conditions
    conditions = get_all_current_conditions(session)
    condition_lines = []
    for c in conditions:
        if c.status != "healthy":
            tissue = session.get(Tissue, c.tissue_id)
            name = tissue.display_name if tissue else f"id:{c.tissue_id}"
            parts = f"  - {name}: {c.status} (severity {c.severity}"
            if c.max_loading_factor is not None:
                parts += f", max_load={c.max_loading_factor}"
            if c.rehab_protocol:
                parts += f", rehab: {c.rehab_protocol}"
            parts += ")"
            condition_lines.append(parts)
    conditions_text = "\n".join(condition_lines) if condition_lines else "  All tissues healthy."

    return {
        "exercise_list": exercise_list,
        "routine_summary": routine_summary,
        "conditions_text": conditions_text,
    }
