"""Thin CRUD helpers over ``PlannedSession`` + ``ProgramDay`` +
``ProgramDayExercise`` used by the Training page and WorkoutSetEditor.

Extracted from the (now-deleted) auto-planner ``app.planner``. Only the
state-mutating helpers consumed by surviving routes live here; the old
auto-generation / rehab / protection logic has been removed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime

from sqlmodel import Session, col, select

from app.exercise_history import empty_scheme_history, get_exercise_scheme_history_map
from app.models import (
    Exercise,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    WorkoutSession,
    WorkoutSet,
)
from app.strength_model import check_burnout_availability, check_heavy_availability
from app.units import legacy_metric_fields


def complete_plan(session: Session, plan_date: date) -> dict:
    """Mark the latest plan for ``plan_date`` as completed and stamp the
    associated WorkoutSession's finished_at timestamp. Idempotent — calling
    it on an already-completed plan is a no-op.
    """
    planned = _latest_planned_session(session, plan_date)
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    now = datetime.now(UTC)

    if planned.status != "completed":
        planned.status = "completed"
        session.add(planned)

    if planned.workout_session_id is not None:
        ws = session.get(WorkoutSession, planned.workout_session_id)
        if ws is not None and ws.finished_at is None:
            ws.finished_at = now
            session.add(ws)

    session.commit()
    session.refresh(planned)
    return _serialize_saved_plan(session, planned)


def get_saved_plan(session: Session, plan_date: date) -> dict | None:
    planned = _latest_planned_session(session, plan_date)
    if not planned:
        return None
    return _serialize_saved_plan(session, planned)


def delete_plan(session: Session, plan_date: date) -> None:
    planned = _latest_planned_session(session, plan_date)
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    day = session.get(ProgramDay, planned.program_day_id)
    if day:
        pdes = session.exec(
            select(ProgramDayExercise).where(
                ProgramDayExercise.program_day_id == day.id
            )
        ).all()
        for pde in pdes:
            session.delete(pde)
        session.delete(day)

    session.delete(planned)
    session.commit()


def add_exercises_to_plan(
    session: Session,
    plan_date: date,
    exercises: list[dict],
) -> dict:
    planned = _latest_planned_session(session, plan_date)
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    day = session.get(ProgramDay, planned.program_day_id)
    if not day:
        raise ValueError("Plan day not found")

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
                "selection_note": ex.get("selection_note"),
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
    planned = _latest_planned_session(session, plan_date)
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


def reorder_plan_exercises(
    session: Session,
    plan_date: date,
    pde_ids: list[int],
) -> dict:
    planned = _latest_planned_session(session, plan_date)
    if not planned:
        raise ValueError(f"No saved plan for {plan_date}")

    for i, pde_id in enumerate(pde_ids):
        pde = session.get(ProgramDayExercise, pde_id)
        if pde:
            pde.sort_order = i
            session.add(pde)

    session.commit()
    return _serialize_saved_plan(session, planned)


# ── Internal helpers ──────────────────────────────────────────────────


def _latest_planned_session(
    session: Session, plan_date: date
) -> PlannedSession | None:
    return session.exec(
        select(PlannedSession)
        .where(PlannedSession.date == plan_date)
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()


def _serialize_saved_plan(session: Session, planned: PlannedSession) -> dict:
    day = session.get(ProgramDay, planned.program_day_id)
    if not day:
        return {"id": planned.id, "error": "Program day not found"}

    day_exercises = list(session.exec(
        select(ProgramDayExercise)
        .where(ProgramDayExercise.program_day_id == day.id)
        .order_by(ProgramDayExercise.sort_order)
    ).all())

    logged_sets: dict[int, list[dict]] = defaultdict(list)
    scheme_history_by_exercise = get_exercise_scheme_history_map(
        session,
        [pde.exercise_id for pde in day_exercises],
        limit=40,
    )
    if planned.workout_session_id:
        # Self-heal: if the referenced WorkoutSession was deleted out from
        # under us (e.g., via DELETE /api/workout-sessions/{id} before this
        # router cascade-cleared the FK), clear the dangling pointer so the
        # frontend doesn't keep POSTing /sets at a 404. Otherwise the user is
        # stuck — they can't log sets, and Cancel also 404s on the missing
        # session.
        ws_exists = session.get(WorkoutSession, planned.workout_session_id)
        if ws_exists is None:
            planned.workout_session_id = None
            if planned.status == "in_progress":
                planned.status = "planned"
            session.add(planned)
            session.commit()
            session.refresh(planned)
    if planned.workout_session_id:
        sets = session.exec(
            select(WorkoutSet)
            .where(WorkoutSet.session_id == planned.workout_session_id)
            .order_by(WorkoutSet.set_order)
        ).all()
        ex_cache: dict[int, Exercise | None] = {}
        for s in sets:
            ex = ex_cache.get(s.exercise_id)
            if ex is None and s.exercise_id not in ex_cache:
                ex = session.get(Exercise, s.exercise_id)
                ex_cache[s.exercise_id] = ex
            logged_sets[s.exercise_id].append({
                "id": s.id,
                "set_order": s.set_order,
                "performed_side": s.performed_side,
                **legacy_metric_fields(ex, s.endurance_value),
                "weight": s.weight,
                "endurance_value": s.endurance_value,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
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

        heavy_avail = {"available": False, "reason": None}
        burnout_avail = {"available": False, "reason": None}
        if exercise:
            if exercise.allow_heavy_loading:
                heavy_avail = check_heavy_availability(
                    pde.exercise_id, session, planned.workout_session_id,
                )
            burnout_avail = check_burnout_availability(pde.exercise_id, session)

        completed_sets = logged_sets.get(pde.exercise_id, [])
        exercises.append({
            "pde_id": pde.id,
            "exercise_id": pde.exercise_id,
            "exercise_name": exercise.name if exercise else "Unknown",
            "equipment": exercise.equipment if exercise else None,
            "allow_heavy_loading": exercise.allow_heavy_loading if exercise else True,
            "heavy_available": heavy_avail["available"],
            "heavy_blocked_reason": heavy_avail["reason"],
            "burnout_available": burnout_avail["available"],
            "burnout_blocked_reason": burnout_avail["reason"],
            "load_input_mode": (
                exercise.load_input_mode if exercise else "external_weight"
            ),
            "set_metric_mode": (
                exercise.set_metric_mode if exercise else "reps"
            ) or "reps",
            "laterality": exercise.laterality if exercise else "bilateral",
            "target_sets": pde.target_sets,
            "target_rep_min": pde.target_rep_min,
            "target_rep_max": pde.target_rep_max,
            "rep_scheme": meta.get("rep_scheme"),
            "target_weight": meta.get("target_weight"),
            "performed_side": meta.get("performed_side"),
            "side_explanation": meta.get("side_explanation"),
            "selection_note": meta.get("selection_note"),
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
