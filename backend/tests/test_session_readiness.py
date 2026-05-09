"""Tests for the v4 readiness module: per-session β fit + persistence."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.session_readiness import (
    BETA_LABEL_CLAMP_HIGH,
    BETA_LABEL_CLAMP_LOW,
    BETA_MAX,
    BETA_MIN,
    fit_session_beta,
    is_beta_clamped,
    readiness_label,
    readiness_pct,
    update_session_readiness,
)


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
    session, ws_id: int, ex_id: int, *, weight: float, reps: int, rpe: float,
    set_order: int = 1,
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
    """Seed enough RPE-eligible history so fit_curve returns a usable curve."""
    for i, (w, r, rpe) in enumerate(zip(weights, reps_list, rpes)):
        # Spread across distinct dates so the t-test filter keeps them.
        d = baseline_date - timedelta(days=2 + i)
        ws = WorkoutSession(date=d)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        _add_set(session, ws.id, ex.id, weight=w, reps=int(r), rpe=rpe)


class TestReadinessLabel:
    def test_none(self):
        assert readiness_label(None) is None
        assert readiness_pct(None) is None

    def test_baseline(self):
        assert readiness_label(0.0) == "baseline"
        assert readiness_pct(0.0) == pytest.approx(0.0)

    def test_strong(self):
        assert readiness_label(0.15) == "strong"

    def test_fatigued(self):
        assert readiness_label(-0.20) == "fatigued"

    def test_pct_matches_exp(self):
        # +0.10 β → +10.5% reps (exp(0.1)-1 ≈ 0.1052)
        assert readiness_pct(0.10) == pytest.approx(10.517, rel=1e-3)


class TestBetaClamping:
    def test_within_band_not_clamped(self):
        assert not is_beta_clamped(0.10)
        assert not is_beta_clamped(-0.20)
        assert not is_beta_clamped(0.0)
        assert not is_beta_clamped(BETA_LABEL_CLAMP_HIGH)
        assert not is_beta_clamped(BETA_LABEL_CLAMP_LOW)

    def test_outside_band_clamped(self):
        assert is_beta_clamped(BETA_LABEL_CLAMP_HIGH + 0.01)
        assert is_beta_clamped(BETA_LABEL_CLAMP_LOW - 0.01)
        assert is_beta_clamped(1.0)
        assert is_beta_clamped(-1.4)

    def test_none_not_clamped(self):
        assert not is_beta_clamped(None)

    def test_label_uses_clamped_band(self):
        # A wildly high raw β should still map to "strong", not blow up.
        assert readiness_label(1.4) == "strong"
        # Same for the low end.
        assert readiness_label(-1.4) == "fatigued"
        # And exactly the clamp boundary stays at "strong" / "fatigued".
        assert readiness_label(BETA_LABEL_CLAMP_HIGH) == "strong"
        assert readiness_label(BETA_LABEL_CLAMP_LOW) == "fatigued"

    def test_pct_uses_raw_beta(self):
        # pct should reflect the raw signal, not the clamped one.
        raw_pct = readiness_pct(1.0)
        assert raw_pct is not None
        assert raw_pct > 100.0  # exp(1)-1 ≈ 1.718 → 171.8%


class TestFitSessionBeta:
    def test_returns_none_for_empty_session(self, session):
        _make_exercise(session)
        ws = _make_session(session, date(2026, 1, 15))
        assert fit_session_beta(session, ws.id) is None

    def test_returns_none_for_missing_session(self, session):
        assert fit_session_beta(session, 99999) is None

    def test_returns_none_for_no_history(self, session):
        ex = _make_exercise(session)
        ws = _make_session(session, date(2026, 1, 15))
        # Two sets in the session, but no prior history → fit_curve returns None
        # → no usable curve → fit returns None.
        _add_set(session, ws.id, ex.id, weight=185, reps=8, rpe=8.0, set_order=1)
        _add_set(session, ws.id, ex.id, weight=185, reps=7, rpe=9.0, set_order=2)
        assert fit_session_beta(session, ws.id) is None

    def test_strong_day_yields_positive_beta(self, session):
        ex = _make_exercise(session)
        baseline = date(2026, 1, 15)
        # Seed 5 prior sessions across distinct dates with consistent reps.
        # This makes the fitted curve stable and predicts ~6 reps at 200 lb.
        _seed_history(
            session, ex, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Today the athlete crushes more reps than predicted at the same weight.
        ws = _make_session(session, baseline)
        _add_set(session, ws.id, ex.id, weight=180, reps=10, rpe=8.0, set_order=1)
        _add_set(session, ws.id, ex.id, weight=190, reps=9, rpe=8.5, set_order=2)
        beta = fit_session_beta(session, ws.id)
        assert beta is not None
        assert beta > 0.0, f"Expected positive β for strong day, got {beta}"
        assert BETA_MIN <= beta <= BETA_MAX

    def test_weak_day_yields_negative_beta(self, session):
        ex = _make_exercise(session)
        baseline = date(2026, 1, 15)
        _seed_history(
            session, ex, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Today: many fewer reps than predicted.
        ws = _make_session(session, baseline)
        _add_set(session, ws.id, ex.id, weight=170, reps=4, rpe=9.0, set_order=1)
        _add_set(session, ws.id, ex.id, weight=160, reps=4, rpe=9.0, set_order=2)
        beta = fit_session_beta(session, ws.id)
        assert beta is not None
        assert beta < 0.0, f"Expected negative β for weak day, got {beta}"


class TestUpdateSessionReadiness:
    def test_persists_to_workout_session(self, session):
        ex = _make_exercise(session)
        baseline = date(2026, 1, 15)
        _seed_history(
            session, ex, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        ws = _make_session(session, baseline)
        _add_set(session, ws.id, ex.id, weight=180, reps=8, rpe=8.5, set_order=1)
        _add_set(session, ws.id, ex.id, weight=190, reps=7, rpe=9.0, set_order=2)

        beta = update_session_readiness(session, ws.id)
        assert beta is not None
        session.refresh(ws)
        assert ws.readiness_beta == pytest.approx(beta, rel=1e-6)

    def test_clears_when_no_data(self, session):
        ws = _make_session(session, date(2026, 1, 15))
        ws.readiness_beta = 0.123
        session.add(ws)
        session.commit()
        result = update_session_readiness(session, ws.id)
        assert result is None
        session.refresh(ws)
        assert ws.readiness_beta is None


class TestSessionBetaEvolution:
    def test_returns_none_for_missing_session(self, session):
        from app.session_readiness import session_beta_evolution
        assert session_beta_evolution(session, 99999) is None

    def test_returns_empty_list_for_no_sets(self, session):
        from app.session_readiness import session_beta_evolution
        ws = _make_session(session, date(2026, 1, 15))
        assert session_beta_evolution(session, ws.id) == []

    def test_one_point_per_exercise_in_completion_order(self, session):
        from app.session_readiness import session_beta_evolution

        bench = _make_exercise(session, name="Bench Press")
        squat = _make_exercise(session, name="Back Squat")
        baseline = date(2026, 1, 15)
        # Prior history for each exercise so curves can fit.
        _seed_history(
            session, bench, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        _seed_history(
            session, squat, baseline_date=baseline,
            weights=[200, 220, 240, 260, 280, 300],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        ws = _make_session(session, baseline)
        # Bench first (set_orders 1-2), then squat (3-4), then bench again (5).
        # Completion order should be by max set_order: bench (5) after squat (4).
        _add_set(session, ws.id, bench.id, weight=180, reps=8, rpe=8.5, set_order=1)
        _add_set(session, ws.id, bench.id, weight=180, reps=7, rpe=9.0, set_order=2)
        _add_set(session, ws.id, squat.id, weight=240, reps=8, rpe=8.5, set_order=3)
        _add_set(session, ws.id, squat.id, weight=240, reps=7, rpe=9.0, set_order=4)
        _add_set(session, ws.id, bench.id, weight=185, reps=6, rpe=9.0, set_order=5)

        points = session_beta_evolution(session, ws.id)
        assert points is not None
        assert [p["exercise_id"] for p in points] == [squat.id, bench.id]
        assert points[0]["exercise_name"] == "Back Squat"
        assert points[1]["exercise_name"] == "Bench Press"
        # Per-exercise set_count: each entry counts only its own RPE-eligible
        # obs (squat has 2, bench has 3).
        assert points[0]["set_count"] == 2
        assert points[1]["set_count"] == 3
        # Each point has either a numeric β or null with the contract fields.
        for p in points:
            assert "beta" in p
            assert p["beta"] is None or BETA_MIN <= p["beta"] <= BETA_MAX

    def test_per_exercise_beta_isolation(self, session):
        """β for exercise A must not be influenced by exercise B's sets."""
        from app.session_readiness import session_beta_evolution

        bench = _make_exercise(session, name="Bench Press")
        squat = _make_exercise(session, name="Back Squat")
        baseline = date(2026, 1, 15)
        _seed_history(
            session, bench, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        _seed_history(
            session, squat, baseline_date=baseline,
            weights=[200, 220, 240, 260, 280, 300],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )

        # Same WorkoutSession (so curve as_of is identical across both
        # measurements); add bench sets between the two evolution reads
        # and verify squat's β is unaffected.
        ws = _make_session(session, baseline)
        squat_a = _add_set(session, ws.id, squat.id,
                           weight=240, reps=8, rpe=8.5, set_order=1)
        squat_b = _add_set(session, ws.id, squat.id,
                           weight=240, reps=7, rpe=9.0, set_order=2)
        before = session_beta_evolution(session, ws.id)
        assert before is not None
        squat_before = next(p for p in before if p["exercise_id"] == squat.id)
        assert squat_before["beta"] is not None

        # Now add some bench sets (a "strong day" pattern). Per-exercise
        # contract: this must not move squat's β.
        _add_set(session, ws.id, bench.id,
                 weight=180, reps=14, rpe=8.0, set_order=3)
        _add_set(session, ws.id, bench.id,
                 weight=190, reps=12, rpe=8.5, set_order=4)
        after = session_beta_evolution(session, ws.id)
        assert after is not None
        squat_after = next(p for p in after if p["exercise_id"] == squat.id)
        assert squat_after["beta"] is not None
        bench_after = next(p for p in after if p["exercise_id"] == bench.id)
        assert bench_after["beta"] is not None and bench_after["beta"] > 0.0

        assert squat_after["beta"] == pytest.approx(
            squat_before["beta"], abs=1e-9,
        ), (
            f"Adding bench sets moved squat β: "
            f"{squat_before['beta']} → {squat_after['beta']}"
        )
        # Sanity: silence unused-locals warnings if any.
        _ = (squat_a, squat_b)

    def test_per_exercise_beta_no_prior_curve(self, session):
        """Exercise with today's RPE sets but no prior history → β is None."""
        from app.session_readiness import session_beta_evolution

        ex = _make_exercise(session, name="New Lift")
        ws = _make_session(session, date(2026, 1, 15))
        # No history; just two RPE-eligible sets today.
        _add_set(session, ws.id, ex.id,
                 weight=100, reps=8, rpe=8.5, set_order=1)
        _add_set(session, ws.id, ex.id,
                 weight=110, reps=7, rpe=9.0, set_order=2)

        points = session_beta_evolution(session, ws.id)
        assert points is not None
        assert len(points) == 1
        # 2 RPE-eligible obs but no fittable prior curve → β is None.
        assert points[0]["beta"] is None
        assert points[0]["set_count"] == 2

    def test_node_per_exercise_even_without_rpe(self, session):
        from app.session_readiness import session_beta_evolution

        bench = _make_exercise(session, name="Bench Press")
        baseline = date(2026, 1, 15)
        _seed_history(
            session, bench, baseline_date=baseline,
            weights=[150, 160, 170, 180, 190, 200],
            reps_list=[10, 9, 8, 7, 6, 5],
            rpes=[8.5, 8.5, 8.5, 8.5, 8.5, 9.0],
        )
        # Logged set with NO rpe → not RPE-eligible. Should still produce a
        # node, with beta=None (not enough cumulative observations).
        ws = _make_session(session, baseline)
        s = WorkoutSet(
            session_id=ws.id, exercise_id=bench.id, set_order=1,
            weight=180, endurance_value=8.0, rpe=None,
        )
        session.add(s)
        session.commit()

        points = session_beta_evolution(session, ws.id)
        assert points is not None
        assert len(points) == 1
        assert points[0]["beta"] is None
        assert points[0]["set_count"] == 0  # no RPE-eligible obs for this exercise
