"""Tests for the workout_sets router (individual set CRUD + PDE editing)."""
import json

import pytest
from sqlmodel import Session, select

from app.models import (
    Exercise,
    ProgramDay,
    ProgramDayExercise,
    RehabPlan,
    Tissue,
    TrackedTissue,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
    WorkoutSetTissueFeedback,
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


def test_update_set_persists_tissue_feedback(client, session: Session, workout_set: WorkoutSet):
    tissue = Tissue(
        name="biceps_long_head",
        display_name="Biceps Long Head",
        type="muscle",
        recovery_hours=48.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(
        tissue_id=tissue.id,
        side="left",
        display_name="Left Biceps Long Head",
        active=True,
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    first = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={
            "tissue_feedback": [
                {
                    "tracked_tissue_id": tracked.id,
                    "pain_0_10": 5,
                    "symptom_note": "moderate pulling",
                }
            ]
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["tissue_feedback"]) == 1
    assert first_payload["tissue_feedback"][0]["tracked_tissue_id"] == tracked.id
    assert first_payload["tissue_feedback"][0]["pain_0_10"] == 5
    assert first_payload["tissue_feedback"][0]["symptom_note"] == "moderate pulling"

    second = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={
            "tissue_feedback": [
                {
                    "tracked_tissue_id": tracked.id,
                    "pain_0_10": 2,
                    "symptom_note": "mild after reset",
                }
            ]
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["tissue_feedback"]) == 1
    assert second_payload["tissue_feedback"][0]["pain_0_10"] == 2
    assert second_payload["tissue_feedback"][0]["symptom_note"] == "mild after reset"

    rows = session.exec(
        select(WorkoutSetTissueFeedback).where(
            WorkoutSetTissueFeedback.workout_set_id == workout_set.id
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].tracked_tissue_id == tracked.id
    assert rows[0].pain_0_10 == 2
    assert rows[0].symptom_note == "mild after reset"


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


def test_add_set_response_includes_timestamps_and_tissue_feedback(client, session: Session, workout_session: WorkoutSession):
    tissue = Tissue(name="biceps_long_head", display_name="Biceps Long Head", type="muscle", recovery_hours=48.0)
    exercise = Exercise(
        name="Single-Arm Cable Curl",
        load_input_mode="external_weight",
        laterality="unilateral",
    )
    session.add(tissue)
    session.add(exercise)
    session.commit()
    session.refresh(tissue)
    session.refresh(exercise)

    tracked = TrackedTissue(
        tissue_id=tissue.id,
        side="left",
        display_name="Left Biceps Long Head",
        active=True,
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    session.add(
        RehabPlan(
            tracked_tissue_id=tracked.id,
            protocol_id="cervical-radiculopathy-deltoid",
            stage_id="strength-rebuild",
            pain_monitoring_threshold=2,
        )
    )
    session.commit()

    resp = client.post(
        f"/api/workout-sessions/{workout_session.id}/sets",
        json={
            "exercise_id": exercise.id,
            "reps": 10,
            "performed_side": "left",
            "tissue_feedback": [
                {
                    "tracked_tissue_id": tracked.id,
                    "pain_0_10": 4,
                    "symptom_note": "pulling at distal tendon",
                }
            ],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["started_at"] is None
    assert data["completed_at"] is None
    assert len(data["tissue_feedback"]) == 1
    assert data["tissue_feedback"][0]["tracked_tissue_id"] == tracked.id
    assert data["tissue_feedback"][0]["pain_0_10"] == 4
    assert data["tissue_feedback"][0]["above_threshold"] is True


def test_update_set_sets_completed_at_and_reorders_session(client, session: Session, exercise: Exercise, workout_session: WorkoutSession):
    later = WorkoutSet(
        session_id=workout_session.id,
        exercise_id=exercise.id,
        set_order=2,
        reps=None,
    )
    earlier = WorkoutSet(
        session_id=workout_session.id,
        exercise_id=exercise.id,
        set_order=1,
        reps=None,
    )
    session.add(later)
    session.add(earlier)
    session.commit()
    session.refresh(later)
    session.refresh(earlier)

    first_done = client.patch(
        f"/api/workout-sets/{later.id}",
        json={"reps": 12, "completed_at": "2026-03-15T10:05:00Z"},
    )
    assert first_done.status_code == 200
    assert first_done.json()["completed_at"].startswith("2026-03-15T10:05:00")

    second_done = client.patch(
        f"/api/workout-sets/{earlier.id}",
        json={"reps": 10, "completed_at": "2026-03-15T10:10:00Z"},
    )
    assert second_done.status_code == 200

    session_response = client.get(f"/api/workout-sessions/{workout_session.id}")
    assert session_response.status_code == 200
    ordered_ids = [row["id"] for row in session_response.json()["sets"]]
    assert ordered_ids == [later.id, earlier.id]
    assert [row["set_order"] for row in session_response.json()["sets"]] == [1, 2]


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
