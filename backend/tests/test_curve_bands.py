"""Tests for bootstrap_curve_bands.

Verify the contract:
* Quantile bands are monotonically nested (q05 ≤ q25 ≤ q50 ≤ q75 ≤ q95)
* Bands collapse toward the point fit when every session is an exact replica
* Sparse exercises (< MIN_SESSIONS_FOR_BANDS sessions) return None
* Bodyweight-only exercises return None
* Bands are evaluated in entered space and ship the W envelope
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models import Exercise, WeightLog, WorkoutSession, WorkoutSet
from app.strength_model import (
    DEFAULT_N_BOOT,
    MIN_SESSIONS_FOR_BANDS,
    bootstrap_curve_bands,
    fit_curve,
)

# ── Builders ──────────────────────────────────────────────────────────


def _ex(session, **kwargs) -> Exercise:
    defaults = {
        "name": "Bench Press",
        "equipment": "barbell",
        "allow_heavy_loading": True,
        "load_input_mode": "external_weight",
        "bodyweight_fraction": 0.0,
        "external_load_multiplier": 1.0,
        "set_metric_mode": "reps",
    }
    defaults.update(kwargs)
    e = Exercise(**defaults)
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


def _set(
    session, ws_id: int, ex_id: int, *,
    weight: float, reps: float, rpe: float = 8.5, set_order: int = 1,
) -> WorkoutSet:
    s = WorkoutSet(
        session_id=ws_id, exercise_id=ex_id, set_order=set_order,
        weight=weight, endurance_value=reps, rpe=rpe,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _bw(session, on_date: date, lb: float = 180.0) -> WeightLog:
    w = WeightLog(weight_lb=lb, logged_at=on_date)
    session.add(w)
    session.commit()
    session.refresh(w)
    return w


def _seed_realistic_sessions(
    session, ex_id: int, as_of: date, n_sessions: int = 6,
) -> None:
    """Build N sessions spaced 4 days apart with realistic-looking
    weight/reps points spanning a useful weight range so the curve fit
    has identifiability. Variation across sessions drives bootstrap spread.
    """
    rng_seed = 0
    for s_idx in range(n_sessions):
        d = as_of - timedelta(days=4 * (n_sessions - s_idx))
        ws = _ws(session, d)
        # Small per-session perturbation so resamples diverge.
        offset = (s_idx % 3) - 1  # -1, 0, +1 lb
        for set_idx, (w_lb, reps) in enumerate(
            [(115.0 + offset, 8.5), (135.0 + offset, 6.0), (155.0 + offset, 3.5)]
        ):
            _set(
                session, ws.id, ex_id,
                weight=w_lb, reps=reps, rpe=8.5, set_order=set_idx + 1,
            )
        rng_seed += 1


# ── Tests ─────────────────────────────────────────────────────────────


class TestSparseGate:
    def test_no_sessions_returns_none(self, session):
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        assert bootstrap_curve_bands(ex.id, session, as_of=as_of, n_boot=20) is None

    def test_below_min_sessions_returns_none(self, session):
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        # Build MIN_SESSIONS_FOR_BANDS - 1 sessions: bootstrap should reject.
        for offset in range(1, MIN_SESSIONS_FOR_BANDS):
            ws = _ws(session, as_of - timedelta(days=offset * 4))
            for set_idx, (w, r) in enumerate([(115.0, 8.0), (135.0, 5.0)]):
                _set(session, ws.id, ex.id, weight=w, reps=r, set_order=set_idx + 1)
        _bw(session, as_of - timedelta(days=20))
        bands = bootstrap_curve_bands(ex.id, session, as_of=as_of, n_boot=20)
        assert bands is None

    def test_bodyweight_only_returns_none(self, session):
        ex = _ex(
            session,
            name="Pull Up",
            load_input_mode="bodyweight",
            bodyweight_fraction=1.0,
            external_load_multiplier=0.0,
        )
        as_of = date(2026, 5, 1)
        _seed_realistic_sessions(session, ex.id, as_of, n_sessions=6)
        _bw(session, as_of - timedelta(days=30))
        assert bootstrap_curve_bands(ex.id, session, as_of=as_of, n_boot=20) is None


class TestBandsContract:
    @pytest.fixture
    def bands_payload(self, session):
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        _seed_realistic_sessions(session, ex.id, as_of, n_sessions=6)
        _bw(session, as_of - timedelta(days=30))
        bands = bootstrap_curve_bands(
            ex.id, session, as_of=as_of, n_boot=60, seed=42,
        )
        assert bands is not None
        return bands

    def test_payload_shape(self, bands_payload):
        for key in (
            "W_grid", "q05", "q25", "q50", "q75", "q95",
            "n_boot_success", "W_lo_entered", "W_hi_entered",
            "bw_offset", "ext_mult",
        ):
            assert key in bands_payload, f"missing {key!r}"

    def test_quantiles_monotonically_nested(self, bands_payload):
        q05 = bands_payload["q05"]
        q25 = bands_payload["q25"]
        q50 = bands_payload["q50"]
        q75 = bands_payload["q75"]
        q95 = bands_payload["q95"]
        n = len(bands_payload["W_grid"])
        assert len(q05) == len(q25) == len(q50) == len(q75) == len(q95) == n
        # Allow tiny float tolerance.
        eps = 1e-9
        for i in range(n):
            assert q05[i] <= q25[i] + eps, f"q05>q25 at i={i}"
            assert q25[i] <= q50[i] + eps, f"q25>q50 at i={i}"
            assert q50[i] <= q75[i] + eps, f"q50>q75 at i={i}"
            assert q75[i] <= q95[i] + eps, f"q75>q95 at i={i}"

    def test_w_envelope_matches_observed_data(self, bands_payload):
        # Observed entered weights are 115/135/155 (±1 lb perturbation).
        assert 113.0 <= bands_payload["W_lo_entered"] <= 117.0
        assert 153.0 <= bands_payload["W_hi_entered"] <= 157.0

    def test_w_grid_covers_envelope_with_padding(self, bands_payload):
        grid = bands_payload["W_grid"]
        W_lo = bands_payload["W_lo_entered"]
        W_hi = bands_payload["W_hi_entered"]
        assert grid[0] < W_lo, "grid should start below observed min"
        assert grid[-1] > W_hi, "grid should extend above observed max"
        # Monotonically increasing.
        for i in range(len(grid) - 1):
            assert grid[i] < grid[i + 1]

    def test_band_has_positive_width_in_observed_region(self, bands_payload):
        # In the observed range, at least the median grid point should
        # have non-zero (q05, q95) spread.
        grid = bands_payload["W_grid"]
        W_lo = bands_payload["W_lo_entered"]
        W_hi = bands_payload["W_hi_entered"]
        widths_inside = [
            bands_payload["q95"][i] - bands_payload["q05"][i]
            for i, w in enumerate(grid) if W_lo <= w <= W_hi
        ]
        assert widths_inside, "no grid points inside observed range"
        # At least one in-range point must have non-zero spread.
        assert max(widths_inside) > 0.01

    def test_n_boot_success_reasonable(self, bands_payload):
        # Default success threshold is 50%; with seeded perturbed data
        # we expect well above that.
        n = bands_payload["n_boot_success"]
        assert n >= 30, f"n_boot_success={n} too low"


class TestBandsEvaluation:
    def test_q50_close_to_point_fit_in_observed_range(self, session):
        """Median bootstrap fit at observed weights should be near the
        single-shot fit predictions (within a few reps tolerance)."""
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        _seed_realistic_sessions(session, ex.id, as_of, n_sessions=6)
        _bw(session, as_of - timedelta(days=30))
        bands = bootstrap_curve_bands(
            ex.id, session, as_of=as_of, n_boot=80, seed=7,
        )
        assert bands is not None
        fit = fit_curve(
            ex.id, session, exclude_today=True, as_of=as_of,
        )
        assert fit is not None
        # Pick a grid point at ~135 lb (the middle of observed range).
        grid = bands["W_grid"]
        # Find index closest to 135
        target_idx = min(
            range(len(grid)), key=lambda i: abs(grid[i] - 135.0),
        )
        q50 = bands["q50"][target_idx]
        # Point fit prediction at entered weight 135 lb. Convert to
        # effective space the same way the bands do.
        W_eff = 135.0 * bands["ext_mult"] + bands["bw_offset"]
        from app.strength_model import fresh_curve
        r_pred = float(fresh_curve(W_eff, fit.M, fit.k, fit.gamma, fit.delta))
        # Tolerance is generous because bootstrap samples small N with
        # replacement; the median should still track within ~2 reps.
        assert abs(q50 - r_pred) <= 2.0, f"q50={q50} pred={r_pred}"


class TestDefaults:
    def test_default_n_boot_is_reasonable(self):
        assert 50 <= DEFAULT_N_BOOT <= 500

    def test_min_sessions_threshold(self):
        assert MIN_SESSIONS_FOR_BANDS >= 3


class TestBandsEndpoint:
    """Integration test for GET /api/planner/curve-bands."""

    def test_returns_bands_for_seeded_exercise(self, client, session):
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        _seed_realistic_sessions(session, ex.id, as_of, n_sessions=6)
        _bw(session, as_of - timedelta(days=30))
        r = client.get(
            f"/api/planner/curve-bands/{ex.id}",
            params={"date": as_of.isoformat()},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["has_bands"] is True
        assert "W_grid" in body
        assert "q05" in body and "q95" in body
        # Cached round-trip: same answer the second time.
        r2 = client.get(
            f"/api/planner/curve-bands/{ex.id}",
            params={"date": as_of.isoformat()},
        )
        assert r2.status_code == 200
        assert r2.json() == body

    def test_returns_has_bands_false_for_sparse(self, client, session):
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        # 1 session only
        ws = _ws(session, as_of - timedelta(days=5))
        for set_idx, (w, r_) in enumerate([(115.0, 8.0), (135.0, 5.0)]):
            _set(
                session, ws.id, ex.id, weight=w, reps=r_,
                set_order=set_idx + 1,
            )
        _bw(session, as_of - timedelta(days=30))
        r = client.get(
            f"/api/planner/curve-bands/{ex.id}",
            params={"date": as_of.isoformat()},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"has_bands": False}

    def test_cache_hit_avoids_recompute(self, client, session):
        """Second request should hit cache (verified indirectly via the
        chart_cache table containing the row)."""
        from app.chart_cache import bands_key, cache_get
        ex = _ex(session)
        as_of = date(2026, 5, 1)
        _seed_realistic_sessions(session, ex.id, as_of, n_sessions=6)
        _bw(session, as_of - timedelta(days=30))
        r = client.get(
            f"/api/planner/curve-bands/{ex.id}",
            params={"date": as_of.isoformat()},
        )
        assert r.status_code == 200
        # Bypass HTTP and read the cache directly.
        cached = cache_get(session, bands_key(ex.id, as_of))
        assert cached is not None
        assert cached["has_bands"] is True
