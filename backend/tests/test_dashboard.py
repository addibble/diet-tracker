from datetime import UTC, datetime

from app.models import WeightLog


def test_dashboard_trends(client, session):
    food = client.post("/api/foods", json={
        "name": "Trail Mix", "serving_size_grams": 100,
        "calories_per_serving": 200, "fat_per_serving": 10,
        "carbs_per_serving": 20, "protein_per_serving": 10,
    }).json()

    client.post("/api/meals", json={
        "date": "2026-03-06",
        "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 50}],
    })
    client.post("/api/meals", json={
        "date": "2026-03-07",
        "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 100}],
    })

    session.add(WeightLog(
        weight_lb=180.0,
        logged_at=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
    ))
    session.add(WeightLog(
        weight_lb=181.0,
        logged_at=datetime(2026, 3, 4, 8, 0, tzinfo=UTC),
    ))
    session.add(WeightLog(
        weight_lb=179.0,
        logged_at=datetime(2026, 3, 4, 18, 0, tzinfo=UTC),
    ))
    session.add(WeightLog(
        weight_lb=178.0,
        logged_at=datetime(2026, 3, 7, 9, 0, tzinfo=UTC),
    ))
    session.commit()

    resp = client.get("/api/dashboard/trends?end_date=2026-03-07")

    assert resp.status_code == 200
    data = resp.json()
    assert data["start_date"] == "2026-03-01"
    assert data["end_date"] == "2026-03-07"
    assert data["latest_weight_lb"] == 178.0
    assert len(data["days"]) == 7

    day_map = {day["date"]: day for day in data["days"]}
    assert day_map["2026-03-04"]["weight_lb"] == 179.0
    assert day_map["2026-03-05"]["weight_lb"] is None
    assert day_map["2026-03-06"]["total_calories"] == 100.0
    assert day_map["2026-03-07"]["total_calories"] == 200.0
    assert day_map["2026-03-07"]["macro_calories"] == {
        "fat": 90.0,
        "carbs": 80.0,
        "protein": 40.0,
    }
    assert day_map["2026-03-07"]["macro_calorie_percentages"] == {
        "fat": 42.9,
        "carbs": 38.1,
        "protein": 19.0,
    }

    regression = data["weight_regression"]
    assert regression["points_used"] == 3
    assert regression["slope_lb_per_day"] == -0.333
    assert regression["slope_lb_per_week"] == -2.33
    assert regression["start_weight_lb"] == 180.0
    assert regression["end_weight_lb"] == 178.0
