"""Workout domain LLM tools: exercises, tissues, tissue_conditions,
workout_sessions, routine_exercises, workouts.

Each table gets a get_<table> getter and a set_<table> setter following
the shared contract.
"""

import difflib
import json
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    RoutineExercise,
    Tissue,
    TissueCondition,
    Workout,
    WorkoutSession,
    WorkoutSet,
)
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
            "Include history and stats for progression data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "id({eq,in}), name({eq,fuzzy,contains}), "
                        "equipment({eq,contains})."
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
                        ],
                    },
                    "default": ["current_tissues"],
                },
                "limit": {"type": "integer", "default": 50},
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
                                "description": (
                                    "name, equipment, notes."
                                ),
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

    routine_entry = session.exec(
        select(RoutineExercise).where(
            RoutineExercise.exercise_id == exercise.id
        )
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
            "sets": routine_entry.target_sets,
            "rep_min": routine_entry.target_rep_min,
            "rep_max": routine_entry.target_rep_max,
        } if routine_entry else None,
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

    results = []
    for ex in records:
        d = _build_exercise_detail(ex, session)
        if "history" in includes:
            d["history"] = _include_exercise_history(ex, session)
        if "stats" in includes:
            d["stats"] = _include_exercise_stats(ex, session)
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
            sets_moved = 0
            for s in session.exec(
                select(WorkoutSet).where(
                    WorkoutSet.exercise_id == source.id
                )
            ).all():
                s.exercise_id = target.id
                session.add(s)
                sets_moved += 1
            for re in session.exec(
                select(RoutineExercise).where(
                    RoutineExercise.exercise_id == source.id
                )
            ).all():
                re.exercise_id = target.id
                session.add(re)
            for et in session.exec(
                select(ExerciseTissue).where(
                    ExerciseTissue.exercise_id == source.id
                )
            ).all():
                session.delete(et)
            session.delete(source)
            results.append({
                "merged": source.name,
                "into": target.name,
                "sets_moved": sets_moved,
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
                for re in session.exec(
                    select(RoutineExercise).where(
                        RoutineExercise.exercise_id == rec.id
                    )
                ).all():
                    session.delete(re)
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
        )
        session.add(condition)
        session.flush()
        results.append({
            "tissue_name": tissue.name,
            "status": condition.status,
            "severity": condition.severity,
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
            "Create, update, or delete workout sessions. "
            "Add sets via the sets relation: use mode=append to add sets "
            "to an existing session, mode=replace to overwrite all sets. "
            "To update rep_completion, replace sets with updated fields."
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

    # Group records by exercise for rep_check
    exercises_seen: dict[int, dict] = {}

    for rec in records:
        # Resolve exercise
        exercise_id = rec.get("exercise_id")
        exercise_name = rec.get("exercise_name")
        if not exercise_id and exercise_name:
            ex = _get_or_create_exercise(exercise_name, session)
            exercise_id = ex.id
        if not exercise_id:
            warnings.append("Set missing exercise, skipped")
            continue

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
            rep_completion=rec.get("rep_completion"),
            notes=rec.get("notes"),
        ))
        set_order = max(set_order, order) + 1

        if exercise_id not in exercises_seen:
            exercises_seen[exercise_id] = rec

    # Build rep_check for exercises in active routine
    for eid, first_rec in exercises_seen.items():
        routine_entry = session.exec(
            select(RoutineExercise)
            .where(RoutineExercise.exercise_id == eid)
            .where(RoutineExercise.active == 1)
        ).first()
        if routine_entry and routine_entry.target_rep_min is not None:
            exercise = session.get(Exercise, eid)
            rep_check_exercises.append({
                "exercise_name": exercise.name if exercise else f"id:{eid}",
                "weight": first_rec.get("weight"),
                "target_sets": routine_entry.target_sets,
                "target_rep_min": routine_entry.target_rep_min,
                "target_rep_max": routine_entry.target_rep_max,
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
                set_fields.get("date", str(date.today()))
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
#  Routine Exercises
# =====================================================================

GET_ROUTINE_EXERCISES_DEF = {
    "type": "function",
    "function": {
        "name": "get_routine_exercises",
        "description": (
            "Get routine exercise entries with linked exercise "
            "details and last performance data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": (
                        "active({eq}: true/false), "
                        "exercise_name({fuzzy})."
                    ),
                },
                "include": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "exercise",
                            "last_performance",
                        ],
                    },
                    "default": ["exercise", "last_performance"],
                },
            },
        },
    },
}

SET_ROUTINE_EXERCISES_DEF = {
    "type": "function",
    "function": {
        "name": "set_routine_exercises",
        "description": (
            "Add, update, or remove exercises from the training "
            "routine. Each exercise appears at most once."
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
                                    "exercise_name({eq,fuzzy})."
                                ),
                            },
                            "set": {
                                "type": "object",
                                "description": (
                                    "exercise_name, target_sets, "
                                    "target_rep_min, target_rep_max, "
                                    "sort_order, active (bool), "
                                    "notes."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}


def handle_get_routine_exercises(
    args: dict, session: Session
) -> dict:
    filters = args.get("filters") or {}
    includes = args.get("include", ["exercise", "last_performance"])

    stmt = select(RoutineExercise).order_by(RoutineExercise.sort_order)

    # Filter by active
    active_filter = filters.get("active")
    if active_filter is not None:
        if isinstance(active_filter, dict) and "eq" in active_filter:
            val = 1 if active_filter["eq"] else 0
        else:
            val = 1 if active_filter else 0
        stmt = stmt.where(RoutineExercise.active == val)

    routine = list(session.exec(stmt).all())

    # Filter by exercise name
    exercise_name = filters.get("exercise_name")
    if exercise_name:
        en = (
            exercise_name.get("fuzzy")
            if isinstance(exercise_name, dict)
            else exercise_name
        )
        if en:
            ex = _fuzzy_match_exercise(en, session)
            if ex:
                routine = [
                    r for r in routine if r.exercise_id == ex.id
                ]
            else:
                routine = []

    results = []
    for re in routine:
        exercise = session.get(Exercise, re.exercise_id)
        d: dict = {
            "id": re.id,
            "exercise_id": re.exercise_id,
            "exercise_name": (
                exercise.name if exercise else f"id:{re.exercise_id}"
            ),
            "target_sets": re.target_sets,
            "target_rep_min": re.target_rep_min,
            "target_rep_max": re.target_rep_max,
            "sort_order": re.sort_order,
            "active": bool(re.active),
            "notes": re.notes,
        }
        if "last_performance" in includes and exercise:
            last_set = session.exec(
                select(WorkoutSet)
                .where(WorkoutSet.exercise_id == exercise.id)
                .order_by(col(WorkoutSet.created_at).desc())
                .limit(1)
            ).first()
            d["last_performance"] = {
                "weight": last_set.weight if last_set else None,
                "reps": last_set.reps if last_set else None,
                "rep_completion": (
                    last_set.rep_completion if last_set else None
                ),
            }
        results.append(d)

    return getter_response(
        "routine_exercises", results, filters_applied=filters or None
    )


def handle_set_routine_exercises(
    args: dict, session: Session
) -> dict:
    results = []
    created = deleted = changed = 0

    for change in args.get("changes", []):
        op = change["operation"]
        set_fields = change.get("set", {})
        match_spec = change.get("match")

        if op == "create":
            ex_name = set_fields.get("exercise_name", "")
            if not ex_name:
                return error_response(
                    "routine_exercises", "exercise_name required"
                )
            exercise = _get_or_create_exercise(ex_name, session)
            existing = session.exec(
                select(RoutineExercise).where(
                    RoutineExercise.exercise_id == exercise.id
                )
            ).first()
            if existing:
                return error_response(
                    "routine_exercises",
                    f"'{exercise.name}' already in routine",
                )
            re = RoutineExercise(
                exercise_id=exercise.id,
                target_sets=set_fields.get("target_sets", 3),
                target_rep_min=set_fields.get("target_rep_min"),
                target_rep_max=set_fields.get("target_rep_max"),
                sort_order=set_fields.get("sort_order", 0),
                active=1 if set_fields.get("active", True) else 0,
                notes=set_fields.get("notes"),
            )
            session.add(re)
            session.flush()
            results.append({
                "id": re.id,
                "exercise_name": exercise.name,
            })
            created += 1

        elif op == "update":
            # Resolve exercise from match
            ex_name = None
            if match_spec and "exercise_name" in match_spec:
                en = match_spec["exercise_name"]
                ex_name = (
                    en.get("fuzzy") or en.get("eq")
                    if isinstance(en, dict) else en
                )
            if not ex_name:
                return error_response(
                    "routine_exercises",
                    "exercise_name required in match",
                )
            exercise = _fuzzy_match_exercise(ex_name, session)
            if not exercise:
                return error_response(
                    "routine_exercises",
                    f"Exercise '{ex_name}' not found",
                )
            re = session.exec(
                select(RoutineExercise).where(
                    RoutineExercise.exercise_id == exercise.id
                )
            ).first()
            if not re:
                return error_response(
                    "routine_exercises",
                    f"'{exercise.name}' not in routine",
                )
            if "target_sets" in set_fields:
                re.target_sets = set_fields["target_sets"]
            if "target_rep_min" in set_fields:
                re.target_rep_min = set_fields["target_rep_min"]
            if "target_rep_max" in set_fields:
                re.target_rep_max = set_fields["target_rep_max"]
            if "sort_order" in set_fields:
                re.sort_order = set_fields["sort_order"]
            if "active" in set_fields:
                re.active = 1 if set_fields["active"] else 0
            if "notes" in set_fields:
                re.notes = set_fields["notes"]
            session.add(re)
            results.append({
                "id": re.id,
                "exercise_name": exercise.name,
            })
            changed += 1

        elif op == "delete":
            ex_name = None
            if match_spec and "exercise_name" in match_spec:
                en = match_spec["exercise_name"]
                ex_name = (
                    en.get("fuzzy") or en.get("eq")
                    if isinstance(en, dict) else en
                )
            if not ex_name:
                return error_response(
                    "routine_exercises",
                    "exercise_name required in match",
                )
            exercise = _fuzzy_match_exercise(ex_name, session)
            if not exercise:
                return error_response(
                    "routine_exercises",
                    f"Exercise '{ex_name}' not found",
                )
            re = session.exec(
                select(RoutineExercise).where(
                    RoutineExercise.exercise_id == exercise.id
                )
            ).first()
            if not re:
                return error_response(
                    "routine_exercises",
                    f"'{exercise.name}' not in routine",
                )
            session.delete(re)
            results.append({"removed": exercise.name})
            deleted += 1

    session.commit()
    return setter_response(
        "routine_exercises",
        op if args.get("changes") else "noop",
        results,
        matched_count=len(results),
        created_count=created,
        changed_count=changed,
        deleted_count=deleted,
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
                    date=parse_date_val(set_fields.get("date", str(date.today()))),
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

    routine = session.exec(
        select(RoutineExercise)
        .where(RoutineExercise.active == 1)
        .order_by(RoutineExercise.sort_order)
    ).all()
    routine_lines = []
    for re in routine:
        exercise = session.get(Exercise, re.exercise_id)
        name = exercise.name if exercise else f"id:{re.exercise_id}"
        if re.target_rep_min and re.target_rep_max:
            rep_range = (
                f"{re.target_sets}x{re.target_rep_min}"
                f"-{re.target_rep_max}"
            )
        elif re.target_rep_min:
            rep_range = f"{re.target_sets}x{re.target_rep_min}+"
        else:
            rep_range = f"{re.target_sets} sets"
        routine_lines.append(f"  - {name}: {rep_range}")
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
#  Tool registration
# =====================================================================

WORKOUT_TOOL_DEFINITIONS = [
    GET_EXERCISES_DEF, SET_EXERCISES_DEF,
    GET_TISSUES_DEF, SET_TISSUES_DEF,
    GET_TISSUE_CONDITIONS_DEF, SET_TISSUE_CONDITIONS_DEF,
    GET_WORKOUT_SESSIONS_DEF, SET_WORKOUT_SESSIONS_DEF,
    GET_ROUTINE_EXERCISES_DEF, SET_ROUTINE_EXERCISES_DEF,
    GET_WORKOUTS_DEF, SET_WORKOUTS_DEF,
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
    "get_routine_exercises": handle_get_routine_exercises,
    "set_routine_exercises": handle_set_routine_exercises,
    "get_workouts": handle_get_workouts,
    "set_workouts": handle_set_workouts,
}
