from datetime import date, timedelta
from unittest.mock import AsyncMock, patch


def test_chat_proposes_items(client):
    """Chat endpoint should parse ITEMS block and return proposed items."""
    food = client.post("/api/foods", json={
        "name": "Oat Nut Bread", "brand": "Oroweat",
        "calories_per_serving": 110, "fat_per_serving": 2,
        "carbs_per_serving": 20, "protein_per_serving": 4,
    }).json()

    llm_response = (
        'Here\'s what I think you had:\n\n'
        f'<ITEMS>[{{"food_id": {food["id"]}, "name": "Oat Nut Bread", "amount_grams": 60}}]</ITEMS>'
    )

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = llm_response
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "toast"}],
            "date": "2026-03-01",
            "meal_type": "breakfast",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["proposed_items"] is not None
    assert len(data["proposed_items"]) == 1
    assert data["proposed_items"][0]["food_id"] == food["id"]
    assert data["proposed_items"][0]["amount_grams"] == 60
    assert data["proposed_items"][0]["macros_per_serving"]["calories"] == 110
    assert data["saved_meal"] is None


def test_chat_confirms_and_saves(client):
    """When LLM returns ITEMS + CONFIRM, meal should be auto-saved."""
    food = client.post("/api/foods", json={
        "name": "Chicken Breast",
        "calories_per_serving": 165, "fat_per_serving": 3.6,
        "carbs_per_serving": 0, "protein_per_serving": 31,
    }).json()

    llm_response = (
        'Great, saving your meal!\n'
        f'<ITEMS>[{{"food_id": {food["id"]}, "name": "Chicken Breast", '
        '"amount_grams": 200}]</ITEMS>\n'
        '<CONFIRM/>'
    )

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = llm_response
        resp = client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "200g chicken breast"},
                {"role": "assistant", "content": "Looks like 200g chicken breast."},
                {"role": "user", "content": "yes"},
            ],
            "date": "2026-03-01",
            "meal_type": "lunch",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_meal"] is not None
    assert data["saved_meal"]["meal_type"] == "lunch"
    assert len(data["saved_meal"]["items"]) == 1
    assert data["saved_meal"]["items"][0]["food_id"] == food["id"]


def test_chat_strips_tags_from_message(client):
    """The message field should have XML tags stripped out."""
    llm_response = (
        'Here is your breakdown:\n'
        '<ITEMS>[{"food_id": null, "name": "toast", "amount_grams": 60}]</ITEMS>\n'
        'Let me know if that looks right.'
    )

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = llm_response
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "toast"}],
            "date": "2026-03-01",
            "meal_type": "breakfast",
        })

    data = resp.json()
    assert "<ITEMS>" not in data["message"]
    assert "</ITEMS>" not in data["message"]
    assert "<CONFIRM" not in data["message"]


def test_chat_null_food_id_excluded_from_save(client):
    """Items with food_id=null should not be saved even on confirm."""
    llm_response = (
        'Saving!\n'
        '<ITEMS>[{"food_id": null, "name": "mystery food", "amount_grams": 100}]</ITEMS>\n'
        '<CONFIRM/>'
    )

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = llm_response
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "yes"}],
            "date": "2026-03-01",
            "meal_type": "lunch",
        })

    data = resp.json()
    assert data["saved_meal"] is None  # No saveable items


def test_chat_empty_messages_400(client):
    resp = client.post("/api/meals/chat", json={
        "messages": [],
        "date": "2026-03-01",
        "meal_type": "lunch",
    })
    assert resp.status_code == 400


def test_chat_passes_full_history(client):
    """All messages should be forwarded to the LLM."""
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Got it."
        client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "ham sandwich"},
                {"role": "assistant", "content": "60g bread, 50g ham?"},
                {"role": "user", "content": "make it 70g ham"},
            ],
            "date": "2026-03-01",
            "meal_type": "lunch",
        })

    mock_llm.assert_called_once()
    messages = mock_llm.call_args.args[0]
    assert len(messages) == 3
    assert messages[0]["content"] == "ham sandwich"
    assert messages[2]["content"] == "make it 70g ham"


def test_chat_infers_date_and_meal_type_from_message(client):
    food = client.post("/api/foods", json={
        "name": "Greek Yogurt",
        "calories_per_serving": 120, "fat_per_serving": 5,
        "carbs_per_serving": 7, "protein_per_serving": 12,
    }).json()

    llm_response = (
        "Saving now!\n"
        f'<ITEMS>[{{"food_id": {food["id"]}, "name": "Greek Yogurt", '
        '"amount_grams": 150}]</ITEMS>\n'
        "<CONFIRM/>"
    )

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = llm_response
        resp = client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "Yesterday for dinner I had greek yogurt"},
                {"role": "user", "content": "yes save it"},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_meal"] is not None
    assert data["saved_meal"]["meal_type"] == "dinner"
    assert data["saved_meal"]["date"] == str(date.today() - timedelta(days=1))
