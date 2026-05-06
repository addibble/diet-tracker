"""Tests for LLM tool pagination envelope (total_count + has_more + offset)."""
import datetime

from app.llm_tools.nutrition import handle_get_foods, handle_get_meal_logs
from app.models import Food, MealLog


def test_get_foods_pagination_envelope(session):
    for i in range(7):
        session.add(Food(
            name=f"PagFood{i:02d}",
            serving_size_grams=100,
            calories_per_serving=100,
            fat_per_serving=1, carbs_per_serving=1, protein_per_serving=1,
        ))
    session.commit()

    res = handle_get_foods({"limit": 3, "offset": 0}, session)
    assert res["count"] == 3
    assert res["total_count"] >= 7
    assert res["has_more"] is True
    assert res["offset"] == 0
    assert res["limit"] == 3

    res2 = handle_get_foods(
        {"limit": 3, "offset": res["total_count"] - 1}, session,
    )
    assert res2["count"] == 1
    assert res2["has_more"] is False


def test_get_foods_clamps_limit_to_max(session):
    res = handle_get_foods({"limit": 9999}, session)
    assert res["limit"] == 200


def test_get_foods_negative_offset_normalized(session):
    res = handle_get_foods({"offset": -5}, session)
    assert res["offset"] == 0


def test_get_meal_logs_pagination(session):
    for i in range(5):
        ml = MealLog(
            date=datetime.date(2026, 5, i + 1),
            meal_type="breakfast",
        )
        session.add(ml)
    session.commit()

    res = handle_get_meal_logs({"limit": 2, "offset": 0}, session)
    assert res["count"] == 2
    assert res["total_count"] >= 5
    assert res["has_more"] is True
