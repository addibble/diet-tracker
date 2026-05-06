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
