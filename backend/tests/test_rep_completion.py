"""Tests for auto-computed rep_completion on workout sets."""

import datetime as dt

import pytest
from sqlmodel import select

from app.llm_tools.workout import handle_set_workout_sessions
from app.models import (
    Exercise,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)


@pytest.fixture()
def program_with_exercise(session):
    """Create an active program with one exercise (8-12 rep target)."""
    ex = Exercise(name="Bench Press", equipment="barbell")
    session.add(ex)
    session.flush()

    prog = TrainingProgram(name="Test Program", active=1)
    session.add(prog)
    session.flush()

    day = ProgramDay(program_id=prog.id, day_label="A")
    session.add(day)
    session.flush()

    pde = ProgramDayExercise(
        program_day_id=day.id,
        exercise_id=ex.id,
        target_sets=3,
        target_rep_min=8,
        target_rep_max=12,
    )
    session.add(pde)
    session.commit()

    for obj in [ex, prog, day, pde]:
        session.refresh(obj)
    return ex, prog, day, pde


def _log_sets(session, exercise_name, reps_list, ws_id=None):
    """Log sets via set_workout_sessions and return the result."""
    records = [
        {"exercise_name": exercise_name, "reps": r, "weight": 135}
        for r in reps_list
    ]
    changes = [
        {
            "operation": "update" if ws_id else "create",
            "set": {"date": "2026-03-15"},
            "relations": {"sets": {"mode": "append", "records": records}},
        }
    ]
    if ws_id:
        changes[0]["match"] = {"id": {"eq": ws_id}}
    return handle_set_workout_sessions({"changes": changes}, session)


def test_auto_rep_completion_full(program_with_exercise, session):
    """Reps >= target_rep_max → full."""
    ex, prog, day, _ = program_with_exercise
    # Create a workout session linked via PlannedSession
    ws = WorkoutSession(date=dt.date(2026, 3, 15))
    session.add(ws)
    session.flush()
    ps = PlannedSession(
        program_day_id=day.id, date=dt.date(2026, 3, 15),
        workout_session_id=ws.id,
    )
    session.add(ps)
    session.commit()

    _log_sets(session, "Bench Press", [12, 12, 12], ws_id=ws.id)

    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.session_id == ws.id)
    ).all()
    assert all(s.rep_completion == "full" for s in sets)


def test_auto_rep_completion_partial(program_with_exercise, session):
    """target_rep_min <= reps < target_rep_max → partial."""
    ex, prog, day, _ = program_with_exercise
    ws = WorkoutSession(date=dt.date(2026, 3, 15))
    session.add(ws)
    session.flush()
    ps = PlannedSession(
        program_day_id=day.id, date=dt.date(2026, 3, 15),
        workout_session_id=ws.id,
    )
    session.add(ps)
    session.commit()

    _log_sets(session, "Bench Press", [10, 9, 8], ws_id=ws.id)

    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == ws.id)
        .order_by(WorkoutSet.set_order)
    ).all()
    assert all(s.rep_completion == "partial" for s in sets)


def test_auto_rep_completion_failed(program_with_exercise, session):
    """Reps < target_rep_min → failed."""
    ex, prog, day, _ = program_with_exercise
    ws = WorkoutSession(date=dt.date(2026, 3, 15))
    session.add(ws)
    session.flush()
    ps = PlannedSession(
        program_day_id=day.id, date=dt.date(2026, 3, 15),
        workout_session_id=ws.id,
    )
    session.add(ps)
    session.commit()

    _log_sets(session, "Bench Press", [7, 6, 5], ws_id=ws.id)

    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.session_id == ws.id)
    ).all()
    assert all(s.rep_completion == "failed" for s in sets)


def test_auto_rep_completion_mixed(program_with_exercise, session):
    """Different reps produce different completion statuses."""
    ex, prog, day, _ = program_with_exercise
    ws = WorkoutSession(date=dt.date(2026, 3, 15))
    session.add(ws)
    session.flush()
    ps = PlannedSession(
        program_day_id=day.id, date=dt.date(2026, 3, 15),
        workout_session_id=ws.id,
    )
    session.add(ps)
    session.commit()

    _log_sets(session, "Bench Press", [12, 10, 6], ws_id=ws.id)

    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == ws.id)
        .order_by(WorkoutSet.set_order)
    ).all()
    assert sets[0].rep_completion == "full"
    assert sets[1].rep_completion == "partial"
    assert sets[2].rep_completion == "failed"


def test_explicit_rep_completion_not_overridden(
    program_with_exercise, session
):
    """Explicitly provided rep_completion is preserved."""
    ex, prog, day, _ = program_with_exercise
    ws = WorkoutSession(date=dt.date(2026, 3, 15))
    session.add(ws)
    session.flush()
    ps = PlannedSession(
        program_day_id=day.id, date=dt.date(2026, 3, 15),
        workout_session_id=ws.id,
    )
    session.add(ps)
    session.commit()

    handle_set_workout_sessions(
        {
            "changes": [
                {
                    "operation": "update",
                    "match": {"id": {"eq": ws.id}},
                    "set": {"date": "2026-03-15"},
                    "relations": {
                        "sets": {
                            "mode": "append",
                            "records": [
                                {
                                    "exercise_name": "Bench Press",
                                    "reps": 12,
                                    "weight": 135,
                                    "rep_completion": "partial",
                                }
                            ],
                        }
                    },
                }
            ]
        },
        session,
    )

    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.session_id == ws.id)
    ).all()
    assert sets[0].rep_completion == "partial"  # not "full"


def test_no_program_link_leaves_rep_completion_null(session):
    """Without a PlannedSession link, rep_completion stays None."""
    ex = Exercise(name="Random Exercise")
    session.add(ex)
    session.commit()

    result = handle_set_workout_sessions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {"date": "2026-03-15"},
                    "relations": {
                        "sets": {
                            "mode": "replace",
                            "records": [
                                {
                                    "exercise_name": "Random Exercise",
                                    "reps": 10,
                                    "weight": 100,
                                }
                            ],
                        }
                    },
                }
            ]
        },
        session,
    )

    ws_id = result["matches"][0]["id"]
    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.session_id == ws_id)
    ).all()
    assert sets[0].rep_completion is None
