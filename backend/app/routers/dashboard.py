from datetime import UTC, date, datetime, time, timedelta
from math import sqrt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.config import user_today
from app.database import get_session
from app.macros import compute_food_macros
from app.models import (
    Food,
    MacroTarget,
    MealItem,
    MealLog,
    Recipe,
    RecipeComponent,
    WeightLog,
)
from app.routers.daily import build_daily_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class WeightInput(BaseModel):
    weight_lb: float


def _macro_calorie_breakdown(summary: dict) -> tuple[dict[str, float], dict[str, float]]:
    fat_calories = round(float(summary.get("total_fat", 0)) * 9, 1)
    carbs_calories = round(float(summary.get("total_carbs", 0)) * 4, 1)
    protein_calories = round(float(summary.get("total_protein", 0)) * 4, 1)
    macro_calories = {
        "fat": fat_calories,
        "carbs": carbs_calories,
        "protein": protein_calories,
    }
    total_macro_calories = sum(macro_calories.values())
    if total_macro_calories <= 0:
        return macro_calories, {key: 0.0 for key in macro_calories}
    return macro_calories, {
        key: round((value / total_macro_calories) * 100, 1)
        for key, value in macro_calories.items()
    }


def _latest_weights_by_day(
    session: Session,
    start_day: date,
    end_day: date,
) -> dict[date, WeightLog]:
    start_dt = datetime.combine(start_day, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=UTC)
    logs = session.exec(
        select(WeightLog)
        .where(WeightLog.logged_at >= start_dt)
        .where(WeightLog.logged_at < end_dt)
        .order_by(WeightLog.logged_at)
    ).all()

    latest_by_day: dict[date, WeightLog] = {}
    for log in logs:
        if log.logged_at.tzinfo is not None:
            log_day = log.logged_at.astimezone(UTC).date()
        else:
            log_day = log.logged_at.date()
        latest_by_day[log_day] = log
    return latest_by_day


def _weight_regression(weight_days: list[dict]) -> dict | None:
    """Compute linear regression over days that have weight data.

    Uses actual date offsets from the first entry so gaps between readings
    are correctly reflected in the slope.
    """
    if not weight_days:
        return None

    first_date = date.fromisoformat(weight_days[0]["date"])
    points = [
        ((date.fromisoformat(d["date"]) - first_date).days, float(d["weight_lb"]))
        for d in weight_days
    ]

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    count = len(points)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = 0.0
    if denominator > 0:
        slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    intercept = mean_y - (slope * mean_x)

    regression_line = [
        {
            "date": d["date"],
            "weight_lb": round(
                intercept + slope * (date.fromisoformat(d["date"]) - first_date).days,
                2,
            ),
        }
        for d in weight_days
    ]
    return {
        "points_used": count,
        "slope_lb_per_day": round(slope, 3),
        "slope_lb_per_week": round(slope * 7, 2),
        "start_weight_lb": regression_line[0]["weight_lb"],
        "end_weight_lb": regression_line[-1]["weight_lb"],
        "line": regression_line,
    }


def _daily_calories_bulk(
    session: Session,
    start_day: date,
    end_day: date,
) -> dict[date, float]:
    """Compute total calories per day over a date range in bulk."""
    meals = session.exec(
        select(MealLog)
        .where(MealLog.date >= start_day)
        .where(MealLog.date <= end_day)
    ).all()
    if not meals:
        return {}

    meal_ids = [m.id for m in meals]
    items = session.exec(
        select(MealItem).where(MealItem.meal_log_id.in_(meal_ids))  # type: ignore[union-attr]
    ).all()

    # Batch-load all referenced foods and recipes
    food_ids = {i.food_id for i in items if i.food_id}
    recipe_ids = {i.recipe_id for i in items if i.recipe_id}
    foods = {f.id: f for f in session.exec(select(Food).where(Food.id.in_(food_ids))).all()} if food_ids else {}
    recipes = {}
    if recipe_ids:
        for rid in recipe_ids:
            recipe = session.get(Recipe, rid)
            if not recipe:
                continue
            comps = session.exec(
                select(RecipeComponent).where(RecipeComponent.recipe_id == rid)
            ).all()
            total_g = sum(c.amount_grams for c in comps)
            total_cal = 0.0
            for c in comps:
                f = foods.get(c.food_id) or session.get(Food, c.food_id)
                if f:
                    if f.id not in foods:
                        foods[f.id] = f
                    total_cal += compute_food_macros(f, c.amount_grams)["calories"]
            recipes[rid] = (total_g, total_cal)

    meal_id_to_day = {m.id: m.date for m in meals}
    cal_by_day: dict[date, float] = {}
    for item in items:
        day = meal_id_to_day[item.meal_log_id]
        cal = 0.0
        if item.food_id and item.food_id in foods:
            cal = compute_food_macros(foods[item.food_id], item.amount_grams)["calories"]
        elif item.recipe_id and item.recipe_id in recipes:
            total_g, total_cal = recipes[item.recipe_id]
            if total_g > 0:
                cal = total_cal * (item.amount_grams / total_g)
        cal_by_day[day] = cal_by_day.get(day, 0.0) + cal
    return cal_by_day


def _calorie_stats(
    daily_cals: list[float],
) -> dict | None:
    """Compute mean and standard deviation of daily calories."""
    if not daily_cals:
        return None
    n = len(daily_cals)
    avg = sum(daily_cals) / n
    variance = sum((c - avg) ** 2 for c in daily_cals) / n
    return {
        "avg_calories_per_day": round(avg),
        "std_calories_per_day": round(sqrt(variance)),
        "days_counted": n,
    }


def _macro_target_window_start(end_day: date, session: Session) -> date:
    target = session.exec(
        select(MacroTarget)
        .where(MacroTarget.day <= end_day)
        .order_by(MacroTarget.day.desc())
    ).first()
    if target:
        return target.day
    return end_day - timedelta(days=6)


@router.get("/trends")
def dashboard_trends(
    end_date: date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    resolved_end_date = end_date or user_today()

    # 7-day window drives the daily macros breakdown and target-normalized trends
    seven_day_start = resolved_end_date - timedelta(days=6)
    weights_7day = _latest_weights_by_day(session, seven_day_start, resolved_end_date)

    days: list[dict] = []
    for offset in range(7):
        day = seven_day_start + timedelta(days=offset)
        summary = build_daily_summary(day, session)
        macro_calories, macro_percentages = _macro_calorie_breakdown(summary)
        weight_log = weights_7day.get(day)
        days.append(
            {
                "date": str(day),
                "total_calories": float(summary["total_calories"]),
                "total_fat": float(summary["total_fat"]),
                "total_saturated_fat": float(summary["total_saturated_fat"]),
                "total_cholesterol": float(summary["total_cholesterol"]),
                "total_sodium": float(summary["total_sodium"]),
                "total_carbs": float(summary["total_carbs"]),
                "total_fiber": float(summary["total_fiber"]),
                "total_protein": float(summary["total_protein"]),
                "macro_calories": macro_calories,
                "macro_calorie_percentages": macro_percentages,
                "active_macro_target": summary.get("active_macro_target"),
                "weight_lb": round(weight_log.weight_lb, 2) if weight_log else None,
                "weight_logged_at": (
                    weight_log.logged_at.isoformat() if weight_log else None
                ),
            }
        )

    # Weight regression window: from macro target start, only days with data
    weight_start = _macro_target_window_start(resolved_end_date, session)
    all_weights = _latest_weights_by_day(session, weight_start, resolved_end_date)
    weight_days = [
        {
            "date": str(day_date),
            "weight_lb": round(log.weight_lb, 2),
            "weight_logged_at": log.logged_at.isoformat(),
        }
        for day_date, log in sorted(all_weights.items())
    ]

    latest_weight = weight_days[-1] if weight_days else None

    # Calorie stats over the regression window, excluding today (partial day)
    # Only count days that have at least one meal logged (untracked ≠ 0 cal)
    yesterday = resolved_end_date - timedelta(days=1)
    cal_by_day = _daily_calories_bulk(session, weight_start, yesterday)
    logged_dates = set(
        session.exec(
            select(MealLog.date)
            .where(MealLog.date >= weight_start)
            .where(MealLog.date <= yesterday)
            .distinct()
        ).all()
    )
    daily_cals = [cal_by_day.get(d, 0.0) for d in sorted(logged_dates)]
    calorie_stats = _calorie_stats(daily_cals)

    weight_reg = _weight_regression(weight_days)

    # TDEE estimate: intake minus estimated surplus/deficit from weight change
    # 3500 kcal ≈ 1 lb body weight
    tdee_estimate = None
    if weight_reg and calorie_stats:
        surplus_per_day = weight_reg["slope_lb_per_day"] * 3500
        tdee_estimate = round(calorie_stats["avg_calories_per_day"] - surplus_per_day)

    return {
        "start_date": str(seven_day_start),
        "end_date": str(resolved_end_date),
        "latest_weight_lb": latest_weight["weight_lb"] if latest_weight else None,
        "latest_weight_logged_at": (
            latest_weight["weight_logged_at"] if latest_weight else None
        ),
        "days": days,
        "weight_days": weight_days,
        "weight_regression": weight_reg,
        "calorie_stats": calorie_stats,
        "tdee_estimate": tdee_estimate,
    }


@router.put("/weight")
def put_today_weight(
    body: WeightInput,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Create or update today's weight log entry."""
    today = user_today()
    day_start = datetime.combine(today, time.min, tzinfo=UTC)
    day_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)

    existing = session.exec(
        select(WeightLog)
        .where(WeightLog.logged_at >= day_start)
        .where(WeightLog.logged_at < day_end)
        .order_by(WeightLog.logged_at.desc())
    ).first()

    now = datetime.now(UTC)
    if existing:
        existing.weight_lb = body.weight_lb
        existing.logged_at = now
        session.add(existing)
    else:
        existing = WeightLog(weight_lb=body.weight_lb, logged_at=now)
        session.add(existing)

    session.commit()
    session.refresh(existing)
    return {
        "id": existing.id,
        "weight_lb": round(existing.weight_lb, 2),
        "logged_at": existing.logged_at.isoformat(),
    }
