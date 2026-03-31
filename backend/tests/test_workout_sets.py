"""Tests for the workout_sets router (individual set CRUD + PDE editing)."""
import json

import pytest
from sqlmodel import Session

from app.models import (
    Exercise,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)


@pytest.fixture()
def exercise(session: Session) -> Exercise:
    ex = Exercise(name="Bench Press", load_input_mode="external_weight")
    session.add(ex)
    session.commit()
    session.refresh(ex)
    return ex


@pytest.fixture()
def workout_session(session: Session) -> WorkoutSession:
    import datetime

    ws = WorkoutSession(date=datetime.date(2026, 3, 15))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture()
def workout_set(
    session: Session, exercise: Exercise, workout_session: WorkoutSession
) -> WorkoutSet:
    s = WorkoutSet(
        session_id=workout_session.id,
        exercise_id=exercise.id,
        set_order=1,
        reps=10,
        weight=135.0,
        rpe=7.0,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


# ── PATCH /api/workout-sets/{set_id} ──────────────────────────────────


def test_update_set(client, workout_set):
    resp = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={"reps": 12, "rpe": 8.5, "performed_side": "bilateral"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reps"] == 12
    assert data["rpe"] == 8.5
    assert data["weight"] == 135.0  # unchanged
    assert data["performed_side"] == "bilateral"


def test_update_set_not_found(client):
    resp = client.patch("/api/workout-sets/99999", json={"reps": 5})
    assert resp.status_code == 404


def test_update_set_partial(client, workout_set):
    """Only the fields sent should be updated."""
    resp = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={"notes": "felt good"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == "felt good"
    assert data["reps"] == 10  # unchanged
    assert data["weight"] == 135.0  # unchanged


# ── POST /api/workout-sessions/{session_id}/sets ──────────────────────


def test_add_set(client, exercise, workout_session):
    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={
            "exercise_id": exercise.id,
            "reps": 8,
            "weight": 155.0,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["exercise_id"] == exercise.id
    assert data["reps"] == 8
    assert data["weight"] == 155.0
    assert data["set_order"] == 1  # auto-assigned
    assert data["performed_side"] == "bilateral"


def test_add_set_auto_order(client, exercise, workout_session, workout_set):
    """set_order should be auto-incremented past existing sets."""
    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={"exercise_id": exercise.id, "reps": 6},
    )
    assert resp.status_code == 201
    assert resp.json()["set_order"] == 2  # workout_set has order=1


def test_add_set_session_not_found(client, exercise):
    resp = client.post(
        "/api/workout-sessions/99999/sets",
        json={"exercise_id": exercise.id, "reps": 5},
    )
    assert resp.status_code == 404


def test_add_set_exercise_not_found(client, workout_session):
    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={"exercise_id": 99999, "reps": 5},
    )
    assert resp.status_code == 400


def test_add_set_keeps_explicit_unilateral_side(client, session: Session, workout_session: WorkoutSession):
    exercise = Exercise(
        name="Single-Arm Cable Curl",
        load_input_mode="external_weight",
        laterality="unilateral",
    )
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={
            "exercise_id": exercise.id,
            "reps": 10,
            "performed_side": "left",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["performed_side"] == "left"


def test_add_set_infers_side_from_unilateral_exercise_name(client, session: Session, workout_session: WorkoutSession):
    exercise = Exercise(
        name="Left Only Lateral Raise",
        load_input_mode="external_weight",
        laterality="unilateral",
    )
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={"exercise_id": exercise.id, "reps": 12},
    )
    assert resp.status_code == 201
    assert resp.json()["performed_side"] == "left"


# ── DELETE /api/workout-sets/{set_id} ─────────────────────────────────


def test_delete_set(client, workout_set):
    resp = client.delete(f"/api/workout-sets/{workout_set.id}")
    assert resp.status_code == 204
    # Verify actually deleted
    resp2 = client.patch(
        f"/api/workout-sets/{workout_set.id}", json={"reps": 1}
    )
    assert resp2.status_code == 404


def test_delete_set_not_found(client):
    resp = client.delete("/api/workout-sets/99999")
    assert resp.status_code == 404


# ── PATCH /api/program-day-exercises/{pde_id} ─────────────────────────


@pytest.fixture()
def program_day_exercise(session: Session, exercise: Exercise):
    prog = TrainingProgram(name="Test Program")
    session.add(prog)
    session.flush()
    day = ProgramDay(program_id=prog.id, day_label="A")
    session.add(day)
    session.flush()
    pde = ProgramDayExercise(
        program_day_id=day.id,
        exercise_id=exercise.id,
        target_sets=3,
        target_rep_min=8,
        target_rep_max=12,
        notes=json.dumps({"rep_scheme": "3x8-12", "target_weight": 135}),
    )
    session.add(pde)
    session.commit()
    session.refresh(pde)
    return pde


def test_update_pde_targets(client, program_day_exercise):
    resp = client.patch(
        f"/api/program-day-exercises/{program_day_exercise.id}",
        json={"target_sets": 4, "target_weight": 155},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_sets"] == 4
    assert data["target_weight"] == 155
    assert data["target_rep_min"] == 8  # unchanged
    assert data["rep_scheme"] == "3x8-12"  # unchanged


def test_update_pde_rep_scheme(client, program_day_exercise):
    resp = client.patch(
        f"/api/program-day-exercises/{program_day_exercise.id}",
        json={"rep_scheme": "5x5", "target_rep_min": 5, "target_rep_max": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rep_scheme"] == "5x5"
    assert data["target_rep_min"] == 5
    assert data["target_rep_max"] == 5
    assert data["target_weight"] == 135  # unchanged


def test_update_pde_performed_side_metadata(client, program_day_exercise):
    response = client.patch(
        f"/api/program-day-exercises/{program_day_exercise.id}",
        json={"performed_side": "left", "side_explanation": "Left-side rehab focus"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["performed_side"] == "left"
    assert data["side_explanation"] == "Left-side rehab focus"


def test_update_pde_not_found(client):
    resp = client.patch(
        "/api/program-day-exercises/99999",
        json={"target_sets": 5},
    )
    assert resp.status_code == 404
