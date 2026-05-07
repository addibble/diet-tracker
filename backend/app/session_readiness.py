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

Display label & clamping
------------------------
The fitted β can range up to ±1.5, but the v4 paper's empirical observations
show real σ_β ≈ 0.12 with extremes around [-0.4, +0.2]. We therefore clamp the
β value used for the categorical readiness *label* (and the banner color) to
``[BETA_LABEL_CLAMP_LOW, BETA_LABEL_CLAMP_HIGH]`` so a noisy 2-set fit cannot
flip the banner to "strong" / "fatigued" prematurely. The raw β is still
displayed numerically with a "clamped" indicator when it falls outside this
band, so the underlying signal remains visible to the user.

Prescription path
-----------------
β is **not** applied to ``solve_weight`` in v4.0: the per-exercise curve refit
(via ``refit_with_observations`` weighting today's sets at 0.70) already
absorbs the day's signal into M / k / γ. Multiplying again by exp(β) would
double-count. β is purely a display signal in this revision.
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

# Hard bounds on the raw β fit. Real σ_β observed ≈ 0.12; this wide range
# is just a numerical safety net for the optimizer.
BETA_MIN = -1.5
BETA_MAX = 1.5

# Tighter band used for the readiness *label* and color tier. Anything outside
# this range is considered "outside empirical support" and gets capped for
# the categorical banner; the raw β number is still shown to the user.
BETA_LABEL_CLAMP_LOW = -0.35
BETA_LABEL_CLAMP_HIGH = 0.35

# Minimum number of usable RPE sets in a session before β is meaningful.
MIN_SETS_FOR_BETA = 2


def _clamp_for_label(beta: float) -> float:
    return max(BETA_LABEL_CLAMP_LOW, min(BETA_LABEL_CLAMP_HIGH, beta))


def is_beta_clamped(beta: float | None) -> bool:
    """True when the raw β falls outside the label-clamp band."""
    if beta is None:
        return False
    return beta < BETA_LABEL_CLAMP_LOW or beta > BETA_LABEL_CLAMP_HIGH


def readiness_label(beta: float | None) -> str | None:
    """Categorical label for the readiness banner.

    Uses the clamped β so a single-shot 2-set fit at the optimizer's hard
    bound cannot bias the banner.
    """
    if beta is None:
        return None
    b = _clamp_for_label(beta)
    if b >= 0.10:
        return "strong"
    if b >= 0.04:
        return "above_baseline"
    if b > -0.04:
        return "baseline"
    if b > -0.10:
        return "below_baseline"
    return "fatigued"


def readiness_pct(beta: float | None) -> float | None:
    """β translated to "percent reps vs baseline" for UI display.

    Reports the raw (unclamped) β so the user sees the real signal.
    +10% means 10% more reps than the fresh curve predicts on this day.
    """
    if beta is None:
        return None
    return float((math.exp(beta) - 1.0) * 100.0)


def _rpe_confidence(rpe: float) -> float:
    rir = _rpe_to_rir(rpe)
    return max(0.2, math.exp(-0.25 * rir))


def _collect_session_observations(
    session: SQLSession,
    workout_session: WorkoutSession,
    *,
    exercise_id: int | None = None,
) -> list[tuple[int, float, float, float]]:
    """Return [(exercise_id, effective_weight, reps_to_failure, weight)] for
    RPE-eligible rep-mode sets in this WorkoutSession.

    When ``exercise_id`` is provided, only sets for that exercise are
    considered (used by the per-exercise β trend).
    """
    bw_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
    stmt = (
        select(WorkoutSet)
        .where(WorkoutSet.session_id == workout_session.id)
        .order_by(WorkoutSet.set_order)
    )
    if exercise_id is not None:
        stmt = stmt.where(WorkoutSet.exercise_id == exercise_id)
    sets = session.exec(stmt).all()
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
    session: SQLSession,
    workout_session_id: int,
    *,
    exercise_id: int | None = None,
) -> float | None:
    """Compute β for a single workout session using current per-exercise curves.

    When ``exercise_id`` is provided, β is fit using only that exercise's
    RPE-eligible sets (used by the per-exercise readiness sparkline).
    Returns None if there are fewer than ``MIN_SETS_FOR_BETA`` usable sets.
    """
    workout_session = session.get(WorkoutSession, workout_session_id)
    if workout_session is None:
        return None

    obs = _collect_session_observations(
        session, workout_session, exercise_id=exercise_id,
    )
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
