"""Tests for bootstrap mode (new-athlete per-exercise calibration)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.strength_model import (
    BOOT_GAMMA,
    BOOT_K,
    MIN_DISTINCT_WEIGHTS_TIER1,
    MIN_SETS_TIER2,
    _invert_universal_curve,
    _prescribe_universal_curve,
    bootstrap_prescription,
    is_bootstrap,
    prescribe_next_set,
)


def _make_exercise(session: Session, **kwargs) -> Exercise:
    defaults = {
        "name": f"BootTest {id(kwargs)}",
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


def _make_session_sets(
    session: Session, exercise: Exercise, sets_data: list[dict],
    session_date: date,
) -> WorkoutSession:
    ws = WorkoutSession(date=session_date)
    session.add(ws)
    session.flush()
    for i, sd in enumerate(sets_data):
        session.add(WorkoutSet(
            session_id=ws.id, exercise_id=exercise.id,
            set_order=i + 1, reps=sd["reps"], weight=sd["weight"], rpe=sd["rpe"],
        ))
    session.flush()
    return ws


# ── Universal curve math ────────────────────────────────────────


class TestUniversalCurveRoundTrip:
    def test_invert_then_prescribe_recovers_weight(self):
        """If we invert (W, rtf) to M and then ask for W at the same target
        rtf, we should land back near W. This is the fix for the rubber-duck
        "model mismatch" finding — Brzycki in / universal curve out biases
        the prescription, so we use the same curve both directions."""
        w = 100.0
        for rtf in [8.0, 12.0, 18.0, 23.0]:
            m = _invert_universal_curve(rtf, w)
            w_back = _prescribe_universal_curve(m, rtf)
            assert abs(w_back - w) < 0.01, f"rtf={rtf}: {w_back} vs {w}"

    def test_invert_handles_zero_rtf(self):
        assert _invert_universal_curve(0, 100) > 0

    def test_invert_caps_extreme_rtf(self):
        """Very-high rtf shouldn't blow up (Brzycki would give absurd 1RMs)."""
        m_cap = _invert_universal_curve(50.0, 100.0)
        # Capped at rtf=30 internally → bounded output
        assert m_cap < 100.0 * 10


# ── is_bootstrap detection ──────────────────────────────────────


class TestIsBootstrap:
    def test_no_exercise_is_false(self, session):
        assert is_bootstrap(99999, session) is False

    def test_cold_start_returns_true(self, session):
        ex = _make_exercise(session)
        assert is_bootstrap(ex.id, session) is True

    def test_bodyweight_returns_false(self, session):
        ex = _make_exercise(session, load_input_mode="bodyweight")
        assert is_bootstrap(ex.id, session) is False

    def test_below_threshold_is_bootstrap(self, session):
        """Only 2 RPE sets → still bootstrap (< MIN_SETS_TIER2=3)."""
        ex = _make_exercise(session)
        _make_session_sets(session, ex, [
            {"reps": 10, "weight": 100, "rpe": 8.0},
            {"reps": 8, "weight": 110, "rpe": 8.5},
        ], session_date=date.today())
        assert is_bootstrap(ex.id, session) is True
        assert MIN_SETS_TIER2 == 3

    def test_single_weight_is_bootstrap(self, session):
        """3 RPE sets but only 1 distinct weight → still bootstrap."""
        ex = _make_exercise(session)
        _make_session_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 7.5},
            {"reps": 10, "weight": 100, "rpe": 8.5},
            {"reps": 8, "weight": 100, "rpe": 9.0},
        ], session_date=date.today())
        assert is_bootstrap(ex.id, session) is True
        assert MIN_DISTINCT_WEIGHTS_TIER1 == 2

    def test_meets_threshold_exits_bootstrap(self, session):
        ex = _make_exercise(session)
        _make_session_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 7.5},
            {"reps": 10, "weight": 110, "rpe": 8.5},
            {"reps": 8, "weight": 120, "rpe": 9.0},
        ], session_date=date.today() - timedelta(days=3))
        assert is_bootstrap(ex.id, session) is False

    def test_low_rpe_sets_dont_count(self, session):
        """RPE < MIN_RPE_FOR_FIT don't count toward exit threshold."""
        ex = _make_exercise(session)
        _make_session_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 6.0},  # too low
            {"reps": 10, "weight": 110, "rpe": 6.5},  # too low
            {"reps": 8, "weight": 120, "rpe": 6.0},   # too low
        ], session_date=date.today() - timedelta(days=3))
        assert is_bootstrap(ex.id, session) is True


# ── bootstrap_prescription ──────────────────────────────────────


class TestBootstrapAnchor:
    def test_set1_returns_no_weight(self, session):
        ex = _make_exercise(session)
        result = bootstrap_prescription(ex.id, session, prior_sets=[], bodyweight_lb=180)
        assert result["mode"] == "bootstrap"
        assert result["next_set"]["proposed_weight"] is None
        assert result["next_set"]["set_number"] == 1
        assert "prompt" in result["bootstrap"]
        assert result["bootstrap"]["stage"] == 0

    def test_heavy_vs_light_different_targets(self, session):
        heavy = _make_exercise(session, allow_heavy_loading=True)
        light = _make_exercise(session, allow_heavy_loading=False)
        h = bootstrap_prescription(heavy.id, session, [], 180)
        light_r = bootstrap_prescription(light.id, session, [], 180)
        # Light should target more reps than heavy on anchor set.
        assert light_r["next_set"]["target_reps"] > h["next_set"]["target_reps"]


class TestBootstrapProbing:
    def test_set2_uses_set1_observation(self, session):
        ex = _make_exercise(session)
        prior = [{"weight": 100, "reps": 15, "rpe": 7.0, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, bodyweight_lb=180)
        ns = result["next_set"]
        assert ns["proposed_weight"] is not None
        assert ns["set_number"] == 2
        # With a nominal-target set 1 (15 reps, RPE 7 on a heavy exercise),
        # set 2 should be meaningfully heavier than set 1 (we're probing
        # higher intensity, not running it back).
        assert ns["proposed_weight"] > 100

    def test_set2_after_severe_overshoot_steps_down_more(self, session):
        """Athlete guessed too heavy: hit failure at only 4 reps (target 15).
        The severe-overshoot clamp should allow a drop below 80% of W_1."""
        ex = _make_exercise(session)
        prior = [{"weight": 100, "reps": 4, "rpe": 10.0, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, bodyweight_lb=180)
        ns = result["next_set"]
        assert ns["proposed_weight"] < 100
        # Severity flag in response metadata
        assert result["bootstrap"]["severe_over"] is True

    def test_clamp_prevents_absurd_jump(self, session):
        """Set 1 landed way below target (low RPE, tons of reps): the normal
        clamp should cap upward correction at 1.55x (severe-under) to avoid
        wild jumps."""
        ex = _make_exercise(session)
        prior = [{"weight": 50, "reps": 30, "rpe": 6.0, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, bodyweight_lb=180)
        ns = result["next_set"]
        # Must not exceed 1.55 × prior (the severe-undershoot ceiling).
        assert ns["proposed_weight"] <= 50 * 1.55 + 0.01

    def test_set2_rounds_away_from_set1(self, session):
        """Set 2's rounded weight must differ from set 1 — same rounded
        weight would give a stacked dot with no new information."""
        ex = _make_exercise(session)
        # Use an anchor that would plausibly round back to the same weight
        prior = [{"weight": 100, "reps": 15, "rpe": 7.0, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, 180)
        assert abs(result["next_set"]["proposed_weight"] - 100) >= 2.5

    def test_set3_uses_both_observations(self, session):
        ex = _make_exercise(session)
        prior = [
            {"weight": 100, "reps": 15, "rpe": 7.0, "set_order": 1},
            {"weight": 115, "reps": 10, "rpe": 8.0, "set_order": 2},
        ]
        result = bootstrap_prescription(ex.id, session, prior, 180)
        assert result["next_set"]["set_number"] == 3
        # At stage 3 the M estimate should be built from both observations.
        assert result["bootstrap"]["M_samples"] == 2


class TestBootstrapBailouts:
    def test_missing_rpe_returns_anchor_behavior(self, session):
        """Prior set without RPE is ignored → fall back to anchor prescription."""
        ex = _make_exercise(session)
        prior = [{"weight": 100, "reps": 15, "rpe": None, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, 180)
        # No usable prior → still on anchor (no proposed weight).
        assert result["next_set"]["proposed_weight"] is None

    def test_zero_reps_ignored(self, session):
        ex = _make_exercise(session)
        prior = [{"weight": 100, "reps": 0, "rpe": 10.0, "set_order": 1}]
        result = bootstrap_prescription(ex.id, session, prior, 180)
        assert result["next_set"]["proposed_weight"] is None


# ── Integration with prescribe_next_set ─────────────────────────


class TestPrescribeNextSetBootstrap:
    def test_routes_to_bootstrap_when_cold(self, session):
        ex = _make_exercise(session)
        result = prescribe_next_set(
            exercise_id=ex.id, session=session,
            prior_sets=[], bodyweight_lb=180,
        )
        assert result.get("mode") == "bootstrap"
        assert result["has_curve"] is False

    def test_exits_bootstrap_when_data_sufficient(self, session):
        ex = _make_exercise(session)
        today = date.today()
        # Two sessions across 2 weights, 3 sets total → meets threshold
        _make_session_sets(session, ex, [
            {"reps": 12, "weight": 100, "rpe": 7.5},
            {"reps": 9, "weight": 110, "rpe": 8.5},
            {"reps": 7, "weight": 120, "rpe": 9.0},
        ], session_date=today - timedelta(days=3))
        _make_session_sets(session, ex, [
            {"reps": 10, "weight": 105, "rpe": 8.0},
        ], session_date=today - timedelta(days=10))
        result = prescribe_next_set(
            exercise_id=ex.id, session=session,
            prior_sets=[], bodyweight_lb=180,
        )
        # No bootstrap banner → curve-based path
        assert result.get("mode") != "bootstrap"

    def test_caps_at_3_bootstrap_sets(self, session):
        ex = _make_exercise(session)
        prior = [
            {"weight": 100, "reps": 15, "rpe": 7.0, "set_order": 1},
            {"weight": 115, "reps": 10, "rpe": 8.0, "set_order": 2},
            {"weight": 125, "reps": 6, "rpe": 9.0, "set_order": 3},
        ]
        result = prescribe_next_set(
            exercise_id=ex.id, session=session,
            prior_sets=prior, bodyweight_lb=180,
        )
        assert result["exercise_complete"] is True
        assert result["next_set"] is None


def test_bootstrap_constants_are_sane():
    assert BOOT_K > 0 and BOOT_GAMMA > 0 and BOOT_GAMMA <= 1.0
