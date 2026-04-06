from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from typing import Literal

from sqlmodel import Session, col, select

from app.exercise_loads import bodyweight_by_date
from app.exercise_loads import effective_weight as calc_effective_weight
from app.models import Exercise, PlannedSession, ProgramDay, ProgramDayExercise, WeightLog, WorkoutSession, WorkoutSet

RepScheme = Literal["heavy", "medium", "volume"]

REP_SCHEME_VERSION = 2
_WEIGHT_AWARE_LOAD_INPUT_MODES = {"bodyweight", "mixed", "assisted_bodyweight"}


def empty_scheme_history() -> dict[str, dict | None]:
    return {
        "heavy": None,
        "medium": None,
        "volume": None,
    }


def canonical_rep_scheme(
    value: str | None,
    *,
    version: int | None = None,
) -> RepScheme | None:
    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None

    if version is None or version < REP_SCHEME_VERSION:
        legacy_map: dict[str, RepScheme] = {
            "heavy": "heavy",
            "medium": "medium",
            "volume": "medium",
            "light": "volume",
        }
        if normalized in legacy_map:
            return legacy_map[normalized]
    else:
        current_map: dict[str, RepScheme] = {
            "heavy": "heavy",
            "medium": "medium",
            "volume": "volume",
            "light": "volume",
        }
        if normalized in current_map:
            return current_map[normalized]

    return infer_rep_scheme_from_text(normalized)


def infer_rep_scheme_from_text(value: str) -> RepScheme | None:
    numbers = [int(match) for match in re.findall(r"\d+", value)]
    if not numbers:
        return None

    if "x" in value and len(numbers) >= 2:
        rep_min = numbers[1]
        rep_max = numbers[2] if len(numbers) >= 3 else numbers[1]
    elif len(numbers) >= 2:
        rep_min = numbers[0]
        rep_max = numbers[1]
    else:
        rep_min = numbers[0]
        rep_max = numbers[0]

    if rep_max <= 6:
        return "heavy"
    if rep_min >= 15 or rep_max >= 18:
        return "volume"
    if rep_min >= 12 and rep_max >= 15:
        return "volume"
    return "medium"


def infer_logged_rep_scheme(sets: list[WorkoutSet]) -> RepScheme | None:
    rep_values = [int(workout_set.reps) for workout_set in sets if workout_set.reps is not None and workout_set.reps > 0]
    if not rep_values:
        return None

    total_reps = sum(rep_values)
    avg_reps = total_reps / len(rep_values)
    max_reps = max(rep_values)
    min_reps = min(rep_values)

    if max_reps <= 6 and total_reps <= 20:
        return "heavy"
    if max_reps >= 15 or total_reps >= 36 or avg_reps >= 13 or min_reps >= 12:
        return "volume"
    return "medium"


def build_scheme_history(session_rows: list[dict]) -> dict[str, dict | None]:
    history = empty_scheme_history()
    for session_row in session_rows:
        rep_scheme = session_row.get("rep_scheme")
        if rep_scheme not in history or history[rep_scheme] is not None:
            continue
        history[rep_scheme] = {
            "date": session_row["date"],
            "rep_scheme": rep_scheme,
            "sets": session_row["sets"],
            "max_weight": session_row["max_weight"],
            "total_volume": session_row["total_volume"],
        }
        if all(history.values()):
            break
    return history


def get_exercise_scheme_history_map(
    session: Session,
    exercise_ids: list[int] | set[int] | tuple[int, ...],
    *,
    limit: int = 40,
    exclude_session_ids: list[int] | set[int] | tuple[int, ...] | None = None,
) -> dict[int, dict[str, dict | None]]:
    sessions_by_exercise = get_exercise_history_map(
        session,
        exercise_ids,
        limit=limit,
        exclude_session_ids=exclude_session_ids,
    )
    return {
        exercise_id: build_scheme_history(session_rows)
        for exercise_id, session_rows in sessions_by_exercise.items()
    }


def get_exercise_history_map(
    session: Session,
    exercise_ids: list[int] | set[int] | tuple[int, ...],
    *,
    limit: int = 20,
    exclude_session_ids: list[int] | set[int] | tuple[int, ...] | None = None,
) -> dict[int, list[dict]]:
    ordered_exercise_ids = [exercise_id for exercise_id in dict.fromkeys(exercise_ids) if exercise_id is not None]
    if not ordered_exercise_ids:
        return {}

    exercises = {
        exercise.id: exercise
        for exercise in session.exec(
            select(Exercise).where(col(Exercise.id).in_(ordered_exercise_ids))
        ).all()
        if exercise.id is not None
    }
    if not exercises:
        return {}

    bodyweight_lookup: dict[date, float] = {}
    if any(
        exercise.load_input_mode in _WEIGHT_AWARE_LOAD_INPUT_MODES
        for exercise in exercises.values()
    ):
        weight_logs = session.exec(select(WeightLog)).all()
        bodyweight_lookup = bodyweight_by_date(weight_logs)

    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(col(WorkoutSet.exercise_id).in_(ordered_exercise_ids))
        .order_by(WorkoutSet.exercise_id, col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    excluded_ids = [session_id for session_id in dict.fromkeys(exclude_session_ids or ()) if session_id is not None]
    if excluded_ids:
        stmt = stmt.where(~col(WorkoutSet.session_id).in_(excluded_ids))
    rows = session.exec(stmt).all()

    grouped_sets: dict[int, dict[date, list[WorkoutSet]]] = defaultdict(dict)
    seen_dates: dict[int, set[date]] = defaultdict(set)
    seen_session_dates: set[date] = set()
    for workout_set, session_date in rows:
        exercise_id = workout_set.exercise_id
        if session_date not in seen_dates[exercise_id]:
            if len(seen_dates[exercise_id]) >= limit:
                continue
            grouped_sets[exercise_id][session_date] = []
            seen_dates[exercise_id].add(session_date)
            seen_session_dates.add(session_date)
        grouped_sets[exercise_id][session_date].append(workout_set)

    planned_scheme_lookup = _planned_scheme_lookup(
        session,
        exercise_ids=ordered_exercise_ids,
        dates=seen_session_dates,
    )

    output: dict[int, list[dict]] = {}
    for exercise_id in ordered_exercise_ids:
        exercise = exercises.get(exercise_id)
        if not exercise:
            continue
        session_rows: list[dict] = []
        for session_date, sets in grouped_sets.get(exercise_id, {}).items():
            session_rows.append(
                _summarize_exercise_session(
                    exercise=exercise,
                    session_date=session_date,
                    sets=sets,
                    bodyweight_lookup=bodyweight_lookup,
                    planned_scheme=planned_scheme_lookup.get((exercise_id, session_date)),
                )
            )
        output[exercise_id] = session_rows
    return output


def _summarize_exercise_session(
    *,
    exercise: Exercise,
    session_date: date,
    sets: list[WorkoutSet],
    bodyweight_lookup: dict[date, float],
    planned_scheme: RepScheme | None,
) -> dict:
    if exercise.load_input_mode in _WEIGHT_AWARE_LOAD_INPUT_MODES:
        effective_weights = [
            calc_effective_weight(exercise, workout_set, bodyweight_lookup, session_date)
            for workout_set in sets
        ]
        max_weight = max(effective_weights) if effective_weights else 0.0
        total_volume = sum(
            (workout_set.reps or 0) * effective_weight
            for workout_set, effective_weight in zip(sets, effective_weights)
        )
    else:
        max_weight = max((workout_set.weight or 0) for workout_set in sets)
        total_volume = sum((workout_set.reps or 0) * (workout_set.weight or 0) for workout_set in sets)

    rep_scheme = planned_scheme or infer_logged_rep_scheme(sets) or "medium"
    rep_completions = [workout_set.rep_completion for workout_set in sets if workout_set.rep_completion]

    return {
        "date": str(session_date),
        "rep_scheme": rep_scheme,
        "sets": [
            {
                "set_order": workout_set.set_order,
                "reps": workout_set.reps,
                "weight": workout_set.weight,
                "duration_secs": workout_set.duration_secs,
                "distance_steps": workout_set.distance_steps,
                "rpe": workout_set.rpe,
                "rep_completion": workout_set.rep_completion,
                "notes": workout_set.notes,
            }
            for workout_set in sets
        ],
        "max_weight": round(max_weight, 2),
        "total_volume": round(total_volume, 2),
        "rep_completions": rep_completions,
        "all_full": bool(sets) and all(workout_set.rep_completion == "full" for workout_set in sets),
    }


def _planned_scheme_lookup(
    session: Session,
    *,
    exercise_ids: list[int],
    dates: set[date],
) -> dict[tuple[int, date], RepScheme]:
    if not exercise_ids or not dates:
        return {}

    rows = session.exec(
        select(
            PlannedSession.id,
            PlannedSession.date,
            ProgramDayExercise.exercise_id,
            ProgramDayExercise.notes,
        )
        .join(ProgramDay, PlannedSession.program_day_id == ProgramDay.id)
        .join(ProgramDayExercise, ProgramDayExercise.program_day_id == ProgramDay.id)
        .where(
            col(PlannedSession.date).in_(list(dates)),
            col(ProgramDayExercise.exercise_id).in_(exercise_ids),
        )
        .order_by(col(PlannedSession.id).desc())
    ).all()

    planned_scheme_lookup: dict[tuple[int, date], RepScheme] = {}
    for _planned_session_id, planned_date, exercise_id, notes in rows:
        key = (exercise_id, planned_date)
        if key in planned_scheme_lookup:
            continue
        rep_scheme = _rep_scheme_from_notes(notes)
        if rep_scheme is not None:
            planned_scheme_lookup[key] = rep_scheme
    return planned_scheme_lookup


def _rep_scheme_from_notes(notes: str | None) -> RepScheme | None:
    if not notes:
        return None
    try:
        meta = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return None
    return canonical_rep_scheme(
        meta.get("rep_scheme"),
        version=meta.get("rep_scheme_version"),
    )
