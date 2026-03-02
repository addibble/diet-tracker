def test_create_recipe(client):
    bread = client.post("/api/foods", json={
        "name": "Bread", "calories_per_serving": 265, "fat_per_serving": 3.2,
        "carbs_per_serving": 49, "protein_per_serving": 9,
    }).json()
    ham = client.post("/api/foods", json={
        "name": "Ham", "calories_per_serving": 145, "fat_per_serving": 6,
        "saturated_fat_per_serving": 2, "sodium_per_serving": 1200,
        "carbs_per_serving": 1.5, "protein_per_serving": 21,
    }).json()
    cheese = client.post("/api/foods", json={
        "name": "Cheddar", "calories_per_serving": 403, "fat_per_serving": 33,
        "saturated_fat_per_serving": 21, "cholesterol_per_serving": 105,
        "carbs_per_serving": 1.3, "protein_per_serving": 25,
    }).json()

    resp = client.post("/api/recipes", json={
        "name": "Ham Sandwich",
        "components": [
            {"food_id": bread["id"], "amount_grams": 60},
            {"food_id": ham["id"], "amount_grams": 50},
            {"food_id": cheese["id"], "amount_grams": 30},
        ],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Ham Sandwich"
    assert len(data["components"]) == 3
    assert data["total_calories"] > 0
    assert data["total_protein"] > 0
    assert data["total_sodium"] > 0


def test_update_recipe_components(client):
    food = client.post("/api/foods", json={
        "name": "Tuna", "calories_per_serving": 132, "fat_per_serving": 1.3,
        "carbs_per_serving": 0, "protein_per_serving": 29,
    }).json()
    recipe = client.post("/api/recipes", json={
        "name": "Tuna Bowl",
        "components": [{"food_id": food["id"], "amount_grams": 100}],
    }).json()

    resp = client.put(f"/api/recipes/{recipe['id']}", json={
        "components": [{"food_id": food["id"], "amount_grams": 150}],
    })
    assert resp.status_code == 200
    assert resp.json()["components"][0]["amount_grams"] == 150


def test_delete_recipe(client):
    food = client.post("/api/foods", json={
        "name": "Oats", "calories_per_serving": 389, "fat_per_serving": 7,
        "carbs_per_serving": 66, "fiber_per_serving": 10, "protein_per_serving": 17,
    }).json()
    recipe = client.post("/api/recipes", json={
        "name": "Oatmeal",
        "components": [{"food_id": food["id"], "amount_grams": 50}],
    }).json()
    resp = client.delete(f"/api/recipes/{recipe['id']}")
    assert resp.status_code == 204
