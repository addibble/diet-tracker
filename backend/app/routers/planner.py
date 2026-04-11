import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.exercise_loads import bodyweight_by_date, latest_bodyweight
from app.models import Exercise, WeightLog
from app.planner import (
    add_exercises_to_plan,
    complete_workout,
    delete_plan,
    get_saved_plan,
    remove_exercises_from_plan,
    reorder_plan_exercises,
    save_plan,
    start_workout,
    suggest_today,
)
from app.strength_model import (
    adjust_prescription,
    fit_curve,
    get_bodyweight_suggestion,
    get_exercise_freshness,
    get_max_recent_entered_weight,
    plan_progressive_sets,
    refit_with_observations,
)

router = APIRouter(prefix="/api/planner", tags=["planner"])


class SavePlanRequest(BaseModel):
    day_label: str
    target_regions: list[str]
    exercises: list[dict]


@router.get("/today")
def get_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return suggest_today(session, as_of=as_of)


@router.post("/save", status_code=201)
def save_today(
    data: SavePlanRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    return save_plan(session, plan_date, data.day_label, data.target_regions, data.exercises)


@router.get("/active")
def get_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return plan


@router.post("/start", status_code=200)
def start_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return start_workout(session, plan["id"])


@router.post("/complete", status_code=200)
def complete_today(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    plan = get_saved_plan(session, plan_date)
    if not plan:
        raise HTTPException(status_code=404, detail="No saved plan for this date")
    return complete_workout(session, plan["id"])


@router.delete("/active", status_code=204)
def delete_active(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        delete_plan(session, plan_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddExercisesRequest(BaseModel):
    exercises: list[dict]


@router.post("/active/exercises", status_code=200)
def add_exercises(
    data: AddExercisesRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return add_exercises_to_plan(session, plan_date, data.exercises)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/active/exercises/{exercise_id}", status_code=200)
def remove_exercise(
    exercise_id: int,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return remove_exercises_from_plan(session, plan_date, [exercise_id])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ReorderRequest(BaseModel):
    pde_ids: list[int]


@router.patch("/active/reorder", status_code=200)
def reorder_exercises(
    data: ReorderRequest,
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    plan_date = as_of or datetime.date.today()
    try:
        return reorder_plan_exercises(session, plan_date, data.pde_ids)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Strength-curve planner endpoints ──


@router.get("/exercise-menu")
def exercise_menu(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Return all exercises ordered by freshness (days since last trained)."""
    return get_exercise_freshness(session)


class PrescribeRequest(BaseModel):
    exercise_id: int
    set_number: int  # 1, 2, or 3
    actual_weight: float | None = None  # user-entered weight override
    prior_sets: list[dict] | None = None  # [{weight, reps, rpe}] from completed sets


@router.post("/prescribe")
def prescribe_set(
    data: PrescribeRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Prescribe weight/reps for a set, with optional in-session refit.

    - set_number=1: uses fitted curve to propose weight
    - set_number=2-3: refits curve incorporating prior_sets, then proposes
    - If actual_weight provided: recalculates target_reps for that weight
    """
    exercise = session.get(Exercise, data.exercise_id)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")

    bw_lookup = _get_bw_lookup(session)
    bw_lb = latest_bodyweight(bw_lookup, datetime.date.today())

    # Fit or refit the curve
    if data.prior_sets:
        fit = refit_with_observations(data.exercise_id, session, data.prior_sets)
    else:
        fit = fit_curve(data.exercise_id, session)

    if fit is None:
        # No curve fit — fall back to last-used weight
        last_weight = get_max_recent_entered_weight(data.exercise_id, session)
        return {
            "has_curve": False,
            "fallback_weight": last_weight,
            "message": "Insufficient RPE data for curve fit. Use recent weight.",
        }

    # If user provided actual weight, adjust prescription
    if data.actual_weight is not None:
        prescription = adjust_prescription(
            fit, exercise, data.actual_weight, bw_lb,
            data.set_number, exercise.allow_heavy_loading,
        )
        return _prescription_response(prescription, fit)

    # Generate full 3-set plan and return the requested set
    max_weight = get_max_recent_entered_weight(data.exercise_id, session)
    prescriptions = plan_progressive_sets(fit, exercise, bw_lb, max_weight)

    if data.set_number < 1 or data.set_number > len(prescriptions):
        raise HTTPException(status_code=400, detail="set_number must be 1-3")

    prescription = prescriptions[data.set_number - 1]
    return _prescription_response(prescription, fit, all_sets=prescriptions)


@router.post("/prescribe-all")
def prescribe_all_sets(
    data: PrescribeRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Prescribe all 3 sets at once for an exercise."""
    exercise = session.get(Exercise, data.exercise_id)
    if exercise is None:
        raise HTTPException(status_code=404, detail="Exercise not found")

    bw_lookup = _get_bw_lookup(session)
    bw_lb = latest_bodyweight(bw_lookup, datetime.date.today())

    if data.prior_sets:
        fit = refit_with_observations(data.exercise_id, session, data.prior_sets)
    else:
        fit = fit_curve(data.exercise_id, session)

    if fit is None:
        last_weight = get_max_recent_entered_weight(data.exercise_id, session)
        return {
            "has_curve": False,
            "fallback_weight": last_weight,
            "message": "Insufficient RPE data for curve fit.",
        }

    # Check for bodyweight exercise
    if (exercise.load_input_mode or "external_weight") in {"bodyweight", "assisted_bodyweight"}:
        suggestion = get_bodyweight_suggestion(data.exercise_id, session)
        return {
            "has_curve": False,
            "is_bodyweight": True,
            "suggestion": suggestion,
        }

    max_weight = get_max_recent_entered_weight(data.exercise_id, session)
    prescriptions = plan_progressive_sets(fit, exercise, bw_lb, max_weight)

    return {
        "has_curve": True,
        "fit_tier": fit.fit_tier,
        "fit_quality": round(fit.identifiability, 2),
        "n_obs": fit.n_obs,
        "sets": [_prescription_dict(p) for p in prescriptions],
    }


def _get_bw_lookup(session: Session) -> dict:
    from sqlmodel import select
    weights = session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all()
    return bodyweight_by_date(weights)


def _prescription_dict(p) -> dict:
    return {
        "set_number": p.set_number,
        "proposed_weight": p.entered_weight,
        "effective_weight": p.effective_weight,
        "target_reps": p.target_reps,
        "target_rpe": p.target_rpe,
        "r_fail": p.r_fail,
        "acceptable_rep_min": p.acceptable_rep_min,
        "acceptable_rep_max": p.acceptable_rep_max,
    }


def _prescription_response(prescription, fit, all_sets=None) -> dict:
    result = {
        "has_curve": True,
        "fit_tier": fit.fit_tier,
        "fit_quality": round(fit.identifiability, 2),
        "n_obs": fit.n_obs,
        "set": _prescription_dict(prescription),
    }
    if all_sets:
        result["all_sets"] = [_prescription_dict(p) for p in all_sets]
    return result
