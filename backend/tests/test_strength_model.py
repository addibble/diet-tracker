"""Tests for strength_model.py -- curve fitting and set prescription."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from sqlmodel import Session

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.strength_model import (
    DEFAULT_GAMMA,
    CurveFit,
    _brzycki_1rm,
    _entered_to_effective,
    _estimate_M_bounds,
    _filter_stale_sessions,
    _identifiability_score,
    _rpe_confidence,
    adjust_prescription,
    detect_inflection,
    fit_curve,
    fit_from_data,
    fresh_curve,
    get_bodyweight_suggestion,
    get_exercise_freshness,
    plan_progressive_sets,
    predict_reps,
    refit_with_observations,
    solve_weight,
)

# ── Unit tests for pure math functions ──


class TestFreshCurve:
    def test_basic_prediction(self):
        """r_fresh should decrease as weight increases."""
        M, k, gamma = 200.0, 15.0, 0.5
        reps_at_100 = fresh_curve(100.0, M, k, gamma)
        reps_at_150 = fresh_curve(150.0, M, k, gamma)
        assert reps_at_100 > reps_at_150 > 0

    def test_zero_at_max(self):
        """r_fresh should be 0 at W = M."""
        assert fresh_curve(200.0, 200.0, 15.0, 0.5) == 0.0

    def test_zero_above_max(self):
        """r_fresh should be 0 for W > M."""
        assert fresh_curve(250.0, 200.0, 15.0, 0.5) == 0.0

    def test_numpy_array(self):
        """Should work with numpy arrays."""
        W = np.array([100.0, 150.0, 200.0, 250.0])
        result = fresh_curve(W, 200.0, 15.0, 0.5)
        assert result[0] > result[1] > 0
        assert result[2] == 0.0
        assert result[3] == 0.0


class TestSolveWeight:
    def test_round_trip(self):
        """solve_weight should invert predict_reps."""
        fit = CurveFit(M=200, k=15, gamma=0.5, n_obs=20, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        for target in [5.0, 10.0, 15.0, 20.0]:
            W = solve_weight(target, fit)
            predicted = predict_reps(W, fit)
            assert abs(predicted - target) < 0.01, f"Round-trip failed for target={target}"

    def test_higher_reps_lower_weight(self):
        """More target reps should produce lower weight."""
        fit = CurveFit(M=200, k=15, gamma=0.5, n_obs=20, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        w5 = solve_weight(5.0, fit)
        w15 = solve_weight(15.0, fit)
        assert w5 > w15

    def test_zero_reps(self):
        """Zero target reps should return near M."""
        fit = CurveFit(M=200, k=15, gamma=0.5, n_obs=20, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        w = solve_weight(0, fit)
        assert w == 200 * 0.95


class TestRPEConfidence:
    def test_rpe_10_highest(self):
        assert _rpe_confidence(10.0) == pytest.approx(1.0)

    def test_rpe_decreasing(self):
        c10 = _rpe_confidence(10.0)
        c8 = _rpe_confidence(8.0)
        c6 = _rpe_confidence(6.0)
        assert c10 > c8 > c6

    def test_min_floor(self):
        assert _rpe_confidence(5.0) >= 0.2


class TestBrzycki:
    def test_known_value(self):
        # At 1 rep, 1RM = weight
        assert _brzycki_1rm(100, 1) == pytest.approx(100 * 36 / 36)

    def test_high_reps_cap(self):
        # At 37+ reps, use rough extrapolation
        result = _brzycki_1rm(100, 40)
        assert result == 250.0


class TestMBounds:
    def test_with_data(self):
        weights = [100.0, 120.0, 140.0]
        reps = [12.0, 8.0, 5.0]
        lower, upper, M_prior = _estimate_M_bounds(weights, reps)
        assert lower > max(weights)
        assert upper > lower
        assert M_prior > 0

    def test_no_reps(self):
        weights = [100.0, 120.0]
        reps = [0.0, 0.0]  # no valid Brzycki estimates
        lower, upper, M_prior = _estimate_M_bounds(weights, reps)
        assert lower == 120.0 * 1.01
        assert M_prior == 120.0 * 1.3


class TestIdentifiability:
    def test_few_obs_zero(self):
        assert _identifiability_score([100], [10]) == 0.0

    def test_varied_data_higher(self):
        # Wide weight range + varied reps = high identifiability
        s1 = _identifiability_score([50, 100, 150], [20, 10, 5])
        # Narrow range
        s2 = _identifiability_score([100, 102, 101], [10, 10, 10])
        assert s1 > s2


# ── Integration tests with DB ──


def _make_exercise(session: Session, **kwargs) -> Exercise:
    defaults = {
        "name": f"Test Exercise {id(kwargs)}",
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
    session_date: date | None = None,
) -> WorkoutSession:
    ws = WorkoutSession(date=session_date or date.today())
    session.add(ws)
    session.flush()
    for i, sd in enumerate(sets_data):
        wset = WorkoutSet(
            session_id=ws.id,
            exercise_id=exercise.id,
            set_order=i + 1,
            reps=sd.get("reps"),
            weight=sd.get("weight"),
            rpe=sd.get("rpe"),
        )
        session.add(wset)
    session.flush()
    return ws


class TestFitCurve:
    def test_nonexistent_exercise(self, session):
        result = fit_curve(99999, session)
        assert result is None

    def test_bodyweight_exercise_returns_none(self, session):
        ex = _make_exercise(session, name="BW Test", load_input_mode="bodyweight")
        result = fit_curve(ex.id, session)
        assert result is None

    def test_insufficient_data_returns_none(self, session):
        ex = _make_exercise(session, name="Sparse Test")
        # Only 2 sets (< MIN_SETS_TIER2=3)
        _make_session_and_sets(session, ex, [
            {"reps": 10, "weight": 100, "rpe": 8.0},
            {"reps": 8, "weight": 120, "rpe": 8.0},
        ])
        result = fit_curve(ex.id, session)
        assert result is None

    def test_sufficient_data_fits(self, session):
        ex = _make_exercise(session, name="Good Data Test")
        # 6 sets at varied weights — should qualify for tier1
        sets_data = [
            {"reps": 15, "weight": 80, "rpe": 7.0},
            {"reps": 12, "weight": 100, "rpe": 7.0},
            {"reps": 10, "weight": 120, "rpe": 8.0},
            {"reps": 8, "weight": 130, "rpe": 8.0},
            {"reps": 6, "weight": 150, "rpe": 9.0},
            {"reps": 4, "weight": 160, "rpe": 9.0},
        ]
        _make_session_and_sets(session, ex, sets_data)
        result = fit_curve(ex.id, session)

        assert result is not None
        assert result.M > 160  # M should exceed max weight
        assert result.k > 0
        assert result.gamma > 0
        assert result.n_obs == 6
        assert result.rmse < 10  # reasonable fit
        assert result.fit_tier in ("tier1", "tier2")  # tier depends on session count

    def test_tier2_with_single_weight(self, session):
        ex = _make_exercise(session, name="Single Weight Test")
        # 4 sets at same weight — tier2 (< 2 distinct weights)
        sets_data = [
            {"reps": 10, "weight": 100, "rpe": 7.0},
            {"reps": 9, "weight": 100, "rpe": 8.0},
            {"reps": 8, "weight": 100, "rpe": 8.0},
            {"reps": 7, "weight": 100, "rpe": 9.0},
        ]
        _make_session_and_sets(session, ex, sets_data)
        result = fit_curve(ex.id, session)

        assert result is not None
        assert result.fit_tier == "tier2"
        assert result.gamma == DEFAULT_GAMMA

    def test_old_data_excluded(self, session):
        ex = _make_exercise(session, name="Old Data Test")
        # Sets from 60 days ago — outside 30-day window
        old_date = date.today() - timedelta(days=60)
        _make_session_and_sets(session, ex, [
            {"reps": 10, "weight": 100, "rpe": 8.0},
            {"reps": 8, "weight": 120, "rpe": 8.0},
            {"reps": 6, "weight": 140, "rpe": 9.0},
        ], session_date=old_date)
        result = fit_curve(ex.id, session)
        assert result is None  # all data too old

    def test_no_rpe_excluded(self, session):
        ex = _make_exercise(session, name="No RPE Test")
        _make_session_and_sets(session, ex, [
            {"reps": 10, "weight": 100, "rpe": None},
            {"reps": 8, "weight": 120, "rpe": None},
            {"reps": 6, "weight": 140, "rpe": None},
        ])
        result = fit_curve(ex.id, session)
        assert result is None  # no RPE = no data


class TestPlanProgressiveSets:
    def _make_fit(self) -> CurveFit:
        return CurveFit(M=200, k=15, gamma=0.5, n_obs=20, rmse=1.0,
                         max_observed_weight=180, fit_tier="tier1")

    def test_heavy_scheme_three_sets(self):
        fit = self._make_fit()
        ex = Exercise(name="Bench", allow_heavy_loading=True,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180)
        assert len(prescriptions) == 3

        # Weight should increase across sets
        assert prescriptions[0].effective_weight < prescriptions[1].effective_weight
        assert prescriptions[1].effective_weight < prescriptions[2].effective_weight

        # RPE should increase
        assert prescriptions[0].target_rpe < prescriptions[1].target_rpe
        assert prescriptions[1].target_rpe < prescriptions[2].target_rpe

    def test_light_scheme_three_sets(self):
        fit = self._make_fit()
        ex = Exercise(name="Lateral Raise", allow_heavy_loading=False,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180)
        assert len(prescriptions) == 3
        # Light scheme: higher reps overall
        assert prescriptions[0].target_reps > prescriptions[2].target_reps

    def test_max_weight_soft_cap(self):
        """Soft cap allows up to 125% of historical max."""
        fit = self._make_fit()
        ex = Exercise(name="Machine Press", allow_heavy_loading=True,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180,
                                              max_entered_weight=150)
        # The heaviest set shouldn't exceed 125% of 150 = 187.5
        for p in prescriptions:
            if p.entered_weight is not None:
                assert p.entered_weight <= 187.5

    def test_monotonicity_guard_prevents_absurd_prescription(self):
        """Regression: clipping weight must not produce more reps at higher RPE.

        Back Extension bug: user did 200 lb x 13 @ RPE 8 (set 2). Curve solves
        258 lb for set 3 but gets clipped to 200, producing 14 reps @ RPE 9.
        The monotonicity guard should bump weight above 200 instead.
        """
        # Use plan_progressive_sets with a strong curve and a low max_weight
        # to trigger clipping that would violate monotonicity
        fit = CurveFit(M=350, k=28, gamma=0.7, n_obs=10, rmse=1.0,
                        max_observed_weight=200, fit_tier="tier1")
        ex = Exercise(name="Back Extension Machine", allow_heavy_loading=True,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180,
                                              max_entered_weight=200)
        # Weight must monotonically increase across sets
        for i in range(1, len(prescriptions)):
            assert prescriptions[i].entered_weight >= prescriptions[i-1].entered_weight, (
                f"Set {i+1} weight {prescriptions[i].entered_weight} < "
                f"Set {i} weight {prescriptions[i-1].entered_weight}"
            )

    def test_entered_weight_is_set(self):
        fit = self._make_fit()
        ex = Exercise(name="Cable Fly", allow_heavy_loading=False,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180)
        for p in prescriptions:
            assert p.entered_weight is not None
            assert p.entered_weight > 0


class TestAdjustPrescription:
    def test_adjust_recalculates(self):
        fit = CurveFit(M=200, k=15, gamma=0.5, n_obs=20, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        ex = Exercise(name="Bench", allow_heavy_loading=True,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)

        p = adjust_prescription(fit, ex, actual_entered_weight=130,
                                bodyweight_lb=180, set_number=1, allow_heavy=True)
        assert p.set_number == 1
        assert p.entered_weight == 130
        assert p.target_reps > 0
        assert p.target_rpe == 7.0  # set 1 heavy scheme


class TestAllowHeavyTierGating:
    """Non-heavy exercises should always get tier2 (fixed gamma)."""

    def test_fit_from_data_non_heavy_forces_tier2(self):
        """Even with enough data for tier1, non-heavy gets tier2."""
        # 6 obs across 2+ sessions, distinct weights — would be tier1 if heavy
        weights = [100.0, 120.0, 140.0, 100.0, 120.0, 140.0]
        reps = [20.0, 15.0, 10.0, 19.0, 14.0, 9.0]
        confs = [1.0] * 6
        ages = [7.0, 7.0, 7.0, 0.0, 0.0, 0.0]

        fit_heavy = fit_from_data(weights, reps, confs, ages, allow_heavy=True)
        fit_light = fit_from_data(weights, reps, confs, ages, allow_heavy=False)

        assert fit_heavy is not None
        assert fit_light is not None
        assert fit_light.fit_tier == "tier2"
        assert fit_light.gamma == DEFAULT_GAMMA

    def test_fit_curve_non_heavy_forces_tier2(self, session):
        """fit_curve with allow_heavy=False always returns tier2."""
        ex = _make_exercise(session, name="Light Exercise", allow_heavy_loading=False)
        # Seed enough varied data across 2 sessions for tier1 eligibility
        ws1 = WorkoutSession(date=date.today() - timedelta(days=7))
        session.add(ws1)
        session.flush()
        for w, r, rpe in [(80, 15, 7.0), (100, 12, 8.0), (120, 8, 9.0)]:
            session.add(WorkoutSet(
                session_id=ws1.id, exercise_id=ex.id, set_order=1,
                reps=r, weight=w, rpe=rpe,
            ))
        ws2 = WorkoutSession(date=date.today())
        session.add(ws2)
        session.flush()
        for w, r, rpe in [(80, 14, 7.0), (100, 11, 8.0), (120, 7, 9.0)]:
            session.add(WorkoutSet(
                session_id=ws2.id, exercise_id=ex.id, set_order=1,
                reps=r, weight=w, rpe=rpe,
            ))
        session.flush()

        fit = fit_curve(ex.id, session, allow_heavy=False)
        assert fit is not None
        assert fit.fit_tier == "tier2"
        assert fit.gamma == DEFAULT_GAMMA


class TestLightScheme:
    """LIGHT_SCHEME should target 18+RIR3, 15+RIR2, 12+RIR1."""

    def test_light_scheme_rep_targets(self):
        fit = CurveFit(M=200, k=15, gamma=0.9, n_obs=10, rmse=1.0,
                        max_observed_weight=150, fit_tier="tier2")
        ex = Exercise(name="Lateral Raise", allow_heavy_loading=False,
                      load_input_mode="external_weight",
                      bodyweight_fraction=0.0, external_load_multiplier=1.0)
        prescriptions = plan_progressive_sets(fit, ex, bodyweight_lb=180)
        assert len(prescriptions) == 3
        # RPE targets: 7, 8, 9
        assert prescriptions[0].target_rpe == 7.0
        assert prescriptions[1].target_rpe == 8.0
        assert prescriptions[2].target_rpe == 9.0
        # Reps should be in the 12-18 range (metabolic failure scheme)
        assert prescriptions[0].target_reps >= 12
        assert prescriptions[2].target_reps >= 8


class TestDetectInflection:
    """Tests for inflection-aware stopping logic."""

    def _make_heavy_exercise(self):
        return Exercise(name="Squat", allow_heavy_loading=True,
                        load_input_mode="external_weight",
                        bodyweight_fraction=0.0, external_load_multiplier=1.0)

    def _make_light_exercise(self):
        return Exercise(name="Cable Fly", allow_heavy_loading=False,
                        load_input_mode="external_weight",
                        bodyweight_fraction=0.0, external_load_multiplier=1.0)

    def test_fatigue_inflection_detected(self):
        """When per-set 1RM declines, fatigue inflection is detected."""
        fit = CurveFit(M=200, k=20, gamma=0.7, n_obs=10, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        ex = self._make_heavy_exercise()
        # Set 3 shows declining 1RM (fatigue)
        sets = [
            {"weight": 100, "reps": 15, "rpe": 7.0},
            {"weight": 140, "reps": 10, "rpe": 8.0},
            {"weight": 170, "reps": 3, "rpe": 10.0},  # very fatigued
        ]
        result = detect_inflection(fit, sets, ex, bodyweight_lb=180)
        assert result.inflecting is True
        assert result.estimated_1rm is not None

    def test_curve_inflection_past_inflection_point(self):
        """When last set is past M*(γ+1)/2, curve inflection stops exercise."""
        # gamma=0.7, M=200 → inflection at 200*(0.7+1)/2 = 170
        fit = CurveFit(M=200, k=20, gamma=0.7, n_obs=10, rmse=1.0,
                        max_observed_weight=190, fit_tier="tier1")
        ex = self._make_heavy_exercise()
        # Last set at 180 > inflection (170) — model is constrained
        sets = [
            {"weight": 100, "reps": 15, "rpe": 7.0},
            {"weight": 140, "reps": 10, "rpe": 8.0},
            {"weight": 180, "reps": 5, "rpe": 9.0},
        ]
        result = detect_inflection(fit, sets, ex, bodyweight_lb=180)
        assert result.inflecting is True
        assert result.suggested_set4 is None

    def test_not_past_inflection_suggests_heavier(self):
        """When last set is before inflection, suggests heavier set."""
        # gamma=0.9, M=300 → inflection at 300*1.9/2 = 285
        fit = CurveFit(M=300, k=20, gamma=0.9, n_obs=10, rmse=1.0,
                        max_observed_weight=200, fit_tier="tier1")
        ex = self._make_heavy_exercise()
        # Last set at 200 < inflection (285)
        sets = [
            {"weight": 100, "reps": 20, "rpe": 7.0},
            {"weight": 150, "reps": 14, "rpe": 8.0},
            {"weight": 200, "reps": 8, "rpe": 9.0},
        ]
        result = detect_inflection(fit, sets, ex, bodyweight_lb=180)
        assert result.inflecting is False
        assert result.suggested_set4 is not None
        assert result.suggested_set4.entered_weight > 200

    def test_non_heavy_never_suggests_extra_set(self):
        """Light exercises never get a set 4 suggestion even when not fatigued."""
        fit = CurveFit(M=300, k=20, gamma=0.9, n_obs=10, rmse=1.0,
                        max_observed_weight=200, fit_tier="tier2")
        ex = self._make_light_exercise()
        # Sets with non-declining Brzycki 1RM so fatigue isn't triggered
        sets = [
            {"weight": 50, "reps": 18, "rpe": 7.0},
            {"weight": 70, "reps": 15, "rpe": 8.0},
            {"weight": 90, "reps": 12, "rpe": 9.0},
        ]
        result = detect_inflection(fit, sets, ex, bodyweight_lb=180)
        # Should not suggest a set 4 regardless of inflection status
        assert result.suggested_set4 is None

    def test_fewer_than_3_sets_returns_not_inflecting(self):
        fit = CurveFit(M=200, k=20, gamma=0.7, n_obs=10, rmse=1.0,
                        max_observed_weight=180, fit_tier="tier1")
        ex = self._make_heavy_exercise()
        result = detect_inflection(fit, [
            {"weight": 100, "reps": 15, "rpe": 7.0},
            {"weight": 140, "reps": 10, "rpe": 8.0},
        ], ex, bodyweight_lb=180)
        assert result.inflecting is False
        assert result.suggested_set4 is None


class TestRefitWithObservations:
    def test_refit_adds_observations(self, session):
        ex = _make_exercise(session, name="Refit Test")
        # Start with 4 sets
        _make_session_and_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 7.0},
            {"reps": 10, "weight": 100, "rpe": 8.0},
            {"reps": 8, "weight": 120, "rpe": 8.0},
            {"reps": 6, "weight": 140, "rpe": 9.0},
        ])

        # Fit without new obs
        fit1 = fit_curve(ex.id, session)
        assert fit1 is not None

        # Refit with a new strong observation
        fit2 = refit_with_observations(ex.id, session, [
            {"weight": 150, "reps": 5, "rpe": 9.0},
        ])
        assert fit2 is not None
        assert fit2.n_obs == 5  # 4 original + 1 new


class TestEnteredToEffective:
    def test_external_weight(self):
        ex = Exercise(name="T", load_input_mode="external_weight",
                      external_load_multiplier=1.0, bodyweight_fraction=0.0)
        assert _entered_to_effective(ex, 100.0, 180.0) == 100.0

    def test_mixed_mode(self):
        ex = Exercise(name="T", load_input_mode="mixed",
                      external_load_multiplier=1.0, bodyweight_fraction=0.5)
        # effective = entered + bw_component
        assert _entered_to_effective(ex, 50.0, 180.0) == 50.0 + 90.0

    def test_assisted_bodyweight(self):
        ex = Exercise(name="T", load_input_mode="assisted_bodyweight",
                      external_load_multiplier=1.0, bodyweight_fraction=0.8)
        # effective = bw_component - entered
        assert _entered_to_effective(ex, 30.0, 180.0) == 180 * 0.8 - 30.0

    def test_multiplier(self):
        ex = Exercise(name="T", load_input_mode="external_weight",
                      external_load_multiplier=2.0, bodyweight_fraction=0.0)
        assert _entered_to_effective(ex, 50.0, 180.0) == 100.0


class TestGetExerciseFreshness:
    def test_returns_list(self, session):
        result = get_exercise_freshness(session)
        assert isinstance(result, list)
        if result:
            item = result[0]
            assert "exercise_id" in item
            assert "days_since_trained" in item
            assert "has_curve_fit" in item


class TestGetBodyweightSuggestion:
    def test_with_no_data(self, session):
        ex = _make_exercise(session, name="BW Suggest Test",
                            load_input_mode="bodyweight")
        result = get_bodyweight_suggestion(ex.id, session)
        assert result["sets"] == 3
        assert result["reps_per_set"] == 15  # default

    def test_with_data(self, session):
        ex = _make_exercise(session, name="BW Data Test",
                            load_input_mode="bodyweight")
        _make_session_and_sets(session, ex, [
            {"reps": 20, "weight": None, "rpe": 7.0},
            {"reps": 18, "weight": None, "rpe": 8.0},
            {"reps": 22, "weight": None, "rpe": 7.0},
        ])
        result = get_bodyweight_suggestion(ex.id, session)
        assert result["sets"] == 3
        assert result["reps_per_set"] == 20  # median of [20, 18, 22]


# ── API endpoint tests ──


def _seed_exercise_with_sets(session, client_session=None):
    """Helper: create an exercise with enough RPE data for curve fitting."""
    ex = Exercise(
        name="API Test Bench",
        allow_heavy_loading=True,
        load_input_mode="external_weight",
        bodyweight_fraction=0.0,
        external_load_multiplier=1.0,
    )
    session.add(ex)
    session.flush()

    ws = WorkoutSession(date=date.today())
    session.add(ws)
    session.flush()

    for reps, weight, rpe in [
        (15, 80, 7.0), (12, 100, 7.0), (10, 120, 8.0),
        (8, 130, 8.0), (6, 150, 9.0), (4, 160, 9.0),
    ]:
        session.add(WorkoutSet(
            session_id=ws.id, exercise_id=ex.id, set_order=1,
            reps=reps, weight=weight, rpe=rpe,
        ))
    session.flush()
    return ex


class TestExerciseMenuEndpoint:
    def test_returns_list(self, client, session):
        _seed_exercise_with_sets(session)
        resp = client.get("/api/planner/exercise-menu")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        item = data[0]
        assert "exercise_id" in item
        assert "has_curve_fit" in item


class TestPrescribeEndpoint:
    def test_prescribe_set_1(self, client, session):
        ex = _seed_exercise_with_sets(session)
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": ex.id,
            "set_number": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_curve"] is True
        assert data["set"]["set_number"] == 1
        assert data["set"]["proposed_weight"] > 0
        assert data["set"]["target_reps"] > 0

    def test_prescribe_with_weight_override(self, client, session):
        ex = _seed_exercise_with_sets(session)
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": ex.id,
            "set_number": 1,
            "actual_weight": 100.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["set"]["proposed_weight"] == 100.0

    def test_prescribe_with_prior_sets(self, client, session):
        ex = _seed_exercise_with_sets(session)
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": ex.id,
            "set_number": 2,
            "prior_sets": [{"weight": 90, "reps": 14, "rpe": 7.0}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_curve"] is True

    def test_prescribe_set3_respects_monotonicity(self, client, session):
        """Regression: set 3 must not prescribe >= reps at same weight as set 2."""
        ex = _seed_exercise_with_sets(session)
        prior_sets = [
            {"weight": 90, "reps": 15, "rpe": 7.0},
            {"weight": 120, "reps": 13, "rpe": 8.0},
        ]
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": ex.id,
            "set_number": 3,
            "prior_sets": prior_sets,
        })
        assert resp.status_code == 200
        data = resp.json()
        if data.get("has_curve") and data.get("set"):
            s = data["set"]
            # If weight <= set 2's weight, reps must be fewer
            if s["proposed_weight"] <= 120:
                assert s["target_reps"] < 13, (
                    f"Monotonicity violation: {s['proposed_weight']} lb x "
                    f"{s['target_reps']} after 120 lb x 13"
                )

    def test_prescribe_no_data_exercise(self, client, session):
        ex = Exercise(name="No Data", load_input_mode="external_weight")
        session.add(ex)
        session.flush()
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": ex.id,
            "set_number": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_curve"] is False

    def test_prescribe_nonexistent_exercise(self, client):
        resp = client.post("/api/planner/prescribe", json={
            "exercise_id": 99999,
            "set_number": 1,
        })
        assert resp.status_code == 404


class TestPrescribeAllEndpoint:
    def test_prescribe_all(self, client, session):
        ex = _seed_exercise_with_sets(session)
        resp = client.post("/api/planner/prescribe-all", json={
            "exercise_id": ex.id,
            "set_number": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_curve"] is True
        assert len(data["sets"]) == 3
        # Weights should ascend
        assert data["sets"][0]["proposed_weight"] < data["sets"][2]["proposed_weight"]


# ── Tests for outlier filtering ──


class TestFilterStaleSessions:
    """Tests for _filter_stale_sessions t-test filter."""

    def test_same_strength_sessions_kept(self):
        """Sessions with similar 1RM distributions should all be kept."""
        # Two sessions at similar strength: ~160 lb 1RM
        weights = [100.0, 120.0, 100.0, 120.0]
        reps = [12.0, 8.0, 11.0, 7.0]  # similar r_fail
        confs = [0.8, 0.8, 0.8, 0.8]
        ages = [0.0, 0.0, 7.0, 7.0]
        w2, r2, c2, a2, ns = _filter_stale_sessions(weights, reps, confs, ages)
        assert len(w2) == 4  # all kept
        assert ns == 2  # both sessions survived

    def test_different_strength_sessions_dropped(self):
        """Sessions with significantly different 1RM should be dropped."""
        # Recent: high strength (~230 1RM)
        # Old: low strength (~120 1RM)
        weights = [120.0, 120.0, 120.0, 70.0, 70.0, 70.0]
        reps = [18.0, 18.0, 14.0, 15.0, 15.0, 15.0]
        confs = [0.8, 0.8, 0.9, 0.8, 0.8, 0.8]
        ages = [4.0, 4.0, 4.0, 21.0, 21.0, 21.0]
        w2, r2, c2, a2, ns = _filter_stale_sessions(weights, reps, confs, ages)
        # Should drop the 21d-old session
        assert len(w2) == 3
        assert all(a == 4.0 for a in a2)
        assert ns == 1  # only anchor survived

    def test_single_set_sessions_kept(self):
        """Sessions with only 1 set can't be t-tested — keep them."""
        weights = [120.0, 120.0, 70.0]
        reps = [18.0, 18.0, 15.0]
        confs = [0.8, 0.8, 0.8]
        ages = [4.0, 4.0, 21.0]  # 21d session has 1 set
        w2, r2, c2, a2, ns = _filter_stale_sessions(weights, reps, confs, ages)
        assert len(w2) == 3  # all kept (can't t-test single-set session)
        assert ns == 2

    def test_too_few_observations_fallback(self):
        """If filtering would leave < MIN_SETS, return unfiltered."""
        # Only 2 recent sets + 3 stale → dropping stale leaves only 2
        weights = [120.0, 120.0, 70.0, 70.0, 70.0]
        reps = [18.0, 18.0, 15.0, 15.0, 15.0]
        confs = [0.8, 0.8, 0.8, 0.8, 0.8]
        ages = [4.0, 4.0, 21.0, 21.0, 21.0]
        w2, r2, c2, a2, ns = _filter_stale_sessions(weights, reps, confs, ages)
        # Falls back to unfiltered since 2 < MIN_SETS_TIER2=3
        assert len(w2) == 5
        assert ns == 2  # fallback reports all original sessions

    def test_single_session_no_filter(self):
        """All sets from same session → nothing to filter."""
        weights = [80.0, 100.0, 120.0]
        reps = [15.0, 12.0, 8.0]
        confs = [0.8, 0.8, 0.9]
        ages = [0.0, 0.0, 0.0]
        w2, r2, c2, a2, ns = _filter_stale_sessions(weights, reps, confs, ages)
        assert len(w2) == 3
        assert ns == 1


class TestRPEFloor:
    """RPE floor should exclude sets with RPE < MIN_RPE_FOR_FIT."""

    def test_low_rpe_sets_excluded(self, session):
        ex = _make_exercise(session, name="RPE Floor Test")
        # 3 good sets + 2 low-RPE sets
        sets_data = [
            {"reps": 15, "weight": 80, "rpe": 7.0},
            {"reps": 12, "weight": 100, "rpe": 8.0},
            {"reps": 10, "weight": 120, "rpe": 9.0},
            {"reps": 20, "weight": 60, "rpe": 5.0},  # should be excluded
            {"reps": 18, "weight": 70, "rpe": 6.0},  # should be excluded
        ]
        _make_session_and_sets(session, ex, sets_data)
        result = fit_curve(ex.id, session)
        assert result is not None
        assert result.n_obs == 3  # only the 3 good sets


class TestAutoDemotion:
    """Auto-demotion should force tier 2 when gamma is unreasonably low."""

    def test_auto_demote_with_inverted_data(self, session):
        """Data where more reps at higher weight should auto-demote to tier2."""
        ex = _make_exercise(session, name="Auto Demote Test")
        # Inverted pattern: more reps at higher weight (rapid progression)
        # Single session → n_sessions_kept=1 → tier2
        sets_data = [
            {"reps": 15, "weight": 70, "rpe": 7.0},
            {"reps": 14, "weight": 90, "rpe": 8.0},
            {"reps": 14, "weight": 90, "rpe": 8.0},
            {"reps": 14, "weight": 90, "rpe": 8.0},
            {"reps": 18, "weight": 120, "rpe": 7.0},
            {"reps": 18, "weight": 120, "rpe": 7.0},
        ]
        _make_session_and_sets(session, ex, sets_data)
        result = fit_curve(ex.id, session)
        assert result is not None
        # Should be tier2 (single session → not enough for free gamma)
        assert result.fit_tier == "tier2"
        assert result.gamma == DEFAULT_GAMMA
