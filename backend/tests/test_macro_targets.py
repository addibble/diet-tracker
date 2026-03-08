def _target_payload(day: str, calories: float) -> dict:
    return {
        "day": day,
        "calories": calories,
        "fat": 70,
        "saturated_fat": 20,
        "cholesterol": 300,
        "sodium": 2300,
        "carbs": 250,
        "fiber": 30,
        "protein": 150,
    }


def test_macro_target_upsert_updates_existing_day(client):
    created = client.post(
        "/api/macro-targets",
        json=_target_payload("2026-03-01", 2000),
    )
    assert created.status_code == 200
    assert created.json()["calories"] == 2000

    updated = client.post(
        "/api/macro-targets",
        json=_target_payload("2026-03-01", 2200),
    )
    assert updated.status_code == 200
    assert updated.json()["calories"] == 2200

    listed = client.get("/api/macro-targets")
    assert listed.status_code == 200
    targets = listed.json()
    assert len(targets) == 1
    assert targets[0]["day"] == "2026-03-01"
    assert targets[0]["calories"] == 2200


def test_daily_summary_uses_active_target_window(client):
    client.post("/api/macro-targets", json=_target_payload("2026-03-01", 2000))
    client.post("/api/macro-targets", json=_target_payload("2026-03-05", 2400))

    day4 = client.get("/api/daily/2026-03-04")
    assert day4.status_code == 200
    target_day4 = day4.json()["active_macro_target"]
    assert target_day4["day"] == "2026-03-01"
    assert target_day4["next_day"] == "2026-03-05"

    day5 = client.get("/api/daily/2026-03-05")
    assert day5.status_code == 200
    target_day5 = day5.json()["active_macro_target"]
    assert target_day5["day"] == "2026-03-05"
    assert target_day5["next_day"] is None


def test_dashboard_trends_include_day_specific_active_targets(client):
    """days always covers 7 days; each day carries the correct active_macro_target."""
    food = client.post("/api/foods", json={
        "name": "Yogurt", "serving_size_grams": 100,
        "calories_per_serving": 100, "fat_per_serving": 4,
        "carbs_per_serving": 10, "protein_per_serving": 8,
    }).json()
    client.post("/api/meals", json={
        "date": "2026-03-04", "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 100}],
    })
    client.post("/api/meals", json={
        "date": "2026-03-06", "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 200}],
    })

    client.post("/api/macro-targets", json=_target_payload("2026-03-01", 2000))
    client.post("/api/macro-targets", json=_target_payload("2026-03-05", 2400))

    resp = client.get("/api/dashboard/trends?end_date=2026-03-07")
    assert resp.status_code == 200
    data = resp.json()
    # days is always 7 days (end_date - 6 through end_date)
    assert data["start_date"] == "2026-03-01"
    assert data["end_date"] == "2026-03-07"
    assert len(data["days"]) == 7
    day_map = {day["date"]: day for day in data["days"]}

    # Per-day active targets are correct regardless of window size
    assert day_map["2026-03-04"]["active_macro_target"]["day"] == "2026-03-01"
    assert day_map["2026-03-04"]["active_macro_target"]["calories"] == 2000
    assert day_map["2026-03-05"]["active_macro_target"]["day"] == "2026-03-05"
    assert day_map["2026-03-06"]["active_macro_target"]["day"] == "2026-03-05"
    assert day_map["2026-03-06"]["active_macro_target"]["calories"] == 2400
