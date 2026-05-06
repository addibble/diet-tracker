"""Tests for POST /api/foods/merge and GET /api/foods/audit."""


def _create_food(client, name, **kwargs):
    payload = {
        "name": name,
        "serving_size_grams": 100,
        "calories_per_serving": 100,
        "fat_per_serving": 1,
        "carbs_per_serving": 1,
        "protein_per_serving": 1,
    }
    payload.update(kwargs)
    resp = client.post("/api/foods", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Merge ─────────────────────────────────────────────────────────────


def test_merge_reassigns_meal_items(client):
    src = _create_food(client, "Apple Old")
    tgt = _create_food(client, "Apple New")

    meal = client.post("/api/meals", json={
        "date": "2026-05-01", "meal_type": "breakfast",
        "items": [{"food_id": src["id"], "amount_grams": 150}],
    }).json()

    resp = client.post(
        "/api/foods/merge",
        json={"source_id": src["id"], "target_id": tgt["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merged_meal_items"] == 1
    assert body["merged_recipe_components"] == 0

    # Source is gone
    assert client.get(f"/api/foods/{src['id']}").status_code == 404
    # Meal item now points to target
    fetched = client.get(f"/api/meals/{meal['id']}").json()
    assert fetched["items"][0]["food_id"] == tgt["id"]


def test_merge_reassigns_recipe_components(client):
    src = _create_food(client, "Flour A")
    tgt = _create_food(client, "Flour B")

    recipe = client.post("/api/recipes", json={
        "name": "Bread",
        "components": [{"food_id": src["id"], "amount_grams": 200}],
    }).json()

    resp = client.post(
        "/api/foods/merge",
        json={"source_id": src["id"], "target_id": tgt["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["merged_recipe_components"] == 1

    fetched = client.get(f"/api/recipes/{recipe['id']}").json()
    assert fetched["components"][0]["food_id"] == tgt["id"]


def test_merge_rejects_same_id(client):
    f = _create_food(client, "Same")
    resp = client.post(
        "/api/foods/merge",
        json={"source_id": f["id"], "target_id": f["id"]},
    )
    assert resp.status_code == 400


def test_merge_404_when_missing(client):
    f = _create_food(client, "Real")
    resp = client.post(
        "/api/foods/merge",
        json={"source_id": 99999, "target_id": f["id"]},
    )
    assert resp.status_code == 404


# ── Audit ─────────────────────────────────────────────────────────────


def test_audit_detects_unused(client):
    _create_food(client, "Zero Use Food")
    resp = client.get("/api/foods/audit")
    assert resp.status_code == 200
    data = resp.json()
    names = {f["name"] for f in data["unused"]}
    assert "Zero Use Food" in names


def test_audit_detects_missing_macros(client):
    _create_food(
        client, "Empty Macros",
        calories_per_serving=0,
        fat_per_serving=0,
        carbs_per_serving=0,
        protein_per_serving=0,
    )
    resp = client.get("/api/foods/audit")
    assert resp.status_code == 200
    data = resp.json()
    names = {f["name"] for f in data["missing_macros"]}
    assert "Empty Macros" in names


def test_audit_detects_fuzzy_duplicates(client):
    _create_food(client, "Chicken Breast")
    _create_food(client, "Chicken Breasts")  # near duplicate
    resp = client.get("/api/foods/audit")
    assert resp.status_code == 200
    groups = resp.json()["duplicate_groups"]
    flat_names = [
        f["name"] for g in groups for f in g["foods"]
    ]
    assert "Chicken Breast" in flat_names
    assert "Chicken Breasts" in flat_names


def test_audit_usage_counts(client):
    f = _create_food(client, "Banana Audit")
    client.post("/api/meals", json={
        "date": "2026-05-01", "meal_type": "breakfast",
        "items": [{"food_id": f["id"], "amount_grams": 100}],
    })
    resp = client.get("/api/foods/audit")
    data = resp.json()
    # f should NOT be in unused
    names = {x["name"] for x in data["unused"]}
    assert "Banana Audit" not in names


def test_audit_recipes(client):
    f = _create_food(client, "Audit Food")
    # Empty recipe
    empty = client.post("/api/recipes", json={
        "name": "Empty Recipe", "components": [],
    }).json()
    # Healthy recipe
    client.post("/api/recipes", json={
        "name": "Healthy Recipe",
        "components": [{"food_id": f["id"], "amount_grams": 50}],
    })

    resp = client.get("/api/recipes/audit")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    empty_names = {r["name"] for r in data["empty"]}
    assert "Empty Recipe" in empty_names
    # Healthy recipe should NOT be in empty
    assert "Healthy Recipe" not in empty_names
    assert empty["id"] in {r["id"] for r in data["empty"]}
