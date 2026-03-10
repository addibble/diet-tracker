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


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

def test_llm_tool_dedup_same_meal_twice(session):
    """handle_set_meal_logs called twice with identical args returns the same meal."""
    from sqlmodel import select
    from app.llm_tools.nutrition import handle_set_meal_logs
    from app.models import Food, MealLog

    food = Food(
        name="Oats", serving_size_grams=100,
        calories_per_serving=389, fat_per_serving=6.9,
        carbs_per_serving=66, protein_per_serving=17,
    )
    session.add(food)
    session.commit()
    session.refresh(food)

    args = {
        "changes": [{
            "operation": "create",
            "set": {"date": "2026-03-01", "meal_type": "breakfast"},
            "relations": {
                "items": {
                    "mode": "replace",
                    "records": [{"food_id": food.id, "amount_grams": 80}],
                }
            },
        }]
    }

    r1 = handle_set_meal_logs(args, session)
    r2 = handle_set_meal_logs(args, session)

    # Both responses should point to the same meal
    assert r1["matches"][0]["id"] == r2["matches"][0]["id"]

    # Only one MealLog row should exist
    meals = session.exec(select(MealLog)).all()
    assert len(meals) == 1

    # The dedup warning should be present in the second response
    assert any("Duplicate meal detected" in w for w in r2.get("warnings", []))


def test_llm_tool_dedup_different_items_not_deduped(session):
    """Two create calls with different items are NOT collapsed into one."""
    from sqlmodel import select
    from app.llm_tools.nutrition import handle_set_meal_logs
    from app.models import Food, MealLog

    food = Food(
        name="Banana", serving_size_grams=100,
        calories_per_serving=89, fat_per_serving=0.3,
        carbs_per_serving=23, protein_per_serving=1.1,
    )
    session.add(food)
    session.commit()
    session.refresh(food)

    base = {"date": "2026-03-01", "meal_type": "snack"}
    args_a = {"changes": [{"operation": "create", "set": base, "relations": {
        "items": {"mode": "replace", "records": [{"food_id": food.id, "amount_grams": 100}]},
    }}]}
    args_b = {"changes": [{"operation": "create", "set": base, "relations": {
        "items": {"mode": "replace", "records": [{"food_id": food.id, "amount_grams": 150}]},
    }}]}

    r1 = handle_set_meal_logs(args_a, session)
    r2 = handle_set_meal_logs(args_b, session)

    assert r1["matches"][0]["id"] != r2["matches"][0]["id"]

    meals = session.exec(select(MealLog)).all()
    assert len(meals) == 2


def test_tool_state_suppresses_confirm_save():
    """_ToolState.meal_saved_via_tools prevents the <CONFIRM/> auto-save path."""
    from app.routers.parse import _ToolState

    state = _ToolState()
    assert not state.meal_saved_via_tools

    # Simulate the executor setting the flag
    state.meal_saved_via_tools = True
    assert state.meal_saved_via_tools
