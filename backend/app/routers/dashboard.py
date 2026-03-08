from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.models import MacroTarget, WeightLog
from app.routers.daily import build_daily_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


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


def _weight_regression(days: list[dict]) -> dict | None:
    points = [
        (index, float(day["weight_lb"]))
        for index, day in enumerate(days)
        if day["weight_lb"] is not None
    ]
    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
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
            "date": day["date"],
            "weight_lb": round(intercept + (slope * index), 2),
        }
        for index, day in enumerate(days)
    ]
    return {
        "points_used": count,
        "slope_lb_per_day": round(slope, 3),
        "slope_lb_per_week": round(slope * 7, 2),
        "start_weight_lb": regression_line[0]["weight_lb"],
        "end_weight_lb": regression_line[-1]["weight_lb"],
        "line": regression_line,
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
    resolved_end_date = end_date or date.today()
    start_date = _macro_target_window_start(resolved_end_date, session)
    total_days = (resolved_end_date - start_date).days + 1
    weights_by_day = _latest_weights_by_day(session, start_date, resolved_end_date)

    days: list[dict] = []
    for offset in range(total_days):
        day = start_date + timedelta(days=offset)
        summary = build_daily_summary(day, session)
        macro_calories, macro_percentages = _macro_calorie_breakdown(summary)
        weight_log = weights_by_day.get(day)
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

    latest_weight = next(
        (day for day in reversed(days) if day["weight_lb"] is not None),
        None,
    )
    return {
        "start_date": str(start_date),
        "end_date": str(resolved_end_date),
        "latest_weight_lb": latest_weight["weight_lb"] if latest_weight else None,
        "latest_weight_logged_at": (
            latest_weight["weight_logged_at"] if latest_weight else None
        ),
        "days": days,
        "weight_regression": _weight_regression(days),
    }
