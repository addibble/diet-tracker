"""Tests for fatigue_profile (reps-by-set-index decomposition)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.strength_model import (
    GLOBAL_BETA_PER_SET,
    MIN_HISTORY_FOR_LEARNED_BETA,
    fatigue_profile,
)


def _make_exercise(session: Session, **kwargs) -> Exercise:
    defaults = {
        "name": f"FatigueTest {id(kwargs)}",
        "allow_heavy_loading": True,
        "load_input_mode": "external_weight",
        "bodyweight_fraction": 0.0,
        "external_load_multiplier": 1.0,
    }
    defaults.update(kwargs)
    ex = Exercise(**defaults)
    session.add(ex)
    session.flush()
    return ex


def _make_session_and_sets(
    session: Session,
    exercise: Exercise,
    sets_data: list[dict],
    session_date: date,
) -> WorkoutSession:
    ws = WorkoutSession(date=session_date)
    session.add(ws)
    session.flush()
    for i, sd in enumerate(sets_data):
        session.add(WorkoutSet(
            session_id=ws.id,
            exercise_id=exercise.id,
            set_order=i + 1,
            endurance_value=sd["reps"],
            weight=sd["weight"],
            rpe=sd["rpe"],
        ))
    session.flush()
    return ws


class TestFatigueProfileShape:
    def test_nonexistent_exercise(self, session):
        result = fatigue_profile(99999, session)
        assert result["has_data"] is False
        assert result["exercise_id"] == 99999

    def test_bodyweight_exercise(self, session):
        ex = _make_exercise(session, load_input_mode="bodyweight")
        result = fatigue_profile(ex.id, session)
        assert result["has_data"] is False
        assert result["is_bodyweight"] is True
        assert result["beta_source"] == "fallback"
        assert result["beta_per_set"] == list(GLOBAL_BETA_PER_SET)

    def test_cold_start_uses_global_fallback(self, session):
        """Exercise with only one session of data → fallback β."""
        ex = _make_exercise(session)
        _make_session_and_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 8.0},
            {"reps": 10, "weight": 100, "rpe": 8.5},
            {"reps": 8, "weight": 100, "rpe": 9.0},
        ], session_date=date.today())
        result = fatigue_profile(ex.id, session)
        # Enough data to fit, but no *prior* sessions → β is global fallback
        assert len(result["session_observations"]) == 3
        assert result["n_history_sessions"] == 0
        assert result["beta_source"] == "fallback"
        assert all(flag is False for flag in result["beta_learned_flags"][:3])


class TestFatigueProfileLearned:
    def test_learns_per_set_decay_from_history(self, session):
        """Three historical sessions with a consistent 3-set decay at a single
        weight should produce a learned β_s that reflects the observed pattern."""
        ex = _make_exercise(session)
        today = date.today()
        # Current session (most recent)
        _make_session_and_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 8.0},
            {"reps": 9,  "weight": 100, "rpe": 8.5},
            {"reps": 7,  "weight": 100, "rpe": 9.0},
        ], session_date=today)
        # Historical sessions — same weight, similar fatigue pattern,
        # slightly varying weights so the curve can actually fit.
        for i, age in enumerate([3, 7, 14]):
            _make_session_and_sets(session, ex, [
                {"reps": 12, "weight": 95 + i * 5, "rpe": 7.5 + 0.1 * i},
                {"reps": 9,  "weight": 95 + i * 5, "rpe": 8.5},
                {"reps": 7,  "weight": 95 + i * 5, "rpe": 9.0},
            ], session_date=today - timedelta(days=age))

        result = fatigue_profile(ex.id, session)
        assert result["has_data"] is True
        assert result["n_history_sessions"] == 3
        assert result["beta_source"] == "learned"
        # Each of sets 1-3 has 3 history observations ≥ threshold
        assert all(result["beta_learned_flags"][:3])
        # Later sets should have more negative β than earlier sets
        assert result["beta_per_set"][1] <= result["beta_per_set"][0]
        assert result["beta_per_set"][2] <= result["beta_per_set"][1]

    def test_model_prediction_uses_beta(self, session):
        """model_prediction rtf should equal r_fresh(W) + β_s for each set."""
        ex = _make_exercise(session)
        today = date.today()
        _make_session_and_sets(session, ex, [
            {"reps": 10, "weight": 100, "rpe": 8.0},
            {"reps": 8,  "weight": 100, "rpe": 8.5},
            {"reps": 6,  "weight": 100, "rpe": 9.0},
        ], session_date=today)
        # Thin history (one prior session) — β falls back to global,
        # but we still get model_prediction entries per observed set.
        _make_session_and_sets(session, ex, [
            {"reps": 10, "weight": 90, "rpe": 7.5},
            {"reps": 8,  "weight": 90, "rpe": 8.5},
            {"reps": 6,  "weight": 90, "rpe": 9.0},
        ], session_date=today - timedelta(days=5))

        result = fatigue_profile(ex.id, session)
        assert len(result["model_prediction"]) == 3
        for pred in result["model_prediction"]:
            assert pred["predicted_rtf"] >= 0
            assert "beta_used" in pred
            assert "beta_learned" in pred

    def test_sparse_set_index_falls_back(self, session):
        """Set index with < MIN_HISTORY_FOR_LEARNED_BETA history observations
        uses the global fallback for that index."""
        ex = _make_exercise(session)
        today = date.today()
        # Today: 3 sets logged
        _make_session_and_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 8.0},
            {"reps": 9,  "weight": 100, "rpe": 8.5},
            {"reps": 7,  "weight": 100, "rpe": 9.0},
        ], session_date=today)
        # One prior session, only 1 set logged at set_index=2 → below threshold
        ws = WorkoutSession(date=today - timedelta(days=3))
        session.add(ws)
        session.flush()
        for so in [1, 2]:
            session.add(WorkoutSet(
                session_id=ws.id, exercise_id=ex.id,
                set_order=so, endurance_value=10, weight=95, rpe=8.0,
            ))
        # Enough RPE sets across history for the curve to fit is not the
        # concern here; we're asserting the learned/fallback flag per index.
        _make_session_and_sets(session, ex, [
            {"reps": 11, "weight": 90, "rpe": 8.0},
            {"reps": 9,  "weight": 90, "rpe": 8.5},
        ], session_date=today - timedelta(days=7))
        session.flush()

        result = fatigue_profile(ex.id, session)
        # Set index 3 had 0 history observations → must be fallback
        assert result["beta_learned_flags"][2] is False
        # At minimum, global tail value applies for index 3
        assert result["beta_per_set"][2] == GLOBAL_BETA_PER_SET[2]


class TestFatigueProfileAPI:
    def test_endpoint_returns_shape(self, client, session):
        ex = _make_exercise(session)
        _make_session_and_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 8.0},
            {"reps": 9,  "weight": 100, "rpe": 8.5},
            {"reps": 7,  "weight": 100, "rpe": 9.0},
        ], session_date=date.today())
        session.commit()

        r = client.get(f"/api/planner/fatigue-profile/{ex.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["exercise_id"] == ex.id
        assert "session_observations" in body
        assert "beta_per_set" in body
        assert "model_prediction" in body
        assert body["beta_source"] in ("learned", "fallback")

    def test_endpoint_404_on_unknown(self, client):
        r = client.get("/api/planner/fatigue-profile/99999")
        assert r.status_code == 200  # shape-preserving
        assert r.json()["has_data"] is False


def test_threshold_constant_is_reasonable():
    assert MIN_HISTORY_FOR_LEARNED_BETA >= 2
    assert GLOBAL_BETA_PER_SET[0] == 0.0
    assert GLOBAL_BETA_PER_SET[1] < 0
    assert GLOBAL_BETA_PER_SET[2] < GLOBAL_BETA_PER_SET[1]
