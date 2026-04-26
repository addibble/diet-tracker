"""Tests for burnout-mode prescription, RIR=3,2,1 bootstrap progression,
and the historical-mean starting-weight anchor."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.strength_model import (
    _BOOT_RIR,
    _starting_weight_from_history,
    bootstrap_prescription,
    check_burnout_availability,
    get_mean_recent_entered_weight,
    prescribe_next_set,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _heavy_capable_exercise(session: Session, ex_id: int = 1) -> Exercise:
    ex = Exercise(
        id=ex_id, name=f"Bench {ex_id}", set_metric_mode="reps",
        allow_heavy_loading=True, load_input_mode="external_weight",
    )
    session.add(ex)
    session.commit()
    return ex


def _seed_set(session: Session, ex_id: int, sess_id: int, weight: float,
              reps: int = 10, rpe: float | None = None,
              days_ago: int = 5, set_order: int = 1) -> None:
    if not session.get(WorkoutSession, sess_id):
        session.add(WorkoutSession(id=sess_id, date=date.today() - timedelta(days=days_ago)))
        session.flush()
    session.add(WorkoutSet(
        session_id=sess_id, exercise_id=ex_id, set_order=set_order,
        weight=weight, endurance_value=reps, rpe=rpe,
    ))
    session.commit()


# ── Bootstrap RIR progression ────────────────────────────────────────


def test_bootstrap_rir_progression_is_three_two_one():
    """User explicitly wants set 3 to be RIR 1, not RIR 2."""
    assert _BOOT_RIR == (3, 2, 1)


# ── Historical-mean starting weight ─────────────────────────────────


class TestHistoricalMean:
    def test_no_history_returns_zero(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        assert get_mean_recent_entered_weight(1, session) is None
        assert _starting_weight_from_history(1, session) == 0.0

    def test_returns_mean_of_recent_weights(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        _seed_set(session, ex_id=1, sess_id=1, weight=100, set_order=1)
        _seed_set(session, ex_id=1, sess_id=1, weight=200, set_order=2)
        assert get_mean_recent_entered_weight(1, session) == 150.0
        assert _starting_weight_from_history(1, session) == 150.0

    def test_bootstrap_stage0_anchor_uses_mean(self, session):
        """Stage 0 (no RPE'd observations) seeds proposed_weight with the
        historical mean instead of None / a static high default."""
        _heavy_capable_exercise(session, ex_id=1)
        # No-RPE history → not loaded as bootstrap observations, but still
        # contributes to the historical mean.
        _seed_set(session, ex_id=1, sess_id=1, weight=40, rpe=None)
        out = bootstrap_prescription(1, session, prior_sets=[], bodyweight_lb=180.0)
        assert out["next_set"]["proposed_weight"] == 40.0
        assert out["next_set"]["effective_weight"] == 40.0

    def test_bootstrap_stage0_anchor_is_none_without_history(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        out = bootstrap_prescription(1, session, prior_sets=[], bodyweight_lb=180.0)
        # Falsy 0 collapses to None for the UI ("no suggestion" state).
        assert out["next_set"]["proposed_weight"] is None


# ── Burnout mode ─────────────────────────────────────────────────────


class TestBurnoutAvailability:
    def test_unavailable_without_history(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        avail = check_burnout_availability(1, session)
        assert avail["available"] is False
        assert "history" in (avail["reason"] or "").lower()

    def test_available_with_recent_history(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        _seed_set(session, ex_id=1, sess_id=1, weight=200)
        avail = check_burnout_availability(1, session)
        assert avail["available"] is True

    def test_available_for_non_heavy_exercise(self, session):
        ex = Exercise(
            id=1, name="Curl", set_metric_mode="reps",
            allow_heavy_loading=False, load_input_mode="external_weight",
        )
        session.add(ex)
        session.commit()
        _seed_set(session, ex_id=1, sess_id=1, weight=40)
        avail = check_burnout_availability(1, session)
        assert avail["available"] is True

    def test_unavailable_for_bodyweight(self, session):
        ex = Exercise(
            id=1, name="Pull-up", set_metric_mode="reps",
            allow_heavy_loading=True, load_input_mode="bodyweight",
        )
        session.add(ex)
        session.commit()
        avail = check_burnout_availability(1, session)
        assert avail["available"] is False


class TestBurnoutPrescription:
    def test_prescribes_half_max_with_amrap(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        # Recent max=200, 100 (so max=200 → burnout proposes 100)
        _seed_set(session, ex_id=1, sess_id=1, weight=200, set_order=1)
        _seed_set(session, ex_id=1, sess_id=1, weight=100, set_order=2)

        out = prescribe_next_set(
            1, session, prior_sets=[], bodyweight_lb=180.0,
            training_mode="burnout",
        )
        assert out["mode"] == "burnout"
        assert out["exercise_complete"] is False
        assert out["next_set"]["proposed_weight"] == 100.0
        assert out["next_set"]["target_rir"] == 0
        assert out["next_set"]["target_rpe"] == 10.0
        assert out["next_set"]["amrap"] is True

    def test_complete_after_one_set(self, session):
        _heavy_capable_exercise(session, ex_id=1)
        _seed_set(session, ex_id=1, sess_id=1, weight=200)
        out = prescribe_next_set(
            1, session, prior_sets=[{"weight": 100, "reps": 30, "rpe": 10.0}],
            bodyweight_lb=180.0, training_mode="burnout",
        )
        assert out["exercise_complete"] is True
        assert out["next_set"] is None

    def test_no_history_falls_through_to_normal(self, session):
        """Without recent history, burnout has no max to halve; the planner
        falls through to whatever the next non-burnout step would do
        (bootstrap / curve fit). Critically, it must not crash."""
        _heavy_capable_exercise(session, ex_id=1)
        out = prescribe_next_set(
            1, session, prior_sets=[], bodyweight_lb=180.0,
            training_mode="burnout",
        )
        # Falls through to bootstrap (no fit + no obs).
        assert out.get("mode") != "burnout"
