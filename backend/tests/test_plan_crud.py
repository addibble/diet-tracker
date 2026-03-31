"""Tests for planner CRUD endpoints: delete, add/remove exercises, reorder, pre-fill sets."""
import datetime

import pytest
from sqlmodel import Session

from app.models import (
    Exercise,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WorkoutSet,
)


@pytest.fixture()
def exercises(session: Session) -> list[Exercise]:
    exs = []
    for name in ["Bench Press", "Squat", "Deadlift", "Overhead Press"]:
        e = Exercise(name=name, equipment="barbell", load_input_mode="external_weight")
        session.add(e)
        exs.append(e)
    session.commit()
    for e in exs:
        session.refresh(e)
    return exs


@pytest.fixture()
def saved_plan(session: Session, exercises: list[Exercise]):
    """Create a saved plan with 3 exercises for today."""
    import json

    prog = TrainingProgram(name="Test Program")
    session.add(prog)
    session.commit()
    session.refresh(prog)

    day = ProgramDay(
        program_id=prog.id,
        day_label="A",
        target_regions='["chest", "shoulders"]',
    )
    session.add(day)
    session.commit()
    session.refresh(day)

    for i, ex in enumerate(exercises[:3]):
        pde = ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=ex.id,
            target_sets=3,
            target_rep_min=8,
            target_rep_max=12,
            sort_order=i,
            notes=json.dumps({"target_weight": 135 + i * 20, "rep_scheme": "volume"}),
        )
        session.add(pde)

    planned = PlannedSession(
        program_day_id=day.id,
        date=datetime.date.today(),
        status="planned",
    )
    session.add(planned)
    session.commit()
    session.refresh(planned)
    return planned


# ── DELETE /api/planner/active ────────────────────────────────────────


def test_delete_plan(client, saved_plan):
    # Verify plan exists
    resp = client.get("/api/planner/active")
    assert resp.status_code == 200

    # Delete it
    resp = client.delete("/api/planner/active")
    assert resp.status_code == 204

    # Verify it's gone
    resp = client.get("/api/planner/active")
    assert resp.status_code == 404


def test_delete_plan_not_found(client):
    resp = client.delete("/api/planner/active")
    assert resp.status_code == 404


# ── POST /api/planner/active/exercises ────────────────────────────────


def test_add_exercise_to_plan(client, saved_plan, exercises):
    # Plan has 3 exercises; add the 4th
    resp = client.post("/api/planner/active/exercises", json={
        "exercises": [{
            "exercise_id": exercises[3].id,
            "target_sets": 4,
            "target_reps": "5-8",
            "target_weight": 225,
            "rep_scheme": "heavy",
        }],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["exercises"]) == 4
    added = data["exercises"][-1]
    assert added["exercise_id"] == exercises[3].id
    assert added["target_sets"] == 4


# ── DELETE /api/planner/active/exercises/{id} ─────────────────────────


def test_remove_exercise_from_plan(client, saved_plan, exercises):
    resp = client.get("/api/planner/active")
    assert len(resp.json()["exercises"]) == 3

    resp = client.delete(f"/api/planner/active/exercises/{exercises[0].id}")
    assert resp.status_code == 200
    assert len(resp.json()["exercises"]) == 2


# ── PATCH /api/planner/active/reorder ─────────────────────────────────


def test_reorder_exercises(client, saved_plan, exercises):
    resp = client.get("/api/planner/active")
    pde_ids = [e["pde_id"] for e in resp.json()["exercises"]]
    assert len(pde_ids) == 3

    # Reverse the order
    reversed_ids = list(reversed(pde_ids))
    resp = client.patch("/api/planner/active/reorder", json={
        "pde_ids": reversed_ids,
    })
    assert resp.status_code == 200
    new_order = [e["pde_id"] for e in resp.json()["exercises"]]
    assert new_order == reversed_ids


# ── Pre-fill sets on start ────────────────────────────────────────────


def test_start_prefills_sets(client, saved_plan, session):
    resp = client.post("/api/planner/start")
    assert resp.status_code == 200
    ws_id = resp.json()["workout_session_id"]

    # Check that sets were pre-created
    sets = session.exec(
        WorkoutSet.__table__.select().where(WorkoutSet.session_id == ws_id)
    ).all()
    # 3 exercises × 3 sets each = 9 sets
    assert len(sets) == 9

    # Check via the plan response
    resp = client.get("/api/planner/active")
    plan = resp.json()
    for ex in plan["exercises"]:
        assert len(ex["completed_sets"]) == ex["target_sets"]
        if ex["target_weight"]:
            for s in ex["completed_sets"]:
                assert s["weight"] == ex["target_weight"]


def test_start_prefills_sets_with_reps(client, saved_plan, session):
    resp = client.post("/api/planner/start")
    assert resp.json()["workout_session_id"]

    # Check the active plan for pre-filled reps
    resp = client.get("/api/planner/active")
    plan = resp.json()
    for ex in plan["exercises"]:
        for s in ex["completed_sets"]:
            # Should be target_rep_max (12)
            assert s["reps"] == 12


def test_start_prefills_planned_performed_side(client, session):
    import json

    prog = TrainingProgram(name="Side Plan Program")
    session.add(prog)
    session.commit()
    session.refresh(prog)

    exercise = Exercise(
        name="Single Arm Cable Press",
        equipment="cable",
        load_input_mode="external_weight",
        laterality="unilateral",
    )
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    day = ProgramDay(
        program_id=prog.id,
        day_label="Left Rehab",
        target_regions='["shoulders"]',
    )
    session.add(day)
    session.commit()
    session.refresh(day)

    pde = ProgramDayExercise(
        program_day_id=day.id,
        exercise_id=exercise.id,
        target_sets=2,
        target_rep_min=10,
        target_rep_max=12,
        sort_order=0,
        notes=json.dumps({"target_weight": 25, "rep_scheme": "volume", "performed_side": "left"}),
    )
    session.add(pde)

    planned = PlannedSession(
        program_day_id=day.id,
        date=datetime.date.today(),
        status="planned",
    )
    session.add(planned)
    session.commit()

    resp = client.post("/api/planner/start")
    assert resp.status_code == 200

    active = client.get("/api/planner/active")
    assert active.status_code == 200
    plan = active.json()
    assert plan["exercises"][0]["performed_side"] == "left"
    assert all(s["performed_side"] == "left" for s in plan["exercises"][0]["completed_sets"])
