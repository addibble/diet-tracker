"""Tests for meal_items router and recipe expansion in chat."""
import pytest
from sqlmodel import Session

from app.models import Food, MealItem, MealLog, Recipe, RecipeComponent


@pytest.fixture()
def yogurt(session: Session) -> Food:
    f = Food(
        name="Greek Yogurt",
        serving_size_grams=100,
        calories_per_serving=97,
        fat_per_serving=5,
        saturated_fat_per_serving=3.3,
        cholesterol_per_serving=10,
        sodium_per_serving=36,
        carbs_per_serving=3.6,
        fiber_per_serving=0,
        protein_per_serving=9,
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


@pytest.fixture()
def granola(session: Session) -> Food:
    f = Food(
        name="Granola",
        serving_size_grams=50,
        calories_per_serving=220,
        fat_per_serving=8,
        saturated_fat_per_serving=1,
        cholesterol_per_serving=0,
        sodium_per_serving=10,
        carbs_per_serving=32,
        fiber_per_serving=3,
        protein_per_serving=5,
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


@pytest.fixture()
def breakfast_recipe(session: Session, yogurt: Food, granola: Food) -> Recipe:
    r = Recipe(name="Yogurt & Granola Breakfast")
    session.add(r)
    session.commit()
    session.refresh(r)
    session.add(RecipeComponent(
        recipe_id=r.id, food_id=yogurt.id, amount_grams=150,
    ))
    session.add(RecipeComponent(
        recipe_id=r.id, food_id=granola.id, amount_grams=50,
    ))
    session.commit()
    return r


@pytest.fixture()
def meal_with_items(
    session: Session, yogurt: Food, granola: Food,
) -> MealLog:
    import datetime

    meal = MealLog(date=datetime.date(2026, 3, 16), meal_type="breakfast")
    session.add(meal)
    session.commit()
    session.refresh(meal)
    session.add(MealItem(
        meal_log_id=meal.id, food_id=yogurt.id, amount_grams=150,
    ))
    session.add(MealItem(
        meal_log_id=meal.id, food_id=granola.id, amount_grams=50,
    ))
    session.commit()
    return meal


# ── PATCH /api/meal-items/{item_id} ──────────────────────────────────


def test_update_meal_item_amount(client, meal_with_items, session):
    # Get the first item
    resp = client.get(f"/api/meals/{meal_with_items.id}")
    item_id = resp.json()["items"][0]["id"]
    original_cals = resp.json()["items"][0]["calories"]

    # Update amount
    resp = client.patch(
        f"/api/meal-items/{item_id}",
        json={"amount_grams": 300},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Should return the full updated meal
    assert data["id"] == meal_with_items.id
    updated_item = next(i for i in data["items"] if i["id"] == item_id)
    assert updated_item["grams"] == 300
    # Calories should have doubled (150g → 300g)
    assert updated_item["calories"] == pytest.approx(original_cals * 2, abs=0.2)


def test_update_meal_item_not_found(client):
    resp = client.patch("/api/meal-items/99999", json={"amount_grams": 100})
    assert resp.status_code == 404


# ── POST /api/meals/{meal_id}/items ───────────────────────────────────


def test_add_meal_item(client, meal_with_items, yogurt):
    resp = client.post(
        f"/api/meals/{meal_with_items.id}/items",
        json={"food_id": yogurt.id, "amount_grams": 50},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["items"]) == 3  # was 2, now 3


def test_add_meal_item_missing_ids(client, meal_with_items):
    resp = client.post(
        f"/api/meals/{meal_with_items.id}/items",
        json={"amount_grams": 50},
    )
    assert resp.status_code == 400


def test_add_meal_item_meal_not_found(client, yogurt):
    resp = client.post(
        "/api/meals/99999/items",
        json={"food_id": yogurt.id, "amount_grams": 50},
    )
    assert resp.status_code == 404


# ── DELETE /api/meal-items/{item_id} ──────────────────────────────────


def test_delete_meal_item(client, meal_with_items):
    resp = client.get(f"/api/meals/{meal_with_items.id}")
    item_id = resp.json()["items"][0]["id"]

    resp = client.delete(f"/api/meal-items/{item_id}")
    assert resp.status_code == 204

    resp = client.get(f"/api/meals/{meal_with_items.id}")
    assert len(resp.json()["items"]) == 1


def test_delete_meal_item_not_found(client):
    resp = client.delete("/api/meal-items/99999")
    assert resp.status_code == 404


# ── Recipe expansion in _resolve_chat_items ───────────────────────────


def test_resolve_chat_items_expands_recipe(
    session, yogurt, granola, breakfast_recipe,
):
    from app.routers.parse import _resolve_chat_items

    raw = [{"recipe_id": breakfast_recipe.id, "amount_grams": 200}]
    result = _resolve_chat_items(raw, session)

    # Recipe with 2 components should produce 2 food items
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert "Greek Yogurt" in names
    assert "Granola" in names

    # Each item should have food_id, group, and source_recipe_id
    for item in result:
        assert item["food_id"] is not None
        assert item["group"] == "Yogurt & Granola Breakfast"
        assert item["source_recipe_id"] == breakfast_recipe.id
        assert "macros_per_serving" in item
        assert "serving_size_grams" in item

    # Amounts should be scaled (200g total / 200g recipe = 1.0x)
    yogurt_item = next(r for r in result if r["name"] == "Greek Yogurt")
    granola_item = next(r for r in result if r["name"] == "Granola")
    assert yogurt_item["amount_grams"] == 150.0
    assert granola_item["amount_grams"] == 50.0


def test_resolve_chat_items_scales_recipe(
    session, yogurt, granola, breakfast_recipe,
):
    from app.routers.parse import _resolve_chat_items

    # Request 400g of a 200g recipe (2x scale)
    raw = [{"recipe_id": breakfast_recipe.id, "amount_grams": 400}]
    result = _resolve_chat_items(raw, session)

    assert len(result) == 2
    yogurt_item = next(r for r in result if r["name"] == "Greek Yogurt")
    granola_item = next(r for r in result if r["name"] == "Granola")
    assert yogurt_item["amount_grams"] == 300.0  # 150 * 2
    assert granola_item["amount_grams"] == 100.0  # 50 * 2


def test_resolve_chat_items_food_passthrough(session, yogurt):
    from app.routers.parse import _resolve_chat_items

    raw = [{"food_id": yogurt.id, "amount_grams": 200}]
    result = _resolve_chat_items(raw, session)
    assert len(result) == 1
    assert result[0]["food_id"] == yogurt.id
    assert result[0]["amount_grams"] == 200
    assert "group" not in result[0]
