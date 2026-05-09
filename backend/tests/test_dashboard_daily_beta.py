"""Tests for GET /api/dashboard/daily-beta — date-aligned daily-mean β strip."""
from __future__ import annotations

from datetime import date, timedelta

from app.models import Exercise, WorkoutSession, WorkoutSet


def _make_exercise(session, name: str = "Bench Press") -> Exercise:
    ex = Exercise(
        name=name,
        equipment="barbell",
        allow_heavy_loading=True,
        load_input_mode="external_weight",
        bodyweight_fraction=0.0,
        external_load_multiplier=1.0,
        set_metric_mode="reps",
    )
    session.add(ex)
    session.commit()
    session.refresh(ex)
    return ex


def _make_session(session, on_date: date) -> WorkoutSession:
    ws = WorkoutSession(date=on_date)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _add_set(
    session, ws_id: int, ex_id: int, *, weight: float, reps: int,
    rpe: float | None, set_order: int,
) -> WorkoutSet:
    s = WorkoutSet(
        session_id=ws_id, exercise_id=ex_id, set_order=set_order,
        weight=weight, endurance_value=float(reps), rpe=rpe,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _seed_history(
    session, ex: Exercise, *, baseline_date: date,
    weights: list[float], reps_list: list[float], rpes: list[float],
) -> None:
    for i, (w, r, rpe) in enumerate(zip(weights, reps_list, rpes)):
        d = baseline_date - timedelta(days=2 + i)
        ws = _make_session(session, d)
        _add_set(session, ws.id, ex.id, weight=w, reps=int(r), rpe=rpe,
                 set_order=1)


class TestDailyBetaEndpoint:
    def test_empty_window(self, client):
        resp = client.get("/api/dashboard/daily-beta?days=5&end_date=2026-03-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["days"] == 5
        assert data["start"] == "2026-03-03"
        assert data["end"] == "2026-03-07"
        assert data["dates"] == [
            "2026-03-03", "2026-03-04", "2026-03-05",
            "2026-03-06", "2026-03-07",
        ]
        assert len(data["points"]) == 5
        for p in data["points"]:
            assert p["worked_out"] is False
            assert p["beta"] is None
            assert p["session_count"] == 0
            assert p["exercise_count"] == 0
            assert p["set_count"] == 0

    def test_window_aligned_with_rest_days(self, client, session):
        bench = _make_exercise(session, name="Bench Press")
        end = date(2026, 3, 10)
        # Seed history with baseline far enough back that no seeded
        # session lands inside the daily-beta window we're testing.
        _seed_history(
            session, bench, baseline_date=end - timedelta(days=15),
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # One workout in the middle of the window (3 rest days surround it).
        ws = _make_session(session, date(2026, 3, 8))
        _add_set(session, ws.id, bench.id,
                 weight=180, reps=10, rpe=8.0, set_order=1)
        _add_set(session, ws.id, bench.id,
                 weight=190, reps=9, rpe=8.5, set_order=2)

        resp = client.get(
            f"/api/dashboard/daily-beta?days=5&end_date={end.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        # Full 5-date window even though only one day has a workout.
        assert data["dates"] == [
            "2026-03-06", "2026-03-07", "2026-03-08",
            "2026-03-09", "2026-03-10",
        ]
        assert len(data["points"]) == 5
        by_date = {p["date"]: p for p in data["points"]}
        wo = by_date["2026-03-08"]
        assert wo["worked_out"] is True
        assert wo["beta"] is not None
        assert wo["beta"] > 0.0  # bench was strong
        assert wo["session_count"] == 1
        assert wo["exercise_count"] == 1
        assert wo["set_count"] == 2
        for d in ("2026-03-06", "2026-03-07", "2026-03-09", "2026-03-10"):
            assert by_date[d]["worked_out"] is False
            assert by_date[d]["beta"] is None

    def test_multi_exercise_day_mean(self, client, session):
        bench = _make_exercise(session, name="Bench Press")
        squat = _make_exercise(session, name="Back Squat")
        end = date(2026, 3, 10)
        # Push seeded history outside the test window.
        _seed_history(
            session, bench, baseline_date=end - timedelta(days=15),
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        _seed_history(
            session, squat, baseline_date=end - timedelta(days=15),
            weights=[200, 220, 240, 260, 280, 300],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Strong on bench, baseline on squat.
        ws = _make_session(session, date(2026, 3, 9))
        _add_set(session, ws.id, bench.id,
                 weight=180, reps=10, rpe=8.0, set_order=1)
        _add_set(session, ws.id, bench.id,
                 weight=190, reps=9, rpe=8.5, set_order=2)
        _add_set(session, ws.id, squat.id,
                 weight=240, reps=8, rpe=8.5, set_order=3)
        _add_set(session, ws.id, squat.id,
                 weight=240, reps=7, rpe=9.0, set_order=4)

        resp = client.get(
            f"/api/dashboard/daily-beta?days=3&end_date={end.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        by_date = {p["date"]: p for p in data["points"]}
        wo = by_date["2026-03-09"]
        assert wo["worked_out"] is True
        assert wo["exercise_count"] == 2
        assert wo["set_count"] == 4  # 2 RPE sets each
        # Daily β is the mean of two exercise βs; bench was strong so the
        # mean should still be > 0.
        assert wo["beta"] is not None
        assert wo["beta"] > 0.0

    def test_two_sessions_same_day_aggregate(self, client, session):
        bench = _make_exercise(session, name="Bench Press")
        squat = _make_exercise(session, name="Back Squat")
        end = date(2026, 3, 10)
        _seed_history(
            session, bench, baseline_date=end - timedelta(days=15),
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        _seed_history(
            session, squat, baseline_date=end - timedelta(days=15),
            weights=[200, 220, 240, 260, 280, 300],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Two distinct sessions on the same date.
        ws_am = _make_session(session, date(2026, 3, 9))
        _add_set(session, ws_am.id, bench.id,
                 weight=180, reps=10, rpe=8.0, set_order=1)
        _add_set(session, ws_am.id, bench.id,
                 weight=190, reps=9, rpe=8.5, set_order=2)
        ws_pm = _make_session(session, date(2026, 3, 9))
        _add_set(session, ws_pm.id, squat.id,
                 weight=240, reps=8, rpe=8.5, set_order=1)
        _add_set(session, ws_pm.id, squat.id,
                 weight=240, reps=7, rpe=9.0, set_order=2)

        resp = client.get(
            f"/api/dashboard/daily-beta?days=2&end_date={end.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        by_date = {p["date"]: p for p in data["points"]}
        wo = by_date["2026-03-09"]
        assert wo["worked_out"] is True
        assert wo["session_count"] == 2
        assert wo["exercise_count"] == 2
        assert wo["set_count"] == 4

    def test_workout_day_with_no_rpe_sets(self, client, session):
        bench = _make_exercise(session, name="Bench Press")
        end = date(2026, 3, 10)
        _seed_history(
            session, bench, baseline_date=end - timedelta(days=15),
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Workout exists but no RPE on today's sets → no eligible β,
        # but worked_out=true so the day still gets a hollow node visually.
        ws = _make_session(session, date(2026, 3, 9))
        _add_set(session, ws.id, bench.id,
                 weight=180, reps=10, rpe=None, set_order=1)

        resp = client.get(
            f"/api/dashboard/daily-beta?days=2&end_date={end.isoformat()}"
        )
        assert resp.status_code == 200
        data = resp.json()
        by_date = {p["date"]: p for p in data["points"]}
        wo = by_date["2026-03-09"]
        assert wo["worked_out"] is True
        assert wo["session_count"] == 1
        assert wo["beta"] is None
        assert wo["exercise_count"] == 0
        assert wo["set_count"] == 0

    def test_days_bounds(self, client):
        resp = client.get("/api/dashboard/daily-beta?days=0")
        assert resp.status_code == 422
        resp = client.get("/api/dashboard/daily-beta?days=91")
        assert resp.status_code == 422
        resp = client.get("/api/dashboard/daily-beta?days=1&end_date=2026-03-07")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dates"] == ["2026-03-07"]
        assert len(data["points"]) == 1
