"""Tests for the EnduranceMetric strategy + curve fitting on non-rep modes."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Exercise, WorkoutSession, WorkoutSet
from app.strength_model import (
    _curve_dict,
    fit_curve,
    get_bodyweight_suggestion,
    prescribe_next_set,
)
from app.units import EnduranceMetric, metric_for


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    return eng


def _make_exercise(session: Session, **kwargs) -> Exercise:
    defaults = dict(
        name="Test", load_input_mode="external_weight", set_metric_mode="reps",
        external_load_multiplier=1.0, bodyweight_fraction=0.0,
        allow_heavy_loading=True, laterality="bilateral",
    )
    defaults.update(kwargs)
    ex = Exercise(**defaults)
    session.add(ex)
    session.commit()
    session.refresh(ex)
    return ex


def _add_session_with_sets(
    session: Session, exercise_id: int, on_date: date, sets: list[dict]
) -> WorkoutSession:
    ws = WorkoutSession(date=on_date)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    for i, s in enumerate(sets, start=1):
        session.add(WorkoutSet(
            session_id=ws.id, exercise_id=exercise_id, set_order=i,
            weight=s["weight"], reps=s.get("reps"),
            endurance_value=s.get("endurance_value", s.get("reps")),
            rpe=s["rpe"],
        ))
    session.commit()
    return ws


class TestMetricFor:
    def test_reps_default(self):
        ex = Exercise(name="x", set_metric_mode="reps")
        m = metric_for(ex)
        assert m.kind == "reps" and m.display_unit == "reps" and m.int_valued

    def test_duration(self):
        ex = Exercise(name="x", set_metric_mode="duration")
        m = metric_for(ex)
        assert m.kind == "duration" and m.display_unit == "s"

    def test_distance(self):
        ex = Exercise(name="x", set_metric_mode="distance")
        m = metric_for(ex)
        assert m.kind == "distance" and m.display_unit == "steps"

    def test_unknown_falls_back_to_reps(self):
        ex = Exercise(name="x", set_metric_mode="garbage")
        assert metric_for(ex).kind == "reps"

    def test_label(self):
        assert EnduranceMetric("reps", "reps", True).label == "reps"
        assert EnduranceMetric("duration", "s", True).label == "seconds"
        assert EnduranceMetric("distance", "steps", True).label == "steps"


class TestDistanceCurve:
    """Farmers Carry-style: external weight, distance metric, varied loads."""

    def test_fits_with_endurance_value(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(s, name="Farmers Carry", set_metric_mode="distance")
            today = date(2026, 4, 20)
            # Build a varied-load history: heavier weight → fewer steps
            for offset, (w, steps, rpe) in enumerate([
                (40, 30, 8.0), (45, 25, 8.5), (50, 20, 9.0),
                (35, 35, 7.5), (40, 28, 8.0), (45, 22, 8.5),
                (50, 18, 9.0), (40, 32, 7.5), (45, 24, 8.5),
            ]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": w, "endurance_value": steps, "rpe": rpe}],
                )

            fit = fit_curve(ex.id, s, as_of=today)
            assert fit is not None
            assert fit.M > 0 and fit.k > 0

            # Curve dict carries the metric annotation
            cd = _curve_dict(fit, ex, bodyweight_lb=180.0)
            assert cd["metric_kind"] == "distance"
            assert cd["display_unit"] == "steps"


class TestDurationCurve:
    """Weighted Plank-style: external weight, duration metric."""

    def test_fits_with_duration_endurance(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(s, name="Weighted Plank", set_metric_mode="duration")
            today = date(2026, 4, 20)
            for offset, (w, secs, rpe) in enumerate([
                (10, 60, 8.0), (15, 45, 8.5), (20, 30, 9.0),
                (10, 55, 8.0), (15, 40, 8.5), (20, 25, 9.0),
                (10, 65, 7.5), (15, 50, 8.0), (20, 35, 8.5),
            ]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": w, "endurance_value": secs, "rpe": rpe}],
                )

            fit = fit_curve(ex.id, s, as_of=today)
            assert fit is not None
            cd = _curve_dict(fit, ex, bodyweight_lb=180.0)
            assert cd["metric_kind"] == "duration"
            assert cd["display_unit"] == "s"


class TestBodyweightSuggestion:
    """Pure bodyweight = permanent tier 3, median quantity in native unit."""

    def test_reps_mode(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(
                s, name="Pull-Up", load_input_mode="bodyweight",
                set_metric_mode="reps",
            )
            today = date(2026, 4, 20)
            for offset, reps in enumerate([10, 12, 11, 9, 10]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": 0, "reps": reps, "endurance_value": reps, "rpe": 8.0}],
                )
            sug = get_bodyweight_suggestion(ex.id, s)
            assert sug["metric_kind"] == "reps"
            assert sug["reps_per_set"] == 10
            assert sug["endurance_per_set"] == 10
            assert "rep" in sug["notes"].lower()

    def test_duration_mode(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(
                s, name="Plank", load_input_mode="bodyweight",
                set_metric_mode="duration",
            )
            today = date(2026, 4, 20)
            for offset, secs in enumerate([45, 60, 50, 55, 45]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": 0, "endurance_value": secs, "rpe": 8.0}],
                )
            sug = get_bodyweight_suggestion(ex.id, s)
            assert sug["metric_kind"] == "duration"
            assert sug["display_unit"] == "s"
            assert sug["endurance_per_set"] == 50  # median
            assert "second" in sug["notes"].lower()

    def test_distance_mode(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(
                s, name="Walking Lunges BW", load_input_mode="bodyweight",
                set_metric_mode="distance",
            )
            today = date(2026, 4, 20)
            for offset, steps in enumerate([20, 24, 22, 18, 20]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": 0, "endurance_value": steps, "rpe": 8.0}],
                )
            sug = get_bodyweight_suggestion(ex.id, s)
            assert sug["metric_kind"] == "distance"
            assert sug["endurance_per_set"] == 20

    def test_no_history_returns_default(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(
                s, name="Plank", load_input_mode="bodyweight",
                set_metric_mode="duration",
            )
            sug = get_bodyweight_suggestion(ex.id, s)
            assert sug["metric_kind"] == "duration"
            assert sug["endurance_per_set"] == 30  # default for duration


class TestPrescribeNextSetMetricKind:
    def test_bodyweight_prescription_emits_metric_kind(self, engine):
        with Session(engine) as s:
            ex = _make_exercise(
                s, name="Plank", load_input_mode="bodyweight",
                set_metric_mode="duration",
            )
            today = date(2026, 4, 20)
            for offset, secs in enumerate([45, 60, 50]):
                _add_session_with_sets(
                    s, ex.id, today - timedelta(days=offset),
                    [{"weight": 0, "endurance_value": secs, "rpe": 8.0}],
                )
            result = prescribe_next_set(ex.id, s, prior_sets=[], bodyweight_lb=180.0)
            assert result["metric_kind"] == "duration"
            assert result["display_unit"] == "s"
            assert result["next_set"]["target_endurance"] > 0
