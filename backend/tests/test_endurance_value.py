"""Tests for the unified endurance_value column + dual-write helper."""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import _backfill_endurance_value, _migrate_legacy_metric_modes
from app.models import Exercise, WorkoutSession, WorkoutSet
from app.units import endurance_value_from_legacy


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    return eng


class TestEnduranceValueFromLegacy:
    def test_reps_mode_prefers_reps(self):
        ex = Exercise(name="x", set_metric_mode="reps")
        assert endurance_value_from_legacy(
            ex, reps=12, duration_secs=None, distance_steps=None
        ) == 12.0

    def test_duration_mode_prefers_duration_secs(self):
        ex = Exercise(name="x", set_metric_mode="duration")
        assert endurance_value_from_legacy(
            ex, reps=1, duration_secs=45, distance_steps=None
        ) == 45.0

    def test_duration_mode_falls_back_to_reps(self):
        """Weighted Plank logged seconds in the reps column before migration."""
        ex = Exercise(name="x", set_metric_mode="duration")
        assert endurance_value_from_legacy(
            ex, reps=35, duration_secs=None, distance_steps=None
        ) == 35.0

    def test_distance_mode_prefers_distance_steps(self):
        ex = Exercise(name="x", set_metric_mode="distance")
        assert endurance_value_from_legacy(
            ex, reps=12, duration_secs=None, distance_steps=24
        ) == 24.0

    def test_distance_mode_falls_back_to_reps(self):
        ex = Exercise(name="x", set_metric_mode="distance")
        assert endurance_value_from_legacy(
            ex, reps=20, duration_secs=None, distance_steps=None
        ) == 20.0

    def test_none_when_all_missing(self):
        ex = Exercise(name="x", set_metric_mode="reps")
        assert endurance_value_from_legacy(
            ex, reps=None, duration_secs=None, distance_steps=None
        ) is None


class TestMigrateLegacyMetricModes:
    def test_hybrid_becomes_reps(self, engine):
        with Session(engine) as s:
            s.add(Exercise(name="Tempo Bench", set_metric_mode="hybrid"))
            s.commit()
        _migrate_legacy_metric_modes(engine)
        with Session(engine) as s:
            ex = s.exec(select(Exercise).where(Exercise.name == "Tempo Bench")).one()
            assert ex.set_metric_mode == "reps"

    def test_carry_becomes_external_weight(self, engine):
        with Session(engine) as s:
            s.add(Exercise(
                name="Farmers Carry", load_input_mode="carry",
                external_load_multiplier=2.0, set_metric_mode="distance",
            ))
            s.commit()
        _migrate_legacy_metric_modes(engine)
        with Session(engine) as s:
            ex = s.exec(
                select(Exercise).where(Exercise.name == "Farmers Carry")
            ).one()
            assert ex.load_input_mode == "external_weight"
            # multiplier preserved — the math is identical
            assert ex.external_load_multiplier == 2.0
            assert ex.set_metric_mode == "distance"


class TestBackfillEnduranceValue:
    def test_reps_mode_backfills_from_reps(self, engine):
        with Session(engine) as s:
            s.add(Exercise(id=1, name="Bench", set_metric_mode="reps"))
            s.add(WorkoutSession(id=1, date=date(2026, 1, 1)))
            s.add(WorkoutSet(session_id=1, exercise_id=1, set_order=1, reps=10))
            s.commit()
        _backfill_endurance_value(engine)
        with Session(engine) as s:
            ws = s.exec(select(WorkoutSet)).one()
            assert ws.endurance_value == 10.0

    def test_duration_mode_prefers_duration_secs(self, engine):
        with Session(engine) as s:
            s.add(Exercise(id=1, name="Plank", set_metric_mode="duration"))
            s.add(WorkoutSession(id=1, date=date(2026, 1, 1)))
            s.add(WorkoutSet(
                session_id=1, exercise_id=1, set_order=1, duration_secs=60, reps=1,
            ))
            s.commit()
        _backfill_endurance_value(engine)
        with Session(engine) as s:
            ws = s.exec(select(WorkoutSet)).one()
            assert ws.endurance_value == 60.0

    def test_duration_mode_falls_back_to_reps(self, engine):
        """Legacy Weighted Plank rows logged seconds in reps; still migrate."""
        with Session(engine) as s:
            s.add(Exercise(id=1, name="Plank", set_metric_mode="duration"))
            s.add(WorkoutSession(id=1, date=date(2026, 1, 1)))
            s.add(WorkoutSet(session_id=1, exercise_id=1, set_order=1, reps=35))
            s.commit()
        _backfill_endurance_value(engine)
        with Session(engine) as s:
            ws = s.exec(select(WorkoutSet)).one()
            assert ws.endurance_value == 35.0

    def test_distance_mode_prefers_distance_steps(self, engine):
        with Session(engine) as s:
            s.add(Exercise(id=1, name="Carry", set_metric_mode="distance"))
            s.add(WorkoutSession(id=1, date=date(2026, 1, 1)))
            s.add(WorkoutSet(
                session_id=1, exercise_id=1, set_order=1,
                reps=12, distance_steps=24,
            ))
            s.commit()
        _backfill_endurance_value(engine)
        with Session(engine) as s:
            ws = s.exec(select(WorkoutSet)).one()
            assert ws.endurance_value == 24.0

    def test_idempotent_skips_non_null(self, engine):
        """Running the backfill twice must not overwrite app-written values."""
        with Session(engine) as s:
            s.add(Exercise(id=1, name="Bench", set_metric_mode="reps"))
            s.add(WorkoutSession(id=1, date=date(2026, 1, 1)))
            s.add(WorkoutSet(
                session_id=1, exercise_id=1, set_order=1,
                reps=10, endurance_value=999.0,  # app wrote an explicit value
            ))
            s.commit()
        _backfill_endurance_value(engine)
        with Session(engine) as s:
            ws = s.exec(select(WorkoutSet)).one()
            assert ws.endurance_value == 999.0
