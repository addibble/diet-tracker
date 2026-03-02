from unittest.mock import AsyncMock, patch


def test_parse_meal_with_db_match(client):
    """When LLM matches a food in DB by id, parse should use it directly."""
    food = client.post("/api/foods", json={
        "name": "chicken breast", "calories_per_serving": 165,
        "fat_per_serving": 3.6, "carbs_per_serving": 0, "protein_per_serving": 31,
    }).json()

    with patch("app.routers.parse.parse_meal_description", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [
            {"name": "chicken breast", "amount_grams": 200, "food_id": food["id"]}
        ]
        resp = client.post("/api/meals/parse", json={"description": "200g chicken breast"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["food_id"] == food["id"]
    assert data["items"][0]["source"] == "db"
    assert data["items"][0]["amount_grams"] == 200


def test_parse_meal_with_usda_lookup(client):
    """When food is not in DB, parse should look up USDA and create it."""
    usda_result = {
        "name": "Salmon",
        "serving_size_grams": 100,
        "calories_per_serving": 208,
        "fat_per_serving": 13.4,
        "saturated_fat_per_serving": 3.05,
        "cholesterol_per_serving": 55,
        "sodium_per_serving": 59,
        "carbs_per_serving": 0,
        "fiber_per_serving": 0,
        "protein_per_serving": 20.4,
    }

    with (
        patch("app.routers.parse.parse_meal_description", new_callable=AsyncMock) as mock_llm,
        patch("app.routers.parse.search_usda", new_callable=AsyncMock) as mock_usda,
    ):
        mock_llm.return_value = [{"name": "salmon", "amount_grams": 150}]
        mock_usda.return_value = usda_result
        resp = client.post("/api/meals/parse", json={"description": "150g salmon"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["source"] == "usda"
    assert data["items"][0]["food_id"] is not None
    assert len(data["new_foods"]) == 1
    assert data["new_foods"][0]["name"] == "Salmon"


def test_parse_meal_unknown_food(client):
    """When neither DB nor USDA has the food, return it as unknown."""
    with (
        patch("app.routers.parse.parse_meal_description", new_callable=AsyncMock) as mock_llm,
        patch("app.routers.parse.search_usda", new_callable=AsyncMock) as mock_usda,
    ):
        mock_llm.return_value = [{"name": "mystery food", "amount_grams": 100}]
        mock_usda.return_value = None
        resp = client.post("/api/meals/parse", json={"description": "100g mystery food"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["food_id"] is None
    assert data["items"][0]["source"] == "unknown"


def test_parse_empty_description(client):
    resp = client.post("/api/meals/parse", json={"description": ""})
    assert resp.status_code == 400


def test_parse_passes_known_foods_to_llm(client):
    """LLM should be called with the known_foods list from DB."""
    client.post("/api/foods", json={
        "name": "oat nut bread", "brand": "Oroweat",
        "calories_per_serving": 110, "fat_per_serving": 2,
        "carbs_per_serving": 20, "protein_per_serving": 4,
    })

    with patch("app.routers.parse.parse_meal_description", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = []
        client.post("/api/meals/parse", json={"description": "toast"})

    mock_llm.assert_called_once()
    known_foods = mock_llm.call_args.args[1]
    assert any(f["name"] == "oat nut bread" and f["brand"] == "Oroweat" for f in known_foods)


def test_parse_stale_food_id_falls_back_to_usda(client):
    """If LLM returns a food_id that doesn't exist, fall back to USDA."""
    usda_result = {
        "name": "Salmon",
        "serving_size_grams": 100,
        "calories_per_serving": 208,
        "fat_per_serving": 13.4,
        "saturated_fat_per_serving": 3.05,
        "cholesterol_per_serving": 55,
        "sodium_per_serving": 59,
        "carbs_per_serving": 0,
        "fiber_per_serving": 0,
        "protein_per_serving": 20.4,
    }

    with (
        patch("app.routers.parse.parse_meal_description", new_callable=AsyncMock) as mock_llm,
        patch("app.routers.parse.search_usda", new_callable=AsyncMock) as mock_usda,
    ):
        mock_llm.return_value = [{"name": "salmon", "amount_grams": 150, "food_id": 9999}]
        mock_usda.return_value = usda_result
        resp = client.post("/api/meals/parse", json={"description": "salmon"})

    assert resp.status_code == 200
    assert resp.json()["items"][0]["source"] == "usda"
