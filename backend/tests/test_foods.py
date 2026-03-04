from unittest.mock import AsyncMock, patch


def test_create_food(client):
    resp = client.post("/api/foods", json={
        "name": "Chicken Breast", "serving_size_grams": 100,
        "calories_per_serving": 165, "fat_per_serving": 3.6,
        "saturated_fat_per_serving": 1.0, "cholesterol_per_serving": 85,
        "sodium_per_serving": 74, "carbs_per_serving": 0,
        "fiber_per_serving": 0, "protein_per_serving": 31,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Chicken Breast"
    assert data["id"] is not None
    assert data["serving_size_grams"] == 100
    assert data["saturated_fat_per_serving"] == 1.0


def test_create_food_custom_serving(client):
    """Foods can have non-100g serving sizes (e.g. from nutrition labels)."""
    resp = client.post("/api/foods", json={
        "name": "Granola Bar", "serving_size_grams": 40,
        "calories_per_serving": 190, "fat_per_serving": 7,
        "saturated_fat_per_serving": 1, "carbs_per_serving": 29,
        "fiber_per_serving": 2, "protein_per_serving": 3,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["serving_size_grams"] == 40
    assert data["calories_per_serving"] == 190


def test_list_foods(client):
    client.post("/api/foods", json={
        "name": "Rice", "calories_per_serving": 130, "fat_per_serving": 0.3,
        "carbs_per_serving": 28, "protein_per_serving": 2.7,
    })
    client.post("/api/foods", json={
        "name": "Broccoli", "calories_per_serving": 34, "fat_per_serving": 0.4,
        "carbs_per_serving": 7, "protein_per_serving": 2.8,
    })
    resp = client.get("/api/foods")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_search_foods(client):
    client.post("/api/foods", json={
        "name": "Banana", "calories_per_serving": 89, "fat_per_serving": 0.3,
        "carbs_per_serving": 23, "protein_per_serving": 1.1,
    })
    client.post("/api/foods", json={
        "name": "Apple", "calories_per_serving": 52, "fat_per_serving": 0.2,
        "carbs_per_serving": 14, "protein_per_serving": 0.3,
    })
    resp = client.get("/api/foods?search=Ban")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["name"] == "Banana"


def test_update_food(client):
    resp = client.post("/api/foods", json={
        "name": "Egg", "calories_per_serving": 155, "fat_per_serving": 11,
        "carbs_per_serving": 1.1, "protein_per_serving": 13,
    })
    food_id = resp.json()["id"]
    resp = client.put(f"/api/foods/{food_id}", json={"name": "Whole Egg"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Whole Egg"


def test_delete_food(client):
    resp = client.post("/api/foods", json={
        "name": "Butter", "serving_size_grams": 14,
        "calories_per_serving": 100, "fat_per_serving": 11,
        "saturated_fat_per_serving": 7, "cholesterol_per_serving": 30,
        "sodium_per_serving": 2, "carbs_per_serving": 0,
        "fiber_per_serving": 0, "protein_per_serving": 0.1,
    })
    food_id = resp.json()["id"]
    resp = client.delete(f"/api/foods/{food_id}")
    assert resp.status_code == 204
    resp = client.get(f"/api/foods/{food_id}")
    assert resp.status_code == 404


def test_new_fields_default_to_zero(client):
    """New macro fields should default to 0 when not provided."""
    resp = client.post("/api/foods", json={
        "name": "Simple Food", "calories_per_serving": 100,
        "fat_per_serving": 5, "carbs_per_serving": 10, "protein_per_serving": 8,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["serving_size_grams"] == 100  # default
    assert data["saturated_fat_per_serving"] == 0
    assert data["cholesterol_per_serving"] == 0
    assert data["sodium_per_serving"] == 0
    assert data["fiber_per_serving"] == 0


def test_create_food_with_brand(client):
    resp = client.post("/api/foods", json={
        "name": "Oat Nut Bread", "brand": "Oroweat",
        "calories_per_serving": 110, "fat_per_serving": 2,
        "carbs_per_serving": 20, "protein_per_serving": 4,
    })
    assert resp.status_code == 201
    assert resp.json()["brand"] == "Oroweat"


def test_create_food_without_brand(client):
    resp = client.post("/api/foods", json={
        "name": "Rice", "calories_per_serving": 130,
        "fat_per_serving": 0.3, "carbs_per_serving": 28, "protein_per_serving": 2.7,
    })
    assert resp.status_code == 201
    assert resp.json()["brand"] is None


def test_update_food_brand(client):
    resp = client.post("/api/foods", json={
        "name": "Cheddar", "calories_per_serving": 110,
        "fat_per_serving": 9, "carbs_per_serving": 0, "protein_per_serving": 7,
    })
    food_id = resp.json()["id"]
    resp = client.put(f"/api/foods/{food_id}", json={"brand": "Kirkland"})
    assert resp.status_code == 200
    assert resp.json()["brand"] == "Kirkland"


def test_search_foods_by_brand(client):
    client.post("/api/foods", json={
        "name": "Honey Ham", "brand": "Hillshire Farm",
        "calories_per_serving": 60, "fat_per_serving": 1.5,
        "carbs_per_serving": 2, "protein_per_serving": 10,
    })
    client.post("/api/foods", json={
        "name": "Turkey", "calories_per_serving": 50,
        "fat_per_serving": 1, "carbs_per_serving": 1, "protein_per_serving": 10,
    })
    resp = client.get("/api/foods?search=Hillshire")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["brand"] == "Hillshire Farm"


def test_import_food_label_success(client):
    with patch(
        "app.routers.foods.parse_nutrition_label_image",
        new_callable=AsyncMock,
    ) as mock_import:
        mock_import.return_value = {
            "name": "granola bar",
            "brand": "Nature Valley",
            "serving_size_grams": 40,
            "calories_per_serving": 190,
            "fat_per_serving": 7,
            "saturated_fat_per_serving": 1,
            "cholesterol_per_serving": 0,
            "sodium_per_serving": 120,
            "carbs_per_serving": 29,
            "fiber_per_serving": 2,
            "protein_per_serving": 3,
        }
        resp = client.post(
            "/api/foods/import-label",
            files={"image": ("label.jpg", b"fake-image-bytes", "image/jpeg")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "granola bar"
    assert data["brand"] == "Nature Valley"
    assert data["serving_size_grams"] == 40
    mock_import.assert_awaited_once()
    assert mock_import.await_args.kwargs["mime_type"] == "image/jpeg"
    assert mock_import.await_args.kwargs["image_bytes"] == b"fake-image-bytes"


def test_import_food_label_rejects_non_image(client):
    resp = client.post(
        "/api/foods/import-label",
        files={"image": ("label.txt", b"not-an-image", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Please upload an image file"


def test_import_food_label_handles_llm_validation_error(client):
    with patch(
        "app.routers.foods.parse_nutrition_label_image",
        new_callable=AsyncMock,
    ) as mock_import:
        mock_import.side_effect = ValueError("Could not parse nutrition label")
        resp = client.post(
            "/api/foods/import-label",
            files={"image": ("label.jpg", b"fake-image-bytes", "image/jpeg")},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Could not parse nutrition label"
