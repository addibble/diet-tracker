"""Tests for chart_cache: get/set roundtrip + invalidation rules."""

from __future__ import annotations

from datetime import date, timedelta

from app.chart_cache import (
    KIND_BANDS,
    KIND_BETA_EVOL,
    KIND_CURVE,
    KIND_FATIGUE,
    bands_key,
    beta_evol_key,
    cache_get,
    cache_set,
    curve_key,
    fatigue_key,
    invalidate_all_charts,
    invalidate_for_session_delete,
    invalidate_for_set_change,
)
from app.models import ChartCache, Exercise, WorkoutSession, WorkoutSet


def _ex(session, name: str = "Bench Press") -> Exercise:
    e = Exercise(
        name=name,
        equipment="barbell",
        allow_heavy_loading=True,
        load_input_mode="external_weight",
        bodyweight_fraction=0.0,
        external_load_multiplier=1.0,
        set_metric_mode="reps",
    )
    session.add(e)
    session.commit()
    session.refresh(e)
    return e


def _ws(session, on_date: date) -> WorkoutSession:
    ws = WorkoutSession(date=on_date)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _set(session, ws_id: int, ex_id: int, set_order: int = 1) -> WorkoutSet:
    s = WorkoutSet(
        session_id=ws_id,
        exercise_id=ex_id,
        set_order=set_order,
        weight=180,
        endurance_value=8.0,
        rpe=8.0,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


class TestCacheRoundtrip:
    def test_get_miss_returns_none(self, session):
        assert cache_get(session, "missing-key") is None

    def test_set_then_get(self, session):
        cache_set(
            session,
            "foo",
            KIND_CURVE,
            {"hello": "world", "n": 42},
            exercise_id=1,
            on_date=date(2026, 1, 15),
        )
        got = cache_get(session, "foo")
        assert got == {"hello": "world", "n": 42}

    def test_set_overwrites_existing(self, session):
        cache_set(session, "foo", KIND_CURVE, {"v": 1})
        cache_set(session, "foo", KIND_CURVE, {"v": 2})
        assert cache_get(session, "foo") == {"v": 2}

    def test_set_handles_dates_in_payload(self, session):
        cache_set(session, "k", KIND_CURVE, {"d0": date(2026, 1, 1)})
        # date serializes to ISO via json.dumps default=str
        got = cache_get(session, "k")
        assert got == {"d0": "2026-01-01"}

    def test_corrupt_payload_drops_and_returns_none(self, session):
        row = ChartCache(
            cache_key="bad",
            kind=KIND_CURVE,
            payload_json="{not valid",
        )
        session.add(row)
        session.commit()
        assert cache_get(session, "bad") is None
        # And the row was cleaned up.
        from sqlmodel import select

        leftover = session.exec(select(ChartCache).where(ChartCache.cache_key == "bad")).first()
        assert leftover is None


class TestInvalidateForSetChange:
    def test_drops_curves_in_30_day_forward_window(self, session):
        ex = _ex(session)
        d0 = date(2026, 1, 15)
        # Cache snapshots at d0, d0+10, d0+30 (in window) and d0+31, d0-1 (out).
        for offset in (0, 10, 30):
            cache_set(
                session,
                curve_key(ex.id, d0 + timedelta(days=offset)),
                KIND_CURVE,
                {"x": offset},
                exercise_id=ex.id,
                on_date=d0 + timedelta(days=offset),
            )
        for offset in (31, -1, -10):
            cache_set(
                session,
                curve_key(ex.id, d0 + timedelta(days=offset)),
                KIND_CURVE,
                {"x": offset},
                exercise_id=ex.id,
                on_date=d0 + timedelta(days=offset),
            )
        ws = _ws(session, d0)
        invalidate_for_set_change(session, ws.id, ex.id, d0)
        session.commit()
        # In-window dropped:
        for offset in (0, 10, 30):
            assert cache_get(session, curve_key(ex.id, d0 + timedelta(days=offset))) is None
        # Out-of-window kept:
        for offset in (31, -1, -10):
            assert cache_get(session, curve_key(ex.id, d0 + timedelta(days=offset))) is not None

    def test_does_not_drop_other_exercises(self, session):
        ex1 = _ex(session, name="Bench")
        ex2 = _ex(session, name="Squat")
        d0 = date(2026, 1, 15)
        cache_set(
            session,
            curve_key(ex1.id, d0),
            KIND_CURVE,
            {"e": 1},
            exercise_id=ex1.id,
            on_date=d0,
        )
        cache_set(
            session,
            curve_key(ex2.id, d0),
            KIND_CURVE,
            {"e": 2},
            exercise_id=ex2.id,
            on_date=d0,
        )
        ws = _ws(session, d0)
        invalidate_for_set_change(session, ws.id, ex1.id, d0)
        session.commit()
        assert cache_get(session, curve_key(ex1.id, d0)) is None
        assert cache_get(session, curve_key(ex2.id, d0)) == {"e": 2}

    def test_drops_betaevol_for_session(self, session):
        ex = _ex(session)
        d0 = date(2026, 1, 15)
        ws = _ws(session, d0)
        cache_set(
            session,
            beta_evol_key(ws.id),
            KIND_BETA_EVOL,
            {"points": []},
            workout_session_id=ws.id,
        )
        invalidate_for_set_change(session, ws.id, ex.id, d0)
        session.commit()
        assert cache_get(session, beta_evol_key(ws.id)) is None

    def test_drops_betaevol_for_later_session_with_same_exercise(self, session):
        ex = _ex(session)
        d0 = date(2026, 1, 15)
        # Mutated session is on d0; later session is 10 days later (inside
        # the 30-day curve-fitting window) and contains the exercise.
        ws_now = _ws(session, d0)
        ws_later = _ws(session, d0 + timedelta(days=10))
        ws_too_late = _ws(session, d0 + timedelta(days=31))
        ws_other_ex = _ws(session, d0 + timedelta(days=5))
        # Only ws_later and ws_too_late get a set on this exercise.
        _set(session, ws_later.id, ex.id)
        _set(session, ws_too_late.id, ex.id)
        # ws_other_ex has a different exercise.
        ex2 = _ex(session, name="Squat")
        _set(session, ws_other_ex.id, ex2.id)
        for w in (ws_now, ws_later, ws_too_late, ws_other_ex):
            cache_set(
                session,
                beta_evol_key(w.id),
                KIND_BETA_EVOL,
                {"sid": w.id},
                workout_session_id=w.id,
            )
        invalidate_for_set_change(session, ws_now.id, ex.id, d0)
        session.commit()
        # In-window same-exercise sessions dropped.
        assert cache_get(session, beta_evol_key(ws_now.id)) is None
        assert cache_get(session, beta_evol_key(ws_later.id)) is None
        # Out-of-window kept.
        assert cache_get(session, beta_evol_key(ws_too_late.id)) is not None
        # Different-exercise session kept.
        assert cache_get(session, beta_evol_key(ws_other_ex.id)) is not None

    def test_drops_fatigue_for_exercise_only(self, session):
        ex1 = _ex(session, name="Bench")
        ex2 = _ex(session, name="Squat")
        d0 = date(2026, 1, 15)
        # Both auto-keyed and explicit-date-keyed fatigue rows for ex1.
        cache_set(
            session, fatigue_key(ex1.id, 30, None), KIND_FATIGUE,
            {"e": 1}, exercise_id=ex1.id, on_date=None,
        )
        cache_set(
            session, fatigue_key(ex1.id, 30, d0 - timedelta(days=400)),
            KIND_FATIGUE, {"e": 1, "old": True},
            exercise_id=ex1.id, on_date=d0 - timedelta(days=400),
        )
        cache_set(
            session, fatigue_key(ex2.id, 30, None), KIND_FATIGUE,
            {"e": 2}, exercise_id=ex2.id, on_date=None,
        )
        ws = _ws(session, d0)
        invalidate_for_set_change(session, ws.id, ex1.id, d0)
        session.commit()
        # All ex1 fatigue rows dropped (regardless of on_date).
        assert cache_get(session, fatigue_key(ex1.id, 30, None)) is None
        assert cache_get(
            session,
            fatigue_key(ex1.id, 30, d0 - timedelta(days=400)),
        ) is None
        # ex2 fatigue cache preserved.
        assert cache_get(session, fatigue_key(ex2.id, 30, None)) == {"e": 2}


class TestInvalidateForSessionDelete:
    def test_drops_session_betaevol_and_curve_windows(self, session):
        ex1 = _ex(session, name="Bench")
        ex2 = _ex(session, name="Squat")
        d0 = date(2026, 1, 15)
        ws = _ws(session, d0)
        cache_set(
            session,
            beta_evol_key(ws.id),
            KIND_BETA_EVOL,
            {},
            workout_session_id=ws.id,
        )
        cache_set(
            session,
            curve_key(ex1.id, d0),
            KIND_CURVE,
            {"e": 1},
            exercise_id=ex1.id,
            on_date=d0,
        )
        cache_set(
            session,
            curve_key(ex2.id, d0 + timedelta(days=15)),
            KIND_CURVE,
            {"e": 2},
            exercise_id=ex2.id,
            on_date=d0 + timedelta(days=15),
        )
        invalidate_for_session_delete(session, ws.id, d0, [ex1.id, ex2.id])
        session.commit()
        assert cache_get(session, beta_evol_key(ws.id)) is None
        assert cache_get(session, curve_key(ex1.id, d0)) is None
        assert cache_get(session, curve_key(ex2.id, d0 + timedelta(days=15))) is None


class TestInvalidateAll:
    def test_drops_every_row(self, session):
        for k in ("a", "b", "c"):
            cache_set(session, k, KIND_CURVE, {"k": k})
        invalidate_all_charts(session)
        session.commit()
        for k in ("a", "b", "c"):
            assert cache_get(session, k) is None


class TestBandsInvalidation:
    """Bands use ``exclude_today`` semantics: a set on ``D`` does NOT
    invalidate the band anchored at ``D``, but DOES invalidate later
    anchors within the 30-day window because those anchors include
    ``D`` in their fit data."""

    def test_set_change_drops_strictly_later_bands(self, session):
        ex = _ex(session)
        d0 = date(2026, 1, 15)
        # Band anchored at d0 (same-day) — should be preserved.
        # Bands at d0+1, d0+15, d0+30 — should be dropped (in window).
        # Band at d0+31 — preserved (outside fit window).
        # Band at d0-1 — preserved (before the set).
        offsets_dropped = (1, 15, 30)
        offsets_kept = (0, 31, -1)
        for offset in offsets_dropped + offsets_kept:
            anchor = d0 + timedelta(days=offset)
            cache_set(
                session, bands_key(ex.id, anchor), KIND_BANDS,
                {"o": offset}, exercise_id=ex.id, on_date=anchor,
            )
        ws = _ws(session, d0)
        invalidate_for_set_change(session, ws.id, ex.id, d0)
        session.commit()
        for offset in offsets_dropped:
            anchor = d0 + timedelta(days=offset)
            assert cache_get(session, bands_key(ex.id, anchor)) is None, (
                f"band anchored at d0+{offset} should be dropped"
            )
        for offset in offsets_kept:
            anchor = d0 + timedelta(days=offset)
            assert cache_get(session, bands_key(ex.id, anchor)) is not None, (
                f"band anchored at d0+{offset} should be kept"
            )

    def test_set_change_does_not_drop_other_exercise_bands(self, session):
        ex1 = _ex(session, name="Bench")
        ex2 = _ex(session, name="Squat")
        d0 = date(2026, 1, 15)
        d_later = d0 + timedelta(days=10)
        cache_set(
            session, bands_key(ex1.id, d_later), KIND_BANDS,
            {"e": 1}, exercise_id=ex1.id, on_date=d_later,
        )
        cache_set(
            session, bands_key(ex2.id, d_later), KIND_BANDS,
            {"e": 2}, exercise_id=ex2.id, on_date=d_later,
        )
        ws = _ws(session, d0)
        invalidate_for_set_change(session, ws.id, ex1.id, d0)
        session.commit()
        assert cache_get(session, bands_key(ex1.id, d_later)) is None
        assert cache_get(session, bands_key(ex2.id, d_later)) == {"e": 2}

    def test_session_delete_drops_all_bands_for_affected_exercise(self, session):
        ex1 = _ex(session, name="Bench")
        ex2 = _ex(session, name="Squat")
        d0 = date(2026, 1, 15)
        ws = _ws(session, d0)
        # Bands at various anchors for both exercises.
        for anchor in (d0, d0 + timedelta(days=10), d0 + timedelta(days=100)):
            cache_set(
                session, bands_key(ex1.id, anchor), KIND_BANDS,
                {"a": str(anchor)}, exercise_id=ex1.id, on_date=anchor,
            )
            cache_set(
                session, bands_key(ex2.id, anchor), KIND_BANDS,
                {"a": str(anchor)}, exercise_id=ex2.id, on_date=anchor,
            )
        invalidate_for_session_delete(session, ws.id, d0, [ex1.id])
        session.commit()
        # All ex1 bands dropped regardless of anchor (session deletes are
        # destructive; safer to nuke than to do windowed cleanup).
        for anchor in (d0, d0 + timedelta(days=10), d0 + timedelta(days=100)):
            assert cache_get(session, bands_key(ex1.id, anchor)) is None
        # ex2 bands preserved.
        for anchor in (d0, d0 + timedelta(days=10), d0 + timedelta(days=100)):
            assert cache_get(session, bands_key(ex2.id, anchor)) is not None

    def test_invalidate_all_charts_drops_bands(self, session):
        ex = _ex(session)
        d0 = date(2026, 1, 15)
        cache_set(
            session, bands_key(ex.id, d0), KIND_BANDS,
            {"x": 1}, exercise_id=ex.id, on_date=d0,
        )
        invalidate_all_charts(session)
        session.commit()
        assert cache_get(session, bands_key(ex.id, d0)) is None
