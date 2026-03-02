def test_create_meal_with_food(client):
    food = client.post("/api/foods", json={
        "name": "Chicken Breast", "serving_size_grams": 100,
        "calories_per_serving": 165, "fat_per_serving": 3.6,
        "saturated_fat_per_serving": 1.0, "cholesterol_per_serving": 85,
        "sodium_per_serving": 74, "carbs_per_serving": 0,
        "fiber_per_serving": 0, "protein_per_serving": 31,
    }).json()

    resp = client.post("/api/meals", json={
        "date": "2026-03-01",
        "meal_type": "lunch",
        "items": [{"food_id": food["id"], "amount_grams": 200}],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["meal_type"] == "lunch"
    assert len(data["items"]) == 1
    # 200g of a food with 100g serving → 2x macros
    assert data["total_calories"] == 330.0
    assert data["total_protein"] == 62.0
    assert data["total_cholesterol"] == 170.0


def test_create_meal_with_custom_serving_food(client):
    """A food with a 40g serving eaten at 80g should scale 2x."""
    food = client.post("/api/foods", json={
        "name": "Granola Bar", "serving_size_grams": 40,
        "calories_per_serving": 190, "fat_per_serving": 7,
        "carbs_per_serving": 29, "protein_per_serving": 3,
    }).json()

    resp = client.post("/api/meals", json={
        "date": "2026-03-01",
        "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 80}],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["total_calories"] == 380.0  # 190 * 2
    assert data["total_protein"] == 6.0  # 3 * 2


def test_create_meal_with_recipe(client):
    bread = client.post("/api/foods", json={
        "name": "Bread", "calories_per_serving": 265, "fat_per_serving": 3.2,
        "carbs_per_serving": 49, "protein_per_serving": 9,
    }).json()
    ham = client.post("/api/foods", json={
        "name": "Ham", "calories_per_serving": 145, "fat_per_serving": 6,
        "carbs_per_serving": 1.5, "protein_per_serving": 21,
    }).json()
    recipe = client.post("/api/recipes", json={
        "name": "Ham Sandwich",
        "components": [
            {"food_id": bread["id"], "amount_grams": 60},
            {"food_id": ham["id"], "amount_grams": 50},
        ],
    }).json()

    resp = client.post("/api/meals", json={
        "date": "2026-03-01",
        "meal_type": "lunch",
        "items": [{"recipe_id": recipe["id"], "amount_grams": 110}],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["total_calories"] == 231.5


def test_daily_summary(client):
    food = client.post("/api/foods", json={
        "name": "Rice", "calories_per_serving": 130, "fat_per_serving": 0.3,
        "carbs_per_serving": 28, "protein_per_serving": 2.7,
    }).json()

    client.post("/api/meals", json={
        "date": "2026-03-01", "meal_type": "lunch",
        "items": [{"food_id": food["id"], "amount_grams": 200}],
    })
    client.post("/api/meals", json={
        "date": "2026-03-01", "meal_type": "dinner",
        "items": [{"food_id": food["id"], "amount_grams": 150}],
    })

    resp = client.get("/api/daily/2026-03-01")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["meals"]) == 2
    assert data["total_calories"] == 455.0
    assert "total_fiber" in data


def test_list_meals_by_date(client):
    food = client.post("/api/foods", json={
        "name": "Apple", "calories_per_serving": 52, "fat_per_serving": 0.2,
        "carbs_per_serving": 14, "protein_per_serving": 0.3,
    }).json()
    client.post("/api/meals", json={
        "date": "2026-03-01", "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 150}],
    })
    client.post("/api/meals", json={
        "date": "2026-03-02", "meal_type": "snack",
        "items": [{"food_id": food["id"], "amount_grams": 100}],
    })

    resp = client.get("/api/meals?date=2026-03-01")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
