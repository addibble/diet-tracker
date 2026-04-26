"""Tests for the unified endurance_value column + dual-write helper."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import _migrate_legacy_metric_modes
from app.models import Exercise
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



