"""Per-session readiness β (v4 strength model).

Models day-to-day variation in athletic performance as a single multiplicative
log-link factor on the fresh-curve rep prediction:

    r_obs ≈ exp(β_j) · r_fresh(W; M_e, k_e, γ_e, δ_e)

β_j > 0 → "killing it" day (more reps than the curve predicts).
β_j < 0 → recovery / poor sleep day (fewer reps than the curve predicts).

Live in-session re-fit
----------------------
Each time a set is logged inside a workout session, ``fit_session_beta`` does
a 1D scalar minimization over that session's RPE-eligible sets:

    β_j  =  argmin_β  Σ_i w_i · (r_i − exp(β) · r_fresh_i)² + λ · β²

The per-exercise curves are taken from ``fit_curve(..., as_of=session.date,
exclude_today=True)`` so they reflect strictly-prior history and do not absorb
the day's performance themselves. The result is persisted to
``WorkoutSession.readiness_beta`` and surfaced to the frontend banner.

Prescription path
-----------------
``solve_weight`` accepts an optional ``readiness_beta``; callers that fetch
the active session's β can pass it through so the recommended weight scales
with the day's readiness (a strong day prescribes heavier load, a weak day
lighter load).
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from scipy.optimize import minimize_scalar
from sqlmodel import Session as SQLSession
from sqlmodel import select

from app.exercise_loads import (
    bodyweight_by_date,
    effective_weight,
    supports_strength_estimate,
)
from app.models import Exercise, WeightLog, WorkoutSession, WorkoutSet
from app.strength_model import (
    MIN_RPE_FOR_FIT,
    fit_curve,
    fresh_curve,
)
from app.units import reps_done_to_rtf as _reps_done_to_rtf
from app.units import rpe_to_rir as _rpe_to_rir

# L2 regularization on β (per session). Empirically tuned in
# tools/model_exploration/session_beta_fit.py — see the v4 paper.
BETA_REG_LAMBDA = 4.0

# Hard bounds on β. Implies a multiplicative range of [exp(-1.5), exp(1.5)] ≈
# [0.22x, 4.5x] reps relative to the fresh curve. Real σ_β observed ≈ 0.12.
BETA_MIN = -1.5
BETA_MAX = 1.5

# Minimum number of usable RPE sets in a session before β is meaningful.
MIN_SETS_FOR_BETA = 2


def readiness_label(beta: float | None) -> str | None:
    """Categorical label for the readiness banner."""
    if beta is None:
        return None
    if beta >= 0.10:
        return "strong"
    if beta >= 0.04:
        return "above_baseline"
    if beta > -0.04:
        return "baseline"
    if beta > -0.10:
        return "below_baseline"
    return "fatigued"


def readiness_pct(beta: float | None) -> float | None:
    """β translated to "percent reps vs baseline" for UI display.

    +10% means 10% more reps than the fresh curve predicts on this day.
    """
    if beta is None:
        return None
    return float((math.exp(beta) - 1.0) * 100.0)


def _rpe_confidence(rpe: float) -> float:
    rir = _rpe_to_rir(rpe)
    return max(0.2, math.exp(-0.25 * rir))


def _collect_session_observations(
    session: SQLSession, workout_session: WorkoutSession,
) -> list[tuple[int, float, float, float]]:
    """Return [(exercise_id, effective_weight, reps_to_failure, weight)] for
    RPE-eligible rep-mode sets in this WorkoutSession.
    """
    bw_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
    sets = session.exec(
        select(WorkoutSet)
        .where(WorkoutSet.session_id == workout_session.id)
        .order_by(WorkoutSet.set_order)
    ).all()
    out: list[tuple[int, float, float, float]] = []
    for ws in sets:
        if ws.rpe is None or ws.rpe < MIN_RPE_FOR_FIT or ws.rpe > 10.0:
            continue
        if ws.endurance_value is None or ws.endurance_value <= 0:
            continue
        exercise = session.get(Exercise, ws.exercise_id)
        if exercise is None:
            continue
        if not supports_strength_estimate(exercise, ws):
            continue
        ew = effective_weight(exercise, ws, bw_lookup, workout_session.date)
        if ew <= 0:
            continue
        rir = _rpe_to_rir(ws.rpe)
        rtf = _reps_done_to_rtf(float(ws.endurance_value), rir)
        out.append((exercise.id, float(ew), float(rtf), _rpe_confidence(ws.rpe)))
    return out


def _solve_beta(
    r_obs: Iterable[float],
    r_pred: Iterable[float],
    w: Iterable[float],
    lam: float = BETA_REG_LAMBDA,
) -> float:
    """Minimize Σ w_i (r_i - exp(β) p_i)^2 + λ β² over β ∈ [BETA_MIN, BETA_MAX]."""
    rs = [float(x) for x in r_obs]
    ps = [float(x) for x in r_pred]
    ws = [float(x) for x in w]
    pairs = [(r, p, wi) for r, p, wi in zip(rs, ps, ws) if p > 0.05 and r > 0.0]
    if len(pairs) < MIN_SETS_FOR_BETA:
        return 0.0

    def loss(b: float) -> float:
        e = math.exp(b)
        s = 0.0
        for r, p, wi in pairs:
            d = r - e * p
            s += wi * d * d
        return s + lam * b * b

    res = minimize_scalar(loss, bounds=(BETA_MIN, BETA_MAX), method="bounded")
    return float(res.x)


def fit_session_beta(
    session: SQLSession, workout_session_id: int,
) -> float | None:
    """Compute β for a single workout session using current per-exercise curves.

    Returns None if the session has fewer than ``MIN_SETS_FOR_BETA`` usable
    RPE sets across all exercises.
    """
    workout_session = session.get(WorkoutSession, workout_session_id)
    if workout_session is None:
        return None

    obs = _collect_session_observations(session, workout_session)
    if len(obs) < MIN_SETS_FOR_BETA:
        return None

    # For each unique exercise, fit a curve excluding the current session
    # (so the curve reflects strictly-prior history, not today's performance).
    curves: dict[int, tuple[float, float, float, float] | None] = {}
    for ex_id in {ex_id for ex_id, _, _, _ in obs}:
        cf = fit_curve(
            ex_id, session, days=30,
            allow_heavy=True,
            exclude_today=True,
            as_of=workout_session.date,
        )
        curves[ex_id] = (cf.M, cf.k, cf.gamma, cf.delta) if cf is not None else None

    r_obs: list[float] = []
    r_pred: list[float] = []
    weights: list[float] = []
    for ex_id, ew, rtf, conf in obs:
        params = curves.get(ex_id)
        if params is None:
            continue
        M, k, gamma, delta = params  # noqa: N806 (M matches the math symbol)
        pred = float(fresh_curve(ew, M, k, gamma, delta))
        if pred <= 0:
            continue
        r_obs.append(rtf)
        r_pred.append(pred)
        weights.append(conf)

    if len(r_obs) < MIN_SETS_FOR_BETA:
        return None

    return _solve_beta(r_obs, r_pred, weights)


def update_session_readiness(
    session: SQLSession, workout_session_id: int, *, commit: bool = True,
) -> float | None:
    """Re-fit β for the given session and persist it to ``readiness_beta``.

    Returns the new β, or None if the session has insufficient data (in which
    case the stored value is also cleared to avoid stale display).
    """
    beta = fit_session_beta(session, workout_session_id)
    workout_session = session.get(WorkoutSession, workout_session_id)
    if workout_session is None:
        return None
    workout_session.readiness_beta = beta
    session.add(workout_session)
    if commit:
        session.commit()
        session.refresh(workout_session)
    return beta
