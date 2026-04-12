"""Workout domain LLM tools: exercises, tissues, tissue_conditions,
workout_sessions, workouts.

Each table gets a get_<table> getter and a set_<table> setter following
the shared contract.
"""

import difflib
import json
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import user_today
from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    Tissue,
    TissueCondition,
    TrainingProgram,
    Workout,
    WorkoutSession,
    WorkoutSet,
)
from app.training_model import build_exercise_risk_ranking
from app.workout_queries import (
    get_all_current_conditions,
    get_current_exercise_tissues,
    get_current_tissues,
    get_last_trained_by_tissue,
)

from .shared import (
    apply_filters,
    apply_fuzzy_post_filter,
    apply_sort,
    error_response,
    fuzzy_score,
    getter_response,
    parse_date_val,
    record_to_dict,
    setter_response,
)

# ── Shared helpers ────────────────────────────────────────────────────


def _fuzzy_match_exercise(name: str, session: Session) -> Exercise | None:
    exercises = session.exec(select(Exercise)).all()
    names = [e.name for e in exercises]
    matches = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    if matches:
        return session.exec(
            select(Exercise).where(Exercise.name == matches[0])
        ).first()
    lower = name.lower().strip()
    for e in exercises:
        if e.name.lower().strip() == lower:
            return e
    return None


def _get_or_create_exercise(
    name: str, session: Session
) -> Exercise:
    existing = _fuzzy_match_exercise(name, session)
    if existing:
        return existing
    exercise = Exercise(name=name)
    session.add(exercise)
    session.flush()
    return exercise


# Common anatomy synonyms — maps alternate names to canonical DB names.
# Both keys and values should be lowercase.
_TISSUE_SYNONYMS: dict[str, str] = {
    # peroneal ↔ fibularis
    "peroneus longus": "fibularis_longus",
    "peroneus brevis": "fibularis_brevis",
    "peroneal longus": "fibularis_longus",
    "peroneal brevis": "fibularis_brevis",
    "peroneals": "fibularis_longus",
    # shoulder
    "glenohumeral joint": "shoulder_joint",
    # spine shortcuts
    "cervical spine joint": "cervical_spine",
    "thoracic spine joint": "thoracic_spine",
    "lumbar spine joint": "lumbar_spine",
    # pec aliases
    "upper pec": "pec_clavicular_head",
    "upper pecs": "pec_clavicular_head",
    "lower pec": "pec_sternal_head",
    "lower pecs": "pec_sternal_head",
    # abs shorthand
    "abs": "rectus_abdominis",
    # quad components
    "vastus medialis oblique": "vastus_medialis",
    "vmo": "vastus_medialis",
    # common alternate names
    "lats": "latissimus_dorsi",
    "traps": "trapezius",
    "rhomboids": "rhomboid_major",
    "hip flexors": "iliopsoas",
    "hip flexor": "iliopsoas",
    "calves": "gastrocnemius",
    "calf": "gastrocnemius",
}


def _normalize_tissue_input(name: str) -> str:
    """Normalize a tissue name for comparison: lowercase, strip,
    collapse whitespace, replace spaces with underscores."""
    return "_".join(name.lower().strip().split())


def _find_tissue_by_name(
    name: str, session: Session
) -> Tissue | None:
    """Resolve a tissue by name, display_name, synonym, or fuzzy match.

    Lookup order:
    1. Exact match on name
    2. Exact match on display_name (case-insensitive)
    3. Normalized match on name (spaces→underscores, lowercase)
    4. Synonym lookup
    5. Fuzzy match on both name and display_name
    """
    if not name:
        return None

    # 1. Exact match on name field
    tissue = session.exec(
        select(Tissue).where(Tissue.name == name)
    ).first()
    if tissue:
        return tissue

    # 2. Case-insensitive match on display_name
    tissues = get_current_tissues(session)
    name_lower = name.lower().strip()
    for t in tissues:
        if t.display_name and t.display_name.lower() == name_lower:
            return t

    # 3. Normalized name match (handles "Lumbar Spine" → "lumbar_spine")
    normalized = _normalize_tissue_input(name)
    for t in tissues:
        if t.name == normalized:
            return t

    # 4. Synonym lookup
    synonym_target = _TISSUE_SYNONYMS.get(name_lower)
    if not synonym_target:
        synonym_target = _TISSUE_SYNONYMS.get(normalized)
    if synonym_target:
        for t in tissues:
            if t.name == synonym_target:
                return t

    # 5. Fuzzy match against both name and display_name
    candidates: dict[str, Tissue] = {}
    for t in tissues:
        candidates[t.name] = t
        if t.display_name:
            candidates[t.display_name.lower()] = t

    matches = difflib.get_close_matches(
        name_lower, candidates.keys(), n=1, cutoff=0.6
    )
    if matches:
        return candidates[matches[0]]

    return None


def _suggest_tissue_matches(
    name: str, session: Session, n: int = 3
) -> list[str]:
    """Return close tissue name suggestions for an unresolved name."""
    tissues = get_current_tissues(session)
    candidates = []
    for t in tissues:
        candidates.append(t.name)
        if t.display_name:
            candidates.append(t.display_name)
    name_lower = name.lower().strip()
    return difflib.get_close_matches(name_lower, candidates, n=n, cutoff=0.4)


def _set_exercise_tissues(
    exercise: Exercise,
    tissue_records: list[dict],
    session: Session,
) -> list[str]:
    """Replace tissue mappings for an exercise.

    Deletes existing mappings and creates new ones.
    """
    warnings: list[str] = []
    # Delete existing mappings
    old = session.exec(
        select(ExerciseTissue).where(
            ExerciseTissue.exercise_id == exercise.id
        )
    ).all()
    for et in old:
        session.delete(et)
    session.flush()
    for tm in tissue_records:
        tissue_id = tm.get("tissue_id")
        tissue_name = tm.get("name")
        tissue = None
        if tissue_id:
            tissue = session.get(Tissue, tissue_id)
        elif tissue_name:
            tissue = _find_tissue_by_name(tissue_name, session)
        if not tissue:
            label = tissue_name or tissue_id
            suggestions = (
                _suggest_tissue_matches(tissue_name, session)
                if tissue_name else []
            )
            hint = (
                f" — did you mean: {', '.join(suggestions)}?"
                if suggestions else ""
            )
            warnings.append(f"Tissue '{label}' not found, skipped{hint}")
            continue
        session.add(ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=tissue.id,
            role=tm.get("role", "primary"),
            loading_factor=tm.get("loading_factor", 1.0),
        ))
    return warnings


def _build_exercise_detail(
    exercise: Exercise, session: Session
) -> dict:
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


def _build_session_summary(
    ws: WorkoutSession, session: Session
) -> dict:
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
                    "set_id": s.id,
                    "set_order": s.set_order,
                    "reps": s.reps,
                    "weight": s.weight,
                    "duration_secs": s.duration_secs,
                    "rpe": s.rpe,
                    "rep_completion": s.rep_completion,
                    "notes": s.notes,
                }
                for s in ex_sets
            ],
        })
    return {
        "id": ws.id,
        "date": str(ws.date),
        "started_at": ws.started_at.isoformat() if ws.started_at else None,
        "finished_at": ws.finished_at.isoformat() if ws.finished_at else None,
        "notes": ws.notes,
        "exercises": exercise_summaries,
    }


def _compute_tissue_readiness(session: Session) -> list[dict]:
    """Compute readiness for all current tissues."""
    now = datetime.now(UTC)
    tissues = get_current_tissues(session)
    conditions = {
        c.tissue_id: c for c in get_all_current_conditions(session)
    }

    # Exercise-tissue mappings
    current_ets = session.exec(
        select(ExerciseTissue)
    ).all()

    exercise_tissues: dict[int, list[int]] = {}
    for et in current_ets:
        exercise_tissues.setdefault(et.exercise_id, []).append(
            et.tissue_id
        )

    # Last trained per tissue using actual session time, not session id order.
    last_trained_map = get_last_trained_by_tissue(session, exercise_tissues)

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
                recovery_pct = min(
                    100.0, (hours_since / effective_recovery) * 100
                )
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
            "hours_since": (
                round(hours_since, 1) if hours_since is not None else None
            ),
            "effective_recovery_hours": effective_recovery,
            "condition": condition.status if condition else "healthy",
            "severity": condition.severity if condition else 0,
            "max_loading_factor": (
                condition.max_loading_factor if condition else None
            ),
        })
    return result


# =====================================================================
#  Exercises
# =====================================================================

GET_EXERCISES_DEF = {
    "type": "function",
    "function": {
        "name": "get_exercises",
        "description": (
            "Get exercise records with tissue mappings. "
            "Search by name (fuzzy) or equipment. "
            "Include history, stats, and training_risk for progression data "
            "and exercise selection."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), name({eq,fuzzy,contains}), "
                        "equipment({eq,contains}), "
                        "recommendation({eq}: avoid/caution/good)."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "current_tissues",
                            "history",
                            "stats",
                            "training_risk",
                        ],
                    },
                    "default": ["current_tissues"],
                },
                "limit": {"type": "integer", "default": 50},
                "sort": {
                    "type": "array",
                    "description": (
                        "Sort by name, or when include contains training_risk, "
                        "by training_risk_7d, training_risk_14d, suitability_score, "
                        "or max_tissue_risk_7d."
                    ),
                },
            },
        },
    },
}

SET_EXERCISES_DEF = {
    "type": "function",
    "function": {
        "name": "set_exercises",
        "description": (
            "Create, update, upsert, merge, or delete exercises. "
            "Manage tissue mappings through current_tissues relation "
            "with mode=append_snapshot. Use merge operation to "
            "combine two exercises.\n"
            "IMPORTANT: For update/upsert/delete, provide match criteria "
            "inside a 'match' object, e.g.:\n"
            '  {"operation":"update","match":{"id":{"eq":46}},'
            '"relations":{"current_tissues":{"mode":"append_snapshot",'
            '"records":[...]}}}\n'
            "Match supports: id({eq}), name({eq,fuzzy}). "
            "Do NOT put id at the top level or use 'where' — "
            "use 'match'."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "upsert",
                                    "delete",
                                    "merge",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "name({eq,fuzzy}), id({eq})."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": "name, equipment, notes.",
                                "properties": {
                                    "name": {"type": "string"},
                                    "equipment": {"type": "string"},
                                    "notes": {"type": "string"},
                                },
                            },
                            "merge_into": {
                                "type": "object",
                                "description": (
                                    "Target exercise for merge. "
                                    "name({eq,fuzzy})."
                                ),
                            },
                            "relations": {
                                "type": "object",
                                "properties": {
                                    "current_tissues": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": [
                                                    "append_snapshot",
                                                ],
                                            },
                                            "records": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "tissue_id": {
                                                            "type": "integer",
                                                            "description": "Exact tissue ID. Optional if name is provided.",
                                                        },
                                                        "name": {
                                                            "type": "string",
                                                            "description": (
                                                                "Tissue name — matches against name, display_name, "
                                                                "common synonyms, and fuzzy. Preferred over tissue_id. "
                                                                "Examples: 'Biceps Brachii', 'biceps_brachii', 'Lats', "
                                                                "'Peroneus Longus' (→ fibularis_longus)."
                                                            ),
                                                        },
                                                        "role": {
                                                            "type": "string",
                                                            "enum": [
                                                                "primary",
                                                                "secondary",
                                                                "stabilizer",
                                                            ],
                                                            "description": (
                                                                "primary: main movers. "
                                                                "secondary: synergists, stabilizing muscles, and tendons. "
                                                                "stabilizer: joints."
                                                            ),
                                                        },
                                                        "loading_factor": {
                                                            "type": "number",
                                                            "description": (
                                                                "0.0-1.0. Fraction of load this tissue bears. "
                                                                "1.0 = fully loaded primary mover or directly loaded joint/tendon."
                                                            ),
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _include_exercise_history(
    exercise: Exercise, session: Session, limit: int = 10
) -> list[dict]:
    """Get recent performance history for an exercise."""
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
        completions = [
            s.rep_completion for s in sets if s.rep_completion
        ]
        sessions_out.append({
            "date": str(d),
            "sets": [
                {
                    "reps": s.reps,
                    "weight": s.weight,
                    "rep_completion": s.rep_completion,
                }
                for s in sets
            ],
            "max_weight": max_weight,
            "total_volume": total_volume,
            "rep_completions": completions,
        })
    return sessions_out


def _include_exercise_stats(
    exercise: Exercise, session: Session
) -> dict:
    """Compute progression stats for an exercise."""
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise.id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    results = session.exec(stmt).all()
    if not results:
        return {"suggestion": "No history yet."}

    sessions_map: dict[date, list] = {}
    for ws, d in results:
        sessions_map.setdefault(d, []).append(ws)

    dates = sorted(sessions_map.keys(), reverse=True)[:5]
    recent_completions = []
    recent_weights: list[float] = []
    for d in dates:
        sets = sessions_map[d]
        completions = [
            s.rep_completion for s in sets if s.rep_completion
        ]
        weights = [s.weight for s in sets if s.weight]
        if completions:
            recent_completions.append(completions[0])
        if weights:
            recent_weights.append(max(weights))

    # Find target from active program's ProgramDayExercise
    active_program = session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()
    pde_entry = None
    if active_program:
        pde_entry = session.exec(
            select(ProgramDayExercise)
            .join(ProgramDay)
            .where(ProgramDay.program_id == active_program.id)
            .where(ProgramDayExercise.exercise_id == exercise.id)
        ).first()

    current_weight = recent_weights[0] if recent_weights else None
    consecutive_full = 0
    for c in recent_completions:
        if c == "full":
            consecutive_full += 1
        else:
            break

    if consecutive_full >= 2:
        suggestion = (
            f"Full completion {consecutive_full}x in a row"
            f"{f' at {current_weight} lbs' if current_weight else ''}."
            " Ready to increase weight by 5-10%."
        )
    elif recent_completions and recent_completions[0] == "failed":
        suggestion = (
            f"Last session failed"
            f"{f' at {current_weight} lbs' if current_weight else ''}."
            " Consider deloading 10% or checking form."
        )
    elif recent_completions and recent_completions[0] == "partial":
        suggestion = (
            f"Within range"
            f"{f' at {current_weight} lbs' if current_weight else ''}."
            " Stay at current weight, aim for top of rep range."
        )
    else:
        suggestion = "Not enough rep completion data."

    return {
        "current_weight": current_weight,
        "consecutive_full": consecutive_full,
        "recent_completions": recent_completions,
        "suggestion": suggestion,
        "routine_target": {
            "sets": pde_entry.target_sets,
            "rep_min": pde_entry.target_rep_min,
            "rep_max": pde_entry.target_rep_max,
        } if pde_entry else None,
    }


def handle_get_exercises(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    includes = args.get("include", ["current_tissues"])
    stmt = select(Exercise)
    stmt, fuzzy_specs = apply_filters(
        stmt, Exercise, filters, fuzzy_fields=["name"]
    )
    stmt = apply_sort(
        stmt, Exercise,
        args.get("sort") or [{"field": "name", "direction": "asc"}],
    )
    limit = args.get("limit", 50)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())

    match_info: list[dict] = []
    if fuzzy_specs:
        records, match_info = apply_fuzzy_post_filter(records, fuzzy_specs)

    training_risk_map = {}
    if "training_risk" in includes:
        risk_rows = build_exercise_risk_ranking(session)
        training_risk_map = {row["id"]: row for row in risk_rows}
        recommendation_filter = (filters or {}).get("recommendation")
        if recommendation_filter:
            expected = (
                recommendation_filter.get("eq")
                if isinstance(recommendation_filter, dict)
                else recommendation_filter
            )
            records = [
                exercise
                for exercise in records
                if training_risk_map.get(exercise.id, {}).get("recommendation") == expected
            ]
        custom_sort = (args.get("sort") or [])
        if custom_sort:
            field = custom_sort[0].get("field")
            reverse = custom_sort[0].get("direction", "asc") == "desc"
            if field in {
                "training_risk_7d",
                "training_risk_14d",
                "suitability_score",
                "max_tissue_risk_7d",
            }:
                field_map = {
                    "training_risk_7d": "weighted_risk_7d",
                    "training_risk_14d": "weighted_risk_14d",
                    "suitability_score": "suitability_score",
                    "max_tissue_risk_7d": "max_tissue_risk_7d",
                }
                sort_key = field_map[field]
                records.sort(
                    key=lambda exercise: training_risk_map.get(exercise.id, {}).get(sort_key, 0),
                    reverse=reverse,
                )

    results = []
    for ex in records:
        d = _build_exercise_detail(ex, session)
        if "history" in includes:
            d["history"] = _include_exercise_history(ex, session)
        if "stats" in includes:
            d["stats"] = _include_exercise_stats(ex, session)
        if "training_risk" in includes:
            d["training_risk"] = training_risk_map.get(ex.id)
        results.append(d)

    return getter_response(
        "exercises", results,
        filters_applied=filters,
        match_info=match_info or None,
    )


def handle_set_exercises(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0
    all_warnings: list[str] = []

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        unsupported_load_fields = [
            field_name
            for field_name in (
                "load_input_mode",
                "bodyweight_fraction",
                "external_load_multiplier",
            )
            if field_name in set_fields
        ]
        if unsupported_load_fields:
            all_warnings.append(
                "Exercise load-model fields are not writable through chat tools."
            )
        # Accept "where" as alias for "match", plus top-level id/name
        match_spec = change.get("match") or change.get("where")
        if not match_spec and "id" in change and change["id"] is not None:
            match_spec = {"id": {"eq": change["id"]}}
        if not match_spec and "name" in change and op != "create":
            match_spec = {"name": {"fuzzy": change["name"]}}
        relations = change.get("relations", {})

        if op == "create":
            name = set_fields.get("name", "").strip()
            if not name:
                return error_response("exercises", "name required")
            existing = _fuzzy_match_exercise(name, session)
            if existing and existing.name.lower() == name.lower():
                return error_response(
                    "exercises", f"'{existing.name}' already exists"
                )
            exercise = Exercise(
                name=name,
                equipment=set_fields.get("equipment"),
                notes=set_fields.get("notes"),
            )
            session.add(exercise)
            session.flush()
            if relations.get("current_tissues"):
                w = _set_exercise_tissues(
                    exercise,
                    relations["current_tissues"].get("records", []),
                    session,
                )
                all_warnings.extend(w)
            results.append(_build_exercise_detail(exercise, session))
            created += 1

        elif op in ("update", "upsert"):
            from .shared import resolve_match
            # If no explicit match spec but set contains id, promote it to
            # match so callers can write {set: {id: 21, ...}} naturally.
            # Also promote id from set fields if still no match
            effective_match = match_spec
            if not effective_match and "id" in set_fields:
                effective_match = {"id": {"eq": set_fields["id"]}}
            recs, _, err = resolve_match(
                session, Exercise, effective_match,
                fuzzy_fields=["name"],
            )
            if not recs and op == "upsert":
                # When the intended match was by id, never silently create a
                # new exercise — return an error so the caller can investigate.
                if effective_match and "id" in effective_match:
                    return error_response(
                        "exercises",
                        f"Exercise not found for id match {effective_match['id']}; "
                        "cannot upsert by id",
                    )
                name = set_fields.get("name", "").strip()
                if not name:
                    return error_response(
                        "exercises",
                        "upsert requires name in set when no match is found",
                    )
                exercise = Exercise(
                    name=name,
                    equipment=set_fields.get("equipment"),
                    notes=set_fields.get("notes"),
                )
                session.add(exercise)
                session.flush()
                if relations.get("current_tissues"):
                    w = _set_exercise_tissues(
                        exercise,
                        relations["current_tissues"].get("records", []),
                        session,
                    )
                    all_warnings.extend(w)
                results.append(
                    _build_exercise_detail(exercise, session)
                )
                created += 1
            elif not recs:
                return error_response("exercises", err or "No match")
            else:
                for rec in recs:
                    if "name" in set_fields:
                        rec.name = set_fields["name"]
                    if "equipment" in set_fields:
                        rec.equipment = set_fields["equipment"]
                    if "notes" in set_fields:
                        rec.notes = set_fields["notes"]
                    session.add(rec)
                    if relations.get("current_tissues"):
                        w = _set_exercise_tissues(
                            rec,
                            relations["current_tissues"].get(
                                "records", []
                            ),
                            session,
                        )
                        all_warnings.extend(w)
                    results.append(
                        _build_exercise_detail(rec, session)
                    )
                    changed += 1

        elif op == "merge":
            from .shared import resolve_match
            src_recs, _, src_err = resolve_match(
                session, Exercise, match_spec,
                fuzzy_fields=["name"],
            )
            if not src_recs:
                return error_response(
                    "exercises", src_err or "Source not found"
                )
            tgt_spec = change.get("merge_into")
            tgt_recs, _, tgt_err = resolve_match(
                session, Exercise, tgt_spec,
                fuzzy_fields=["name"],
            )
            if not tgt_recs:
                return error_response(
                    "exercises", tgt_err or "Target not found"
                )
            source = src_recs[0]
            target = tgt_recs[0]
            if source.id == target.id:
                return error_response(
                    "exercises",
                    "Source and target are the same exercise",
                )

            # workout_sets — move all
            sets_moved = 0
            for s in session.exec(
                select(WorkoutSet).where(
                    WorkoutSet.exercise_id == source.id
                )
            ).all():
                s.exercise_id = target.id
                session.add(s)
                sets_moved += 1

            # program_day_exercises — move; delete dupes where target
            # already exists in the same program_day
            target_pde_days = {
                r.program_day_id
                for r in session.exec(
                    select(ProgramDayExercise).where(
                        ProgramDayExercise.exercise_id == target.id
                    )
                ).all()
            }
            pde_moved = pde_dupes = 0
            for pde in session.exec(
                select(ProgramDayExercise).where(
                    ProgramDayExercise.exercise_id == source.id
                )
            ).all():
                if pde.program_day_id in target_pde_days:
                    session.delete(pde)
                    pde_dupes += 1
                else:
                    pde.exercise_id = target.id
                    session.add(pde)
                    pde_moved += 1

            # exercise_tissues — merge; keep target's row on conflict,
            # move source-only tissues to target
            target_tissue_ids = {
                r.tissue_id
                for r in session.exec(
                    select(ExerciseTissue).where(
                        ExerciseTissue.exercise_id == target.id
                    )
                ).all()
            }
            tissues_moved = tissues_dupes = 0
            for et in session.exec(
                select(ExerciseTissue).where(
                    ExerciseTissue.exercise_id == source.id
                )
            ).all():
                if et.tissue_id in target_tissue_ids:
                    session.delete(et)
                    tissues_dupes += 1
                else:
                    et.exercise_id = target.id
                    session.add(et)
                    tissues_moved += 1

            session.delete(source)
            results.append({
                "merged": source.name,
                "into": target.name,
                "sets_moved": sets_moved,
                "program_days_moved": pde_moved,
                "program_days_dupes_removed": pde_dupes,
                "tissues_moved": tissues_moved,
                "tissues_dupes_removed": tissues_dupes,
            })
            changed += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, Exercise, match_spec,
                fuzzy_fields=["name"],
            )
            if not recs:
                return error_response("exercises", err or "No match")
            for rec in recs:
                for s in session.exec(
                    select(WorkoutSet).where(
                        WorkoutSet.exercise_id == rec.id
                    )
                ).all():
                    session.delete(s)
                for et in session.exec(
                    select(ExerciseTissue).where(
                        ExerciseTissue.exercise_id == rec.id
                    )
                ).all():
                    session.delete(et)
                for pde in session.exec(
                    select(ProgramDayExercise).where(
                        ProgramDayExercise.exercise_id == rec.id
                    )
                ).all():
                    session.delete(pde)
                session.delete(rec)
                results.append({"id": rec.id, "deleted": rec.name})
                deleted += 1

    session.commit()
    return setter_response(
        "exercises",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
        warnings=all_warnings or None,
    )


# =====================================================================
#  Tissues
# =====================================================================

GET_TISSUES_DEF = {
    "type": "function",
    "function": {
        "name": "get_tissues",
        "description": (
            "Get tissue records. Include readiness, volume, "
            "current_condition, tree, or history. "
            "Use readiness to check what is ready to train."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), name({eq,fuzzy,contains}), "
                        "type({eq,in}: muscle/tendon/joint)."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "readiness",
                            "current_condition",
                            "volume_7d",
                            "history",
                        ],
                    },
                },
                "limit": {"type": "integer", "default": 200},
            },
        },
    },
}

SET_TISSUES_DEF = {
    "type": "function",
    "function": {
        "name": "set_tissues",
        "description": (
            "Create, update, or delete tissue definitions."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["create", "update", "delete"],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "id({eq}) or name({eq,fuzzy}) "
                                    "for update/delete."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "name, display_name, type, "
                                    "recovery_hours, notes."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}


def handle_get_tissues(args: dict, session: Session) -> dict:
    includes = args.get("include", [])

    tissues = get_current_tissues(session)

    # Apply filters on the Python list
    filters = args.get("filters") or {}
    filtered = tissues
    if filters.get("type"):
        type_filter = filters["type"]
        if isinstance(type_filter, dict) and "in" in type_filter:
            filtered = [t for t in filtered if t.type in type_filter["in"]]
        elif isinstance(type_filter, dict) and "eq" in type_filter:
            filtered = [t for t in filtered if t.type == type_filter["eq"]]
        elif isinstance(type_filter, str):
            filtered = [t for t in filtered if t.type == type_filter]

    if filters.get("name"):
        name_filter = filters["name"]
        if isinstance(name_filter, dict) and "fuzzy" in name_filter:
            query = name_filter["fuzzy"]
            scored = [
                (t, fuzzy_score(query, t.name))
                for t in filtered
            ]
            scored = [(t, s) for t, s in scored if s >= 0.5]
            scored.sort(key=lambda x: -x[1])
            filtered = [t for t, _ in scored]
        elif isinstance(name_filter, dict) and "eq" in name_filter:
            filtered = [
                t for t in filtered if t.name == name_filter["eq"]
            ]

    if filters.get("id"):
        id_filter = filters["id"]
        if isinstance(id_filter, dict) and "eq" in id_filter:
            filtered = [t for t in filtered if t.id == id_filter["eq"]]
        elif isinstance(id_filter, dict) and "in" in id_filter:
            id_set = set(id_filter["in"])
            filtered = [t for t in filtered if t.id in id_set]

    limit = args.get("limit", 200)
    filtered = filtered[:limit]

    # Build result
    readiness_map = {}
    if "readiness" in includes:
        readiness_list = _compute_tissue_readiness(session)
        readiness_map = {r["tissue_name"]: r for r in readiness_list}

    conditions_map = {}
    if "current_condition" in includes:
        for c in get_all_current_conditions(session):
            conditions_map[c.tissue_id] = c

    results = []
    for t in filtered:
        d: dict = {
            "id": t.id,
            "name": t.name,
            "display_name": t.display_name,
            "type": t.type,
            "recovery_hours": t.recovery_hours,
            "notes": t.notes,
        }
        if "readiness" in includes:
            r = readiness_map.get(t.name, {})
            d["readiness"] = {
                "recovery_pct": r.get("recovery_pct", 100.0),
                "ready": r.get("ready", True),
                "hours_since": r.get("hours_since"),
                "effective_recovery_hours": r.get(
                    "effective_recovery_hours", t.recovery_hours
                ),
            }
        if "current_condition" in includes:
            cond = conditions_map.get(t.id)
            d["current_condition"] = {
                "status": cond.status if cond else "healthy",
                "severity": cond.severity if cond else 0,
                "max_loading_factor": (
                    cond.max_loading_factor if cond else None
                ),
                "rehab_protocol": (
                    cond.rehab_protocol if cond else None
                ),
            }
        results.append(d)

    return getter_response(
        "tissues", results, filters_applied=filters or None
    )


def _resolve_tissue_from_match(
    match_spec: dict, session: Session
) -> Tissue | None:
    """Resolve a tissue from a match spec supporting id or name."""
    # id match: { id: { eq: 27 } } or { id: 27 }
    id_spec = match_spec.get("id")
    if id_spec is not None:
        if isinstance(id_spec, dict):
            id_val = id_spec.get("eq")
        else:
            id_val = id_spec
        if id_val is not None:
            return session.exec(
                select(Tissue).where(Tissue.id == id_val)
            ).first()

    # name match: { name: { eq: "..." } } or { name: { fuzzy: "..." } }
    name_val = None
    name_spec = match_spec.get("name")
    if isinstance(name_spec, dict):
        name_val = name_spec.get("eq") or name_spec.get("fuzzy")
    elif isinstance(name_spec, str):
        name_val = name_spec
    if name_val:
        return _find_tissue_by_name(name_val, session)

    return None


def handle_set_tissues(args: dict, session: Session) -> dict:
    results = []
    created = changed = deleted = 0

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})

        if op == "create":
            name = set_fields.get("name", "").strip()
            if not name:
                return error_response("tissues", "name required")
            display_name = set_fields.get(
                "display_name", name.replace("_", " ").title()
            )
            tissue = Tissue(
                name=name,
                display_name=display_name,
                type=set_fields.get("type", "muscle"),
                recovery_hours=set_fields.get("recovery_hours", 48),
                notes=set_fields.get("notes"),
            )
            session.add(tissue)
            session.flush()
            results.append(record_to_dict(tissue))
            created += 1

        elif op in ("update", "delete"):
            match_spec = change.get("match")
            if not match_spec:
                return error_response(
                    "tissues", "match required for update/delete"
                )
            tissue = _resolve_tissue_from_match(match_spec, session)
            if not tissue:
                return error_response(
                    "tissues",
                    f"Tissue not found for match: {match_spec}",
                )

            if op == "delete":
                snapshot = record_to_dict(tissue)
                for et in session.exec(
                    select(ExerciseTissue).where(
                        ExerciseTissue.tissue_id == tissue.id
                    )
                ).all():
                    session.delete(et)
                session.delete(tissue)
                session.flush()
                results.append(snapshot)
                deleted += 1
            else:
                # Update in-place
                if "name" in set_fields:
                    tissue.name = set_fields["name"]
                if "display_name" in set_fields:
                    tissue.display_name = set_fields["display_name"]
                if "type" in set_fields:
                    tissue.type = set_fields["type"]
                if "recovery_hours" in set_fields:
                    tissue.recovery_hours = set_fields["recovery_hours"]
                if "notes" in set_fields:
                    tissue.notes = set_fields["notes"]
                session.add(tissue)
                session.flush()
                results.append(record_to_dict(tissue))
                changed += 1

    session.commit()
    return setter_response(
        "tissues",
        args["changes"][0]["operation"] if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
    )


# =====================================================================
#  Tissue Conditions
# =====================================================================

GET_TISSUE_CONDITIONS_DEF = {
    "type": "function",
    "function": {
        "name": "get_tissue_conditions",
        "description": (
            "Get tissue condition records. Filter by tissue name "
            "or status. Returns condition history or just current."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "tissue_name({eq,fuzzy}), status({eq,in}), "
                        "tissue_id({eq})."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["current", "history"],
                    },
                    "default": ["current"],
                },
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
}

SET_TISSUE_CONDITIONS_DEF = {
    "type": "function",
    "function": {
        "name": "set_tissue_conditions",
        "description": (
            "Log a tissue condition. Append-only: always creates "
            "a new record. Use when the user reports pain, "
            "tenderness, injury, or recovery status."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["create"],
                            },
                            "set": {
                                "type": "object",
                                "required": [
                                    "tissue_name",
                                    "status",
                                    "severity",
                                ],
                                "properties": {
                                    "tissue_name": {
                                        "type": "string",
                                    },
                                    "tissue_id": {
                                        "type": "integer",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": [
                                            "healthy",
                                            "tender",
                                            "injured",
                                            "rehabbing",
                                        ],
                                    },
                                    "severity": {
                                        "type": "integer",
                                        "description": "0-4",
                                    },
                                    "max_loading_factor": {
                                        "type": "number",
                                    },
                                    "recovery_hours_override": {
                                        "type": "number",
                                    },
                                    "rehab_protocol": {
                                        "type": "string",
                                    },
                                    "notes": {"type": "string"},
                                    "created_at": {
                                        "type": "string",
                                        "description": (
                                            "ISO 8601 date or datetime to "
                                            "backdate the record "
                                            "(e.g. '2026-02-05' or "
                                            "'2026-02-05T00:00:00'). "
                                            "Defaults to now."
                                        ),
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def handle_get_tissue_conditions(
    args: dict, session: Session
) -> dict:
    filters = args.get("filters") or {}
    includes = args.get("include", ["current"])
    limit = args.get("limit", 10)

    # Resolve tissue
    tissue = None
    tissue_name = filters.get("tissue_name")
    tissue_id = filters.get("tissue_id")
    if isinstance(tissue_name, dict):
        tn = tissue_name.get("fuzzy") or tissue_name.get("eq")
        if tn:
            tissue = _find_tissue_by_name(tn, session)
    elif isinstance(tissue_name, str):
        tissue = _find_tissue_by_name(tissue_name, session)
    elif isinstance(tissue_id, dict) and "eq" in tissue_id:
        tissue = session.get(Tissue, tissue_id["eq"])
    elif isinstance(tissue_id, int):
        tissue = session.get(Tissue, tissue_id)

    if "current" in includes and not tissue:
        # Return current conditions for all tissues
        conditions = get_all_current_conditions(session)
        results = []
        for c in conditions:
            t = session.get(Tissue, c.tissue_id)
            results.append({
                "tissue_name": t.name if t else f"id:{c.tissue_id}",
                "tissue_id": c.tissue_id,
                "status": c.status,
                "severity": c.severity,
                "max_loading_factor": c.max_loading_factor,
                "recovery_hours_override": c.recovery_hours_override,
                "rehab_protocol": c.rehab_protocol,
                "notes": c.notes,
                "updated_at": (
                    c.updated_at.isoformat() if c.updated_at else None
                ),
            })
        return getter_response(
            "tissue_conditions", results, filters_applied=filters
        )

    if not tissue:
        return error_response(
            "tissue_conditions",
            "Tissue not found. Provide tissue_name or tissue_id.",
        )

    stmt = (
        select(TissueCondition)
        .where(TissueCondition.tissue_id == tissue.id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(limit)
    )
    conditions = list(session.exec(stmt).all())
    results = [
        {
            "tissue_name": tissue.name,
            "tissue_id": tissue.id,
            "status": c.status,
            "severity": c.severity,
            "max_loading_factor": c.max_loading_factor,
            "recovery_hours_override": c.recovery_hours_override,
            "rehab_protocol": c.rehab_protocol,
            "notes": c.notes,
            "updated_at": (
                c.updated_at.isoformat() if c.updated_at else None
            ),
        }
        for c in conditions
    ]
    return getter_response(
        "tissue_conditions", results, filters_applied=filters
    )


def handle_set_tissue_conditions(
    args: dict, session: Session
) -> dict:
    results = []
    created = 0
    for change in args.get("changes", []):
        set_fields = change.get("set", {})
        # Resolve tissue
        tissue = None
        if set_fields.get("tissue_id"):
            tissue = session.get(Tissue, set_fields["tissue_id"])
        elif set_fields.get("tissue_name"):
            tissue = _find_tissue_by_name(
                set_fields["tissue_name"], session
            )
        if not tissue:
            return error_response(
                "tissue_conditions",
                f"Tissue '{set_fields.get('tissue_name', '')}' not found",
            )
        recorded_at_str = set_fields.get("created_at")
        if recorded_at_str:
            try:
                recorded_at = datetime.fromisoformat(recorded_at_str)
                if recorded_at.tzinfo is None:
                    recorded_at = recorded_at.replace(tzinfo=UTC)
            except ValueError:
                return error_response(
                    "tissue_conditions",
                    f"Invalid recorded_at value: '{recorded_at_str}'. "
                    "Use ISO 8601 format, e.g. '2026-02-05' or "
                    "'2026-02-05T12:00:00'.",
                )
        else:
            recorded_at = datetime.now(UTC)
        condition = TissueCondition(
            tissue_id=tissue.id,
            status=set_fields["status"],
            severity=set_fields.get("severity", 0),
            max_loading_factor=set_fields.get("max_loading_factor"),
            recovery_hours_override=set_fields.get(
                "recovery_hours_override"
            ),
            rehab_protocol=set_fields.get("rehab_protocol"),
            notes=set_fields.get("notes"),
            updated_at=recorded_at,
        )
        session.add(condition)
        session.flush()
        results.append({
            "tissue_name": tissue.name,
            "status": condition.status,
            "severity": condition.severity,
            "created_at": condition.updated_at.isoformat(),
        })
        created += 1

    session.commit()
    return setter_response(
        "tissue_conditions", "create", results,
        matched_count=created,
        created_count=created,
    )


# =====================================================================
#  Workout Sessions
# =====================================================================

GET_WORKOUT_SESSIONS_DEF = {
    "type": "function",
    "function": {
        "name": "get_workout_sessions",
        "description": (
            "Get workout session records with sets and exercise "
            "details. Filter by date, exercise name, or notes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), date({eq,gte,lte}), "
                        "exercise_name({fuzzy}) to filter "
                        "sessions containing that exercise."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["sets", "sets.exercise", "summary"],
                    },
                    "default": ["sets", "sets.exercise"],
                },
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
}

SET_WORKOUT_SESSIONS_DEF = {
    "type": "function",
    "function": {
        "name": "set_workout_sessions",
        "description": (
            "Record COMPLETED sets in an active workout. Use ONLY for logging "
            "sets that the user has actually performed (reps, weight, RPE). "
            "Do NOT use to add, remove, or reorder planned exercises — "
            "use modify_workout_plan for that instead. "
            "Add sets via the sets relation: mode=append to add new sets, "
            "mode=replace to overwrite all sets for a session."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "update",
                                    "delete",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "Required for update/delete. "
                                    "Filter by id or date using {eq} operator. "
                                    'Example: {"id": {"eq": 98}} or '
                                    '{"date": {"eq": "2026-03-10"}}.'
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "date (YYYY-MM-DD), notes, "
                                    "started_at, finished_at."
                                ),
                            },
                            "relations": {
                                "type": "object",
                                "properties": {
                                    "sets": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": [
                                                    "replace",
                                                    "append",
                                                ],
                                            },
                                            "records": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "exercise_name": {
                                                            "type": "string",
                                                        },
                                                        "exercise_id": {
                                                            "type": "integer",
                                                        },
                                                        "set_order": {
                                                            "type": "integer",
                                                        },
                                                        "reps": {
                                                            "type": "integer",
                                                        },
                                                        "weight": {
                                                            "type": "number",
                                                        },
                                                        "duration_secs": {
                                                            "type": "integer",
                                                        },
                                                        "distance_steps": {
                                                            "type": "integer",
                                                        },
                                                        "rpe": {
                                                            "type": "number",
                                                        },
                                                        "rep_completion": {
                                                            "type": "string",
                                                            "enum": [
                                                                "full",
                                                                "partial",
                                                                "failed",
                                                            ],
                                                        },
                                                        "notes": {
                                                            "type": "string",
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _compute_rep_completion(
    reps: int | None,
    target_rep_min: int | None,
    target_rep_max: int | None,
) -> str | None:
    """Derive rep_completion from reps vs target range. Returns None if
    reps or target range is missing."""
    if reps is None or target_rep_min is None or target_rep_max is None:
        return None
    if reps >= target_rep_max:
        return "full"
    if reps >= target_rep_min:
        return "partial"
    return "failed"


def _get_pde_targets_for_session(
    ws: WorkoutSession, session: Session
) -> dict[int, ProgramDayExercise]:
    """Return {exercise_id: ProgramDayExercise} for a workout session
    linked via PlannedSession. Falls back to active program if no
    planned session link."""
    planned = session.exec(
        select(PlannedSession).where(
            PlannedSession.workout_session_id == ws.id
        )
    ).first()
    if planned:
        pdes = session.exec(
            select(ProgramDayExercise).where(
                ProgramDayExercise.program_day_id
                == planned.program_day_id
            )
        ).all()
        return {pde.exercise_id: pde for pde in pdes}

    # Fallback: use the active program's exercises across all days
    active_program = session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()
    if not active_program:
        return {}
    pdes = session.exec(
        select(ProgramDayExercise)
        .join(ProgramDay)
        .where(ProgramDay.program_id == active_program.id)
    ).all()
    return {pde.exercise_id: pde for pde in pdes}


def _write_session_sets(
    ws: WorkoutSession,
    records: list[dict],
    session: Session,
    *,
    mode: str = "replace",
) -> tuple[list[str], list[dict]]:
    """Write workout sets. Returns (warnings, rep_check_exercises)."""
    warnings: list[str] = []
    rep_check_exercises: list[dict] = []

    if mode == "replace":
        old = session.exec(
            select(WorkoutSet).where(WorkoutSet.session_id == ws.id)
        ).all()
        for s in old:
            session.delete(s)
        set_order = 1
    else:
        max_order = session.exec(
            select(func.max(WorkoutSet.set_order)).where(
                WorkoutSet.session_id == ws.id
            )
        ).first() or 0
        set_order = max_order + 1

    pde_map = _get_pde_targets_for_session(ws, session)

    exercises_seen: dict[int, dict] = {}

    for rec in records:
        exercise_id = rec.get("exercise_id")
        exercise_name = rec.get("exercise_name")
        if not exercise_id and exercise_name:
            ex = _get_or_create_exercise(exercise_name, session)
            exercise_id = ex.id
        if not exercise_id:
            warnings.append("Set missing exercise, skipped")
            continue

        # Auto-compute rep_completion if not explicitly provided
        rep_completion = rec.get("rep_completion")
        if rep_completion is None:
            pde = pde_map.get(exercise_id)
            if pde:
                rep_completion = _compute_rep_completion(
                    rec.get("reps"),
                    pde.target_rep_min,
                    pde.target_rep_max,
                )

        order = rec.get("set_order", set_order)
        session.add(WorkoutSet(
            session_id=ws.id,
            exercise_id=exercise_id,
            set_order=order,
            reps=rec.get("reps"),
            weight=rec.get("weight"),
            duration_secs=rec.get("duration_secs"),
            distance_steps=rec.get("distance_steps"),
            rpe=rec.get("rpe"),
            rep_completion=rep_completion,
            notes=rec.get("notes"),
        ))
        set_order = max(set_order, order) + 1

        if exercise_id not in exercises_seen:
            exercises_seen[exercise_id] = rec

    # Build rep_check for exercises in active program
    for eid, first_rec in exercises_seen.items():
        pde = pde_map.get(eid)
        if pde and pde.target_rep_min is not None:
            exercise = session.get(Exercise, eid)
            rep_check_exercises.append({
                "exercise_name": exercise.name if exercise else f"id:{eid}",
                "weight": first_rec.get("weight"),
                "target_sets": pde.target_sets,
                "target_rep_min": pde.target_rep_min,
                "target_rep_max": pde.target_rep_max,
            })

    return warnings, rep_check_exercises


def handle_get_workout_sessions(
    args: dict, session: Session
) -> dict:
    filters = args.get("filters") or {}
    stmt = select(WorkoutSession)

    # Handle exercise_name filter specially (requires join)
    exercise_name = None
    if "exercise_name" in filters:
        en = filters.pop("exercise_name")
        exercise_name = en.get("fuzzy") if isinstance(en, dict) else en

    stmt, fuzzy_specs = apply_filters(stmt, WorkoutSession, filters)
    stmt = stmt.order_by(col(WorkoutSession.date).desc())
    limit = args.get("limit", 20)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())

    # Filter by exercise name if specified
    if exercise_name:
        exercise = _fuzzy_match_exercise(exercise_name, session)
        if not exercise:
            return getter_response(
                "workout_sessions", [],
                warnings=[f"Exercise '{exercise_name}' not found"],
            )
        filtered = []
        for ws in records:
            has_ex = session.exec(
                select(WorkoutSet)
                .where(WorkoutSet.session_id == ws.id)
                .where(WorkoutSet.exercise_id == exercise.id)
            ).first()
            if has_ex:
                filtered.append(ws)
        records = filtered

    return getter_response(
        "workout_sessions",
        [_build_session_summary(ws, session) for ws in records],
        filters_applied=filters or None,
    )


def handle_set_workout_sessions(
    args: dict, session: Session
) -> dict:
    results = []
    created = deleted = changed = 0
    all_warnings: list[str] = []
    all_rep_check: list[dict] = []

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")
        if not match_spec and "id" in change:
            match_spec = {"id": {"eq": change["id"]}}
        relations = change.get("relations", {})

        if op == "create":
            d = parse_date_val(
                set_fields.get("date", str(user_today()))
            )
            ws = WorkoutSession(
                date=d,
                notes=set_fields.get("notes"),
            )
            if set_fields.get("started_at"):
                ws.started_at = datetime.fromisoformat(
                    set_fields["started_at"]
                )
            if set_fields.get("finished_at"):
                ws.finished_at = datetime.fromisoformat(
                    set_fields["finished_at"]
                )
            session.add(ws)
            session.flush()
            if relations.get("sets"):
                w, rc = _write_session_sets(
                    ws,
                    relations["sets"].get("records", []),
                    session,
                    mode=relations["sets"].get("mode", "replace"),
                )
                all_warnings.extend(w)
                all_rep_check.extend(rc)
            result = _build_session_summary(ws, session)
            if all_rep_check:
                result["rep_check"] = all_rep_check
            results.append(result)
            created += 1

        elif op == "update":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, WorkoutSession, match_spec,
            )
            if not recs:
                return error_response(
                    "workout_sessions", err or "No match"
                )
            for rec in recs:
                if "date" in set_fields:
                    rec.date = parse_date_val(set_fields["date"])
                if "notes" in set_fields:
                    rec.notes = set_fields["notes"]
                if "started_at" in set_fields:
                    rec.started_at = datetime.fromisoformat(
                        set_fields["started_at"]
                    )
                if "finished_at" in set_fields:
                    rec.finished_at = datetime.fromisoformat(
                        set_fields["finished_at"]
                    )
                session.add(rec)
                if relations.get("sets"):
                    w, rc = _write_session_sets(
                        rec,
                        relations["sets"].get("records", []),
                        session,
                        mode=relations["sets"].get("mode", "replace"),
                    )
                    all_warnings.extend(w)
                    all_rep_check.extend(rc)
                result = _build_session_summary(rec, session)
                if all_rep_check:
                    result["rep_check"] = all_rep_check
                results.append(result)
                changed += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, WorkoutSession, match_spec
            )
            if not recs:
                return error_response(
                    "workout_sessions", err or "No match"
                )
            for rec in recs:
                for s in session.exec(
                    select(WorkoutSet).where(
                        WorkoutSet.session_id == rec.id
                    )
                ).all():
                    session.delete(s)
                session.delete(rec)
                results.append({"id": rec.id, "date": str(rec.date)})
                deleted += 1

    session.commit()
    return setter_response(
        "workout_sessions",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
        warnings=all_warnings or None,
    )


# =====================================================================
#  Workouts (Apple Watch imports)
# =====================================================================

GET_WORKOUTS_DEF = {
    "type": "function",
    "function": {
        "name": "get_workouts",
        "description": (
            "Get imported workout records from external devices "
            "(Apple Watch, etc). Filter by date, type, or source."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "date({eq,gte,lte}), workout_type({eq}), "
                        "source({eq})."
                    ),
                },
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
}

SET_WORKOUTS_DEF = {
    "type": "function",
    "function": {
        "name": "set_workouts",
        "description": (
            "Import or manage external workout records. "
            "Uses sync_key for deduplication on upsert."
        ),
        "parameters": {
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["operation"],
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "upsert",
                                    "delete",
                                ],
                            },
                            "match": {
                                "type": "object",
                                "description": (
                                    "sync_key({eq}), id({eq})."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "sync_key, date, workout_type, "
                                    "duration_minutes, "
                                    "active_calories, "
                                    "total_calories, distance_km, "
                                    "source."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}


def handle_get_workouts(args: dict, session: Session) -> dict:
    filters = args.get("filters")
    stmt = select(Workout)
    stmt, _ = apply_filters(stmt, Workout, filters)
    stmt = stmt.order_by(col(Workout.date).desc())
    limit = args.get("limit", 25)
    stmt = stmt.limit(limit)
    records = list(session.exec(stmt).all())
    return getter_response(
        "workouts",
        [record_to_dict(r) for r in records],
        filters_applied=filters,
    )


def handle_set_workouts(args: dict, session: Session) -> dict:
    results = []
    created = deleted = changed = 0

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")

        if op in ("create", "upsert"):
            sync_key = set_fields.get("sync_key")
            existing = None
            if sync_key:
                existing = session.exec(
                    select(Workout).where(
                        Workout.sync_key == sync_key
                    )
                ).first()
            if existing and op == "create":
                return error_response(
                    "workouts",
                    f"Workout with sync_key '{sync_key}' exists",
                )
            if existing:
                for k in (
                    "date", "workout_type", "duration_minutes",
                    "active_calories", "total_calories",
                    "distance_km", "source",
                ):
                    if k in set_fields:
                        val = set_fields[k]
                        if k == "date":
                            val = parse_date_val(val)
                        setattr(existing, k, val)
                session.add(existing)
                results.append(record_to_dict(existing))
                changed += 1
            else:
                w = Workout(
                    sync_key=set_fields.get("sync_key", ""),
                    date=parse_date_val(set_fields.get("date", str(user_today()))),
                    workout_type=set_fields.get("workout_type", ""),
                    duration_minutes=set_fields.get(
                        "duration_minutes", 0
                    ),
                    active_calories=set_fields.get(
                        "active_calories", 0
                    ),
                    total_calories=set_fields.get("total_calories"),
                    distance_km=set_fields.get("distance_km"),
                    source=set_fields.get("source"),
                )
                session.add(w)
                session.flush()
                results.append(record_to_dict(w))
                created += 1

        elif op == "delete":
            from .shared import resolve_match
            recs, _, err = resolve_match(
                session, Workout, match_spec
            )
            if not recs:
                return error_response("workouts", err or "No match")
            for rec in recs:
                session.delete(rec)
                results.append({"id": rec.id})
                deleted += 1

    session.commit()
    return setter_response(
        "workouts",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
    )


# =====================================================================
#  Workout context for system prompt (preserved from old system)
# =====================================================================


def get_workout_context(session: Session) -> dict[str, str]:
    """Build workout context strings for the system prompt."""
    exercises = session.exec(
        select(Exercise).order_by(Exercise.name)
    ).all()
    exercise_list = json.dumps(
        [
            {"id": e.id, "name": e.name, "equipment": e.equipment}
            for e in exercises
        ],
        separators=(",", ":"),
    ) if exercises else "[]"

    active_program = session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()
    routine_lines = []
    if active_program:
        program_days = session.exec(
            select(ProgramDay)
            .where(ProgramDay.program_id == active_program.id)
            .order_by(ProgramDay.sort_order)
        ).all()
        for pd in program_days:
            pdes = session.exec(
                select(ProgramDayExercise)
                .where(ProgramDayExercise.program_day_id == pd.id)
                .order_by(ProgramDayExercise.sort_order)
            ).all()
            for pde in pdes:
                exercise = session.get(Exercise, pde.exercise_id)
                name = (
                    exercise.name if exercise
                    else f"id:{pde.exercise_id}"
                )
                if pde.target_rep_min and pde.target_rep_max:
                    rep_range = (
                        f"{pde.target_sets}x{pde.target_rep_min}"
                        f"-{pde.target_rep_max}"
                    )
                elif pde.target_rep_min:
                    rep_range = (
                        f"{pde.target_sets}x{pde.target_rep_min}+"
                    )
                else:
                    rep_range = f"{pde.target_sets} sets"
                routine_lines.append(
                    f"  - {name}: {rep_range} ({pd.day_label})"
                )
    routine_summary = (
        "\n".join(routine_lines) if routine_lines
        else "  (no routine set)"
    )

    conditions = get_all_current_conditions(session)
    condition_lines = []
    for c in conditions:
        if c.status != "healthy":
            tissue = session.get(Tissue, c.tissue_id)
            name = (
                tissue.display_name if tissue else f"id:{c.tissue_id}"
            )
            parts = f"  - {name}: {c.status} (severity {c.severity}"
            if c.max_loading_factor is not None:
                parts += f", max_load={c.max_loading_factor}"
            if c.rehab_protocol:
                parts += f", rehab: {c.rehab_protocol}"
            parts += ")"
            condition_lines.append(parts)
    conditions_text = (
        "\n".join(condition_lines) if condition_lines
        else "  All tissues healthy."
    )

    return {
        "exercise_list": exercise_list,
        "routine_summary": routine_summary,
        "conditions_text": conditions_text,
    }


# =====================================================================
#  Workout planner tool
# =====================================================================

GET_WORKOUT_PLAN_DEF = {
    "type": "function",
    "function": {
        "name": "get_workout_plan",
        "description": (
            "Get today's workout plan. If a plan has been saved (via the Training page), "
            "returns it with progress (which exercises are done). Otherwise generates a "
            "suggestion. Use modify_workout_plan to add or remove exercises from a saved "
            "plan. Use set_workout_sessions ONLY to record actual completed sets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "as_of": {
                    "type": "string",
                    "description": "Optional date (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": [],
        },
    },
}


def handle_get_workout_plan(args: dict, session: "Session") -> dict:
    from datetime import date as date_type

    from app.planner import get_saved_plan, suggest_today

    as_of = None
    if args.get("as_of"):
        as_of = date_type.fromisoformat(args["as_of"])

    plan_date = as_of or date_type.today()

    # Check for saved plan first
    saved = get_saved_plan(session, plan_date)
    if saved:
        return _format_saved_plan(saved)

    # No saved plan — generate suggestion
    result = suggest_today(session, as_of=as_of)
    groups = result.get("groups") or []
    if not groups:
        return {"plan": result.get("message", "No plan available."), "saved": False}

    lines = [
        "Ranked Workout Categories:",
        "",
    ]
    for group in groups[:4]:
        lines.extend([
            f"{group['day_label']}: {round(group['readiness_score'] * 100)}% ready",
            f"  Regions: {', '.join(group.get('target_regions', []))}",
            f"  Available: {group.get('available_count', 0)}/{group.get('exercise_count', 0)}",
            f"  Rationale: {group.get('rationale', '')}",
        ])
        if group.get("ready_tomorrow_count"):
            lines.append(f"  Ready tomorrow: {group['ready_tomorrow_count']}")
        for ex in group.get("exercises", [])[:4]:
            status = ex.get("planner_status", "ready")
            if ex.get("selectable", True):
                weight_str = f" @ {ex['target_weight']} lb" if ex.get("target_weight") else ""
                note = f" ({ex['overload_note']})" if ex.get("overload_note") else ""
                lines.append(
                    f"    - {ex['exercise_name']}: {ex['target_sets']}x{ex['target_reps']}"
                    f"{weight_str} [{ex['rep_scheme']}] ({status}){note}"
                )
            else:
                lines.append(
                    f"    - {ex['exercise_name']}: blocked today ({ex.get('planner_reason', status)})"
                )
        lines.append("")

    if result.get("filtered_tissues"):
        filtered_labels = [
            f"{item['target_label']} ({item['reason']})"
            for item in result["filtered_tissues"]
        ]
        lines.extend([
            "",
            "Filtered today:",
            "  - " + ", ".join(filtered_labels),
        ])

    lines.append("")
    lines.append("This plan is not saved yet. The user can save it from the Training page.")

    return {"plan": "\n".join(lines), "saved": False}


def _format_saved_plan(saved: dict) -> dict:
    status = saved["status"]
    lines = [
        f"Today's Plan: {saved['day_label']} [{status}]",
        f"Regions: {', '.join(saved.get('target_regions', []))}",
        "",
    ]

    remaining = []
    completed = []
    for ex in saved.get("exercises", []):
        name = ex["exercise_name"]
        rep_range = f"{ex.get('target_rep_min', '?')}-{ex.get('target_rep_max', '?')}"
        weight_str = f" @ {ex['target_weight']} lb" if ex.get("target_weight") else ""
        scheme = f" [{ex['rep_scheme']}]" if ex.get("rep_scheme") else ""
        target = f"{ex['target_sets']}x{rep_range}{weight_str}{scheme}"

        if ex["done"]:
            sets_info = ", ".join(
                f"{s['reps']}@{s.get('weight', '?')} RPE {s.get('rpe', '?')}"
                for s in ex.get("completed_sets", [])
            )
            completed.append(f"  [done] {name}: {sets_info}")
        else:
            done_count = ex["sets_done"]
            if done_count > 0:
                remaining.append(f"  [in progress {done_count}/{ex['target_sets']}] {name}: {target}")
            else:
                remaining.append(f"  [ ] {name}: {target}")

    if remaining:
        lines.append("Remaining:")
        lines.extend(remaining)
    if completed:
        lines.append("\nCompleted:")
        lines.extend(completed)

    if not remaining and completed:
        lines.append("\nAll exercises done! Ask the user if they want to finish the workout.")

    return {"plan": "\n".join(lines), "saved": True, "status": status}


# =====================================================================
#  Modify workout plan tool
# =====================================================================

MODIFY_WORKOUT_PLAN_DEF = {
    "type": "function",
    "function": {
        "name": "modify_workout_plan",
        "description": (
            "Add or remove exercises from today's SAVED workout plan "
            "(the pre-workout exercise list, not logged sets). "
            "Use this when the user wants to adjust which exercises are in today's plan "
            "before or during a workout. "
            "Do NOT use set_workout_sessions for this purpose."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "remove"],
                    "description": (
                        "'add' appends exercises to the plan. "
                        "'remove' deletes exercises by exercise_id."
                    ),
                },
                "exercises": {
                    "type": "array",
                    "description": (
                        "For 'add': list of exercise objects with exercise_id (required), "
                        "and optionally target_sets, target_reps (e.g. '8-12'), "
                        "rep_scheme ('heavy'|'medium'|'volume'), target_weight. "
                        "For 'remove': list of objects with exercise_id."
                    ),
                    "items": {
                        "type": "object",
                        "required": ["exercise_id"],
                        "properties": {
                            "exercise_id": {"type": "integer"},
                            "target_sets": {"type": "integer", "default": 3},
                            "target_reps": {"type": "string", "default": "8-12"},
                            "rep_scheme": {"type": "string", "enum": ["heavy", "medium", "volume"]},
                            "target_weight": {"type": "number"},
                        },
                    },
                },
                "as_of": {
                    "type": "string",
                    "description": "Date (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["action", "exercises"],
        },
    },
}


def handle_modify_workout_plan(args: dict, session: "Session") -> dict:
    from datetime import date as date_type

    from app.planner import add_exercises_to_plan, remove_exercises_from_plan

    as_of = None
    if args.get("as_of"):
        as_of = date_type.fromisoformat(args["as_of"])
    plan_date = as_of or date_type.today()

    action = args.get("action")
    exercises = args.get("exercises", [])

    try:
        if action == "add":
            updated = add_exercises_to_plan(session, plan_date, exercises)
        elif action == "remove":
            ids = [e["exercise_id"] for e in exercises]
            updated = remove_exercises_from_plan(session, plan_date, ids)
        else:
            return {"error": f"Unknown action: {action}"}
    except ValueError as e:
        return {"error": str(e)}

    names = [e.get("exercise_name") or f"exercise {e.get('exercise_id')}" for e in exercises]
    verb = "Added" if action == "add" else "Removed"
    summary = f"{verb}: {', '.join(names)}. Plan now has {len(updated['exercises'])} exercises."
    return {"result": summary, "plan": _format_saved_plan(updated)}


# =====================================================================
#  Tool registration
# =====================================================================

WORKOUT_TOOL_DEFINITIONS = [
    GET_EXERCISES_DEF, SET_EXERCISES_DEF,
    GET_TISSUES_DEF, SET_TISSUES_DEF,
    GET_TISSUE_CONDITIONS_DEF, SET_TISSUE_CONDITIONS_DEF,
    GET_WORKOUT_SESSIONS_DEF, SET_WORKOUT_SESSIONS_DEF,
    GET_WORKOUTS_DEF, SET_WORKOUTS_DEF,
    GET_WORKOUT_PLAN_DEF, MODIFY_WORKOUT_PLAN_DEF,
]

WORKOUT_TOOL_HANDLERS = {
    "get_exercises": handle_get_exercises,
    "set_exercises": handle_set_exercises,
    "get_tissues": handle_get_tissues,
    "set_tissues": handle_set_tissues,
    "get_tissue_conditions": handle_get_tissue_conditions,
    "set_tissue_conditions": handle_set_tissue_conditions,
    "get_workout_sessions": handle_get_workout_sessions,
    "set_workout_sessions": handle_set_workout_sessions,
    "get_workouts": handle_get_workouts,
    "set_workouts": handle_set_workouts,
    "get_workout_plan": handle_get_workout_plan,
    "modify_workout_plan": handle_modify_workout_plan,
}
