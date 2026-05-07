"""Tests for the readiness trend endpoint."""
import datetime

import pytest
from sqlmodel import Session

from app.models import WorkoutSession


@pytest.fixture()
def trend_sessions(session: Session):
    today = datetime.date.today()
    sessions = [
        WorkoutSession(date=today - datetime.timedelta(days=10), readiness_beta=-0.3),
        WorkoutSession(date=today - datetime.timedelta(days=5), readiness_beta=0.0),
        WorkoutSession(date=today - datetime.timedelta(days=2), readiness_beta=0.15),
        WorkoutSession(date=today, readiness_beta=None),  # session without β yet
    ]
    for ws in sessions:
        session.add(ws)
    session.commit()
    return sessions


def test_readiness_trend_returns_recent_sessions(client, trend_sessions):
    resp = client.get("/api/workout-sessions/readiness/trend?days=14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 14
    assert len(data["points"]) == 4
    # Sorted ascending by date
    dates = [p["date"] for p in data["points"]]
    assert dates == sorted(dates)
    # Labels populated for non-null β
    fatigued = next(p for p in data["points"] if p["readiness_beta"] == -0.3)
    assert fatigued["readiness_label"] == "fatigued"
    null_pt = next(p for p in data["points"] if p["readiness_beta"] is None)
    assert null_pt["readiness_label"] is None


def test_readiness_trend_respects_days_parameter(client, trend_sessions):
    resp = client.get("/api/workout-sessions/readiness/trend?days=3")
    assert resp.status_code == 200
    data = resp.json()
    # Only sessions in last 3 days (today, day-2)
    assert len(data["points"]) == 2


def test_readiness_trend_clamps_days(client):
    resp = client.get("/api/workout-sessions/readiness/trend?days=999")
    assert resp.status_code == 422  # ge=1, le=90 enforced


def test_readiness_trend_filters_by_exercise(client, session: Session):
    """When exercise_id is supplied, the trend should drop sessions that
    didn't include that exercise and recompute β from just that exercise's
    sets."""
    from app.models import Exercise, WorkoutSession, WorkoutSet

    today = datetime.date.today()
    ex_a = Exercise(name="Bench", load_input_mode="external_weight")
    ex_b = Exercise(name="Squat", load_input_mode="external_weight")
    session.add_all([ex_a, ex_b])
    session.commit()
    session.refresh(ex_a)
    session.refresh(ex_b)

    # Session 1: only ex_a — should appear in ex_a's trend, not ex_b's
    s1 = WorkoutSession(date=today - datetime.timedelta(days=3), readiness_beta=0.10)
    # Session 2: only ex_b — should appear in ex_b's trend, not ex_a's
    s2 = WorkoutSession(date=today - datetime.timedelta(days=1), readiness_beta=-0.05)
    session.add_all([s1, s2])
    session.commit()
    session.refresh(s1)
    session.refresh(s2)
    session.add(WorkoutSet(
        session_id=s1.id, exercise_id=ex_a.id, set_order=1,
        endurance_value=10, weight=135.0, rpe=8.0,
    ))
    session.add(WorkoutSet(
        session_id=s2.id, exercise_id=ex_b.id, set_order=1,
        endurance_value=8, weight=225.0, rpe=8.5,
    ))
    session.commit()

    resp = client.get(
        f"/api/workout-sessions/readiness/trend?days=14&exercise_id={ex_a.id}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["exercise_id"] == ex_a.id
    point_session_ids = [p["session_id"] for p in data["points"]]
    assert s1.id in point_session_ids
    assert s2.id not in point_session_ids


def test_readiness_trend_per_exercise_recomputes_beta(client, session: Session):
    """The per-exercise β should be derived from per-exercise sets, not
    just copied from WorkoutSession.readiness_beta."""
    from app.models import Exercise, WorkoutSession, WorkoutSet

    today = datetime.date.today()
    ex = Exercise(name="OHP", load_input_mode="external_weight")
    session.add(ex)
    session.commit()
    session.refresh(ex)

    # Session has stored session-wide β = +0.5; per-exercise β should be
    # recomputed from the actual sets and may differ. Without enough sets
    # for that exercise, per-exercise β is None.
    ws = WorkoutSession(
        date=today - datetime.timedelta(days=1), readiness_beta=0.5,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    # Single eligible set: below MIN_SETS_FOR_BETA → β should be None
    session.add(WorkoutSet(
        session_id=ws.id, exercise_id=ex.id, set_order=1,
        endurance_value=10, weight=95.0, rpe=8.0,
    ))
    session.commit()

    resp = client.get(
        f"/api/workout-sessions/readiness/trend?days=14&exercise_id={ex.id}"
    )
    assert resp.status_code == 200
    data = resp.json()
    pt = next(p for p in data["points"] if p["session_id"] == ws.id)
    # Stored session-wide β was 0.5, but per-exercise β with <2 sets is None
    assert pt["readiness_beta"] is None
