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
        endurance_value=10,
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


def test_notes_only_update_skips_readiness_refit(
    client, workout_set, monkeypatch
):
    """Editing only `notes` must NOT trigger update_session_readiness — that
    refit is expensive (~0.5–1s) and only β-relevant fields warrant it."""
    from app.routers import workout_sets as ws_router
    calls: list[int] = []

    def _spy(_session, session_id):
        calls.append(session_id)

    monkeypatch.setattr(ws_router, "update_session_readiness", _spy)
    resp = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={"notes": "edit", "performed_side": "left"},
    )
    assert resp.status_code == 200
    assert calls == []


def test_rpe_update_triggers_readiness_refit(
    client, workout_set, monkeypatch
):
    from app.routers import workout_sets as ws_router
    calls: list[int] = []

    def _spy(_session, session_id):
        calls.append(session_id)

    monkeypatch.setattr(ws_router, "update_session_readiness", _spy)
    resp = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={"rpe": 8.5},
    )
    assert resp.status_code == 200
    assert calls == [workout_set.session_id]


def test_update_set_persists_tissue_feedback_removed(client, session: Session, workout_set: WorkoutSet):
    """Placeholder — tissue_feedback subsystem removed; endpoint silently ignores the field."""
    resp = client.patch(
        f"/api/workout-sets/{workout_set.id}",
        json={"reps": 12},
    )
    assert resp.status_code == 200
    assert resp.json()["reps"] == 12
    assert "tissue_feedback" not in resp.json()


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
    # New set gets auto-timestamp (completed_at) since reps are provided,
    # which sorts it before the fixture set (no completed_at) after normalization.
    assert resp.json()["set_order"] == 1


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


def test_add_set_response_includes_timestamps(client, session: Session, workout_session: WorkoutSession):
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
    data = resp.json()
    # Auto-timestamped because reps are provided
    assert data["started_at"] is not None
    assert data["completed_at"] is not None


def test_update_set_sets_completed_at_and_reorders_session(client, session: Session, exercise: Exercise, workout_session: WorkoutSession):
    later = WorkoutSet(
        session_id=workout_session.id,
        exercise_id=exercise.id,
        set_order=2,
        endurance_value=None,
    )
    earlier = WorkoutSet(
        session_id=workout_session.id,
        exercise_id=exercise.id,
        set_order=1,
        endurance_value=None,
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


# ── DELETE /api/workout-sessions/{id} cascade-clears PlannedSession FK ────


def test_delete_workout_session_clears_planned_session_pointer(
    client, session: Session, exercise: Exercise, workout_session: WorkoutSession
):
    """Deleting a WorkoutSession must clear any PlannedSession.workout_session_id
    that points at it AND demote 'in_progress' status back to 'planned'.
    Otherwise the planner serves a dangling pointer and every subsequent
    POST /api/workout-sessions/{stale_id}/sets returns 404, leaving the
    user stuck (Cancel also 404s on the missing session).
    """
    from app.models import PlannedSession, ProgramDay, TrainingProgram

    prog = TrainingProgram(name="P")
    session.add(prog)
    session.flush()
    day = ProgramDay(program_id=prog.id, day_label="A")
    session.add(day)
    session.flush()
    planned = PlannedSession(
        program_day_id=day.id,
        date=workout_session.date,
        status="in_progress",
        workout_session_id=workout_session.id,
    )
    session.add(planned)
    session.commit()
    planned_id = planned.id

    resp = client.delete(f"/api/workout-sessions/{workout_session.id}")
    assert resp.status_code == 204

    session.expire_all()
    refreshed = session.get(PlannedSession, planned_id)
    assert refreshed is not None
    assert refreshed.workout_session_id is None
    assert refreshed.status == "planned"


def test_get_active_self_heals_dangling_workout_session_pointer(
    client, session: Session, exercise: Exercise
):
    """If a PlannedSession.workout_session_id points to a missing WorkoutSession
    (e.g., DB hand-edit, or pre-cascade delete from old code), GET
    /api/planner/active must self-heal by clearing the pointer and demoting
    'in_progress' → 'planned'. Otherwise the user is stuck because the
    frontend POSTs /sets at the dead session_id forever (404).
    """
    import datetime as dt

    from app.models import PlannedSession, ProgramDay, TrainingProgram

    plan_date = dt.date(2026, 3, 15)
    prog = TrainingProgram(name="P")
    session.add(prog)
    session.flush()
    day = ProgramDay(program_id=prog.id, day_label="Quick Start")
    session.add(day)
    session.flush()
    planned = PlannedSession(
        program_day_id=day.id,
        date=plan_date,
        status="in_progress",
        workout_session_id=999_999,  # never existed
    )
    session.add(planned)
    session.commit()
    planned_id = planned.id

    resp = client.get(f"/api/planner/active?as_of={plan_date.isoformat()}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workout_session_id"] is None
    assert body["status"] == "planned"

    session.expire_all()
    refreshed = session.get(PlannedSession, planned_id)
    assert refreshed.workout_session_id is None
    assert refreshed.status == "planned"


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
