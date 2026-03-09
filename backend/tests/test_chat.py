import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, patch

from sqlmodel import select

from app.llm import (
    LLMUpstreamBillingError,
    LLMUpstreamCompletionError,
    LLMUpstreamRetryableError,
    LLMUpstreamTimeoutError,
)
from app.models import MacroTarget, WeightLog


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


def test_chat_passes_selected_model(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Got it."
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "ham sandwich"}],
            "model": "google/gemini-2.5-flash",
        })

    assert resp.status_code == 200
    assert mock_llm.call_args.kwargs["model"] == "google/gemini-2.5-flash"


def test_chat_models_endpoint(client):
    models = [
        {
            "id": "anthropic/claude-3.5-haiku",
            "name": "Claude 3.5 Haiku",
            "provider": "Anthropic",
            "input_cost_per_million": 0.8,
            "output_cost_per_million": 1.6,
            "created": 1_730_000_000,
        },
    ]
    with patch("app.routers.parse.get_chat_models", new_callable=AsyncMock) as mock_models:
        mock_models.return_value = models
        resp = client.get("/api/meals/chat/models")

    assert resp.status_code == 200
    data = resp.json()
    assert data["default_model"]
    assert data["models"] == models
    mock_models.assert_awaited_once()


def test_chat_returns_504_on_llm_timeout(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamTimeoutError("timed out")
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "big prompt"}],
        })

    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"]


def test_chat_returns_502_on_llm_retryable_upstream_error(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamRetryableError("bad gateway")
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "big prompt"}],
        })

    assert resp.status_code == 502
    assert "upstream error" in resp.json()["detail"]


def test_chat_returns_402_on_llm_credit_limit(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamBillingError("fewer max_tokens")
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "large prompt"}],
        })

    assert resp.status_code == 402
    assert "credit limit" in resp.json()["detail"]


def test_chat_returns_502_on_llm_generation_error(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamCompletionError(
            "finish_reason=error; native_finish_reason=MALFORMED_FUNCTION_CALL",
        )
        resp = client.post("/api/meals/chat", json={
            "messages": [{"role": "user", "content": "large prompt"}],
        })

    assert resp.status_code == 502
    assert "generation error" in resp.json()["detail"]
    assert "MALFORMED_FUNCTION_CALL" in resp.json()["detail"]


def test_chat_stream_returns_status_and_result(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Got it."
        resp = client.post("/api/meals/chat/stream", json={
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert resp.status_code == 200
    events = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    assert any(event.get("type") == "status" for event in events)
    result_event = next(event for event in events if event.get("type") == "result")
    assert result_event["data"]["message"] == "Got it."


def test_chat_stream_emits_error_event(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamTimeoutError("timed out")
        resp = client.post("/api/meals/chat/stream", json={
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert resp.status_code == 200
    events = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    error_event = next(event for event in events if event.get("type") == "error")
    assert error_event["status"] == 504


def test_chat_stream_emits_generation_error_event(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = LLMUpstreamCompletionError(
            "finish_reason=error; native_finish_reason=MALFORMED_FUNCTION_CALL",
        )
        resp = client.post("/api/meals/chat/stream", json={
            "messages": [{"role": "user", "content": "hello"}],
        })

    assert resp.status_code == 200
    events = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    error_event = next(event for event in events if event.get("type") == "error")
    assert error_event["status"] == 502
    assert "generation error" in error_event["detail"]
    assert "MALFORMED_FUNCTION_CALL" in error_event["detail"]


def test_chat_stream_flushes_rapid_status_transitions(client):
    async def fake_chat(*_args, **_kwargs):
        from app.llm import _emit_chat_status

        _emit_chat_status({
            "event": "upstream_round_complete",
            "round": 1,
            "finish_reason": "tool_calls",
        })
        _emit_chat_status({
            "event": "tool_call_started",
            "round": 1,
            "tool_name": "set_workout_sessions",
        })
        await asyncio.sleep(0)
        return "Logged it."

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = fake_chat
        resp = client.post("/api/meals/chat/stream", json={
            "messages": [{"role": "user", "content": "log a workout"}],
        })

    assert resp.status_code == 200
    events = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    activity_events = [
        event for event in events
        if event.get("type") == "status" and event.get("last_activity_event")
    ]
    event_names = [event["last_activity_event"] for event in activity_events]
    assert "upstream_round_complete" in event_names
    assert "tool_call_started" in event_names
    tool_event = next(
        event for event in activity_events
        if event["last_activity_event"] == "tool_call_started"
    )
    assert tool_event["activity_source"] == "local_tool"
    assert tool_event["active_tool_name"] == "set_workout_sessions"


def test_chat_does_not_preload_recent_meals(client):
    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Got it."
        client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "what did I eat today?"},
            ],
        })

    mock_llm.assert_called_once()
    recent_meals = mock_llm.call_args.args[3]
    assert recent_meals is None


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
            "client_now_iso": "2026-03-07T20:00:00-07:00",
            "client_timezone": "America/Denver",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_meal"] is not None
    assert data["saved_meal"]["meal_type"] == "dinner"
    assert data["saved_meal"]["date"] == "2026-03-06"


def test_chat_uses_client_local_datetime_for_inferred_defaults(client):
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
                {"role": "user", "content": "I had greek yogurt"},
                {"role": "user", "content": "yes save it"},
            ],
            "client_now_iso": "2026-03-02T01:30:00+00:00",
            "client_timezone": "America/Denver",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_meal"] is not None
    assert data["saved_meal"]["meal_type"] == "dinner"
    assert data["saved_meal"]["date"] == "2026-03-01"


def test_chat_can_log_weight(client, session):
    async def fake_chat(
        messages,
        known_foods,
        known_recipes,
        recent_meals,
        tool_executor,
        **kwargs,
    ):
        assert messages[-1]["content"] == "Log my weight as 180.4 pounds"
        result = await tool_executor("set_weight_logs", {
            "changes": [{"operation": "create", "set": {"weight_lb": 180.4}}],
        })
        assert result["created_count"] == 1
        return "Logged your weight at 180.4 lb."

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = fake_chat
        resp = client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "Log my weight as 180.4 pounds"},
            ],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Logged your weight at 180.4 lb."
    assert data["proposed_items"] is None
    assert data["saved_meal"] is None
    assert data["data_changed"] is True

    weight_logs = session.exec(select(WeightLog)).all()
    assert len(weight_logs) == 1
    assert weight_logs[0].weight_lb == 180.4


def test_chat_can_set_macro_target(client, session):
    async def fake_chat(
        messages,
        known_foods,
        known_recipes,
        recent_meals,
        tool_executor,
        **kwargs,
    ):
        expected = (
            "Set my macro targets for today to 2200 calories and 180 protein"
        )
        assert messages[-1]["content"] == expected
        result = await tool_executor("set_macro_targets", {
            "changes": [{
                "operation": "create",
                "set": {
                    "day": "2026-03-07",
                    "calories": 2200,
                    "protein": 180,
                    "carbs": 220,
                    "fat": 70,
                },
            }],
        })
        assert result["created_count"] == 1
        return "Saved your macro targets for 2026-03-07."

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = fake_chat
        resp = client.post("/api/meals/chat", json={
            "messages": [
                {
                    "role": "user",
                    "content": "Set my macro targets for today to 2200 calories and 180 protein",
                },
            ],
            "client_now_iso": "2026-03-07T18:00:00+00:00",
            "client_timezone": "America/Denver",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Saved your macro targets for 2026-03-07."
    assert data["proposed_items"] is None
    assert data["saved_meal"] is None
    assert data["data_changed"] is True

    target = session.exec(
        select(MacroTarget).where(MacroTarget.day == date(2026, 3, 7))
    ).first()
    assert target is not None
    assert target.calories == 2200
    assert target.protein == 180
    assert target.carbs == 220
    assert target.fat == 70
    assert target.saturated_fat == 0


def test_chat_can_query_macro_targets(client):
    client.post("/api/macro-targets", json={
        "day": "2026-03-01",
        "calories": 2100,
        "fat": 65,
        "saturated_fat": 20,
        "cholesterol": 300,
        "sodium": 2300,
        "carbs": 220,
        "fiber": 30,
        "protein": 170,
    })
    client.post("/api/macro-targets", json={
        "day": "2026-03-05",
        "calories": 2400,
        "fat": 80,
        "saturated_fat": 25,
        "cholesterol": 320,
        "sodium": 2500,
        "carbs": 260,
        "fiber": 32,
        "protein": 190,
    })

    async def fake_chat(
        messages,
        known_foods,
        known_recipes,
        recent_meals,
        tool_executor,
        **kwargs,
    ):
        # Single-day lookup returns the active target for that day
        active_result = await tool_executor("get_macro_targets", {
            "filters": {"day": {"eq": "2026-03-07"}},
        })
        assert active_result["table"] == "macro_targets"
        assert active_result["count"] == 1
        assert active_result["matches"][0]["day"] == "2026-03-05"
        assert active_result["matches"][0]["calories"] == 2400

        # Range lookup returns all targets in the range
        range_result = await tool_executor("get_macro_targets", {
            "filters": {"day": {"gte": "2026-03-01", "lte": "2026-03-10"}},
        })
        assert range_result["table"] == "macro_targets"
        assert range_result["count"] == 2
        return "Your active target is 2400 calories."

    with patch("app.routers.parse.chat_meal", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = fake_chat
        resp = client.post("/api/meals/chat", json={
            "messages": [
                {"role": "user", "content": "What are my macro targets?"},
            ],
            "client_now_iso": "2026-03-07T18:00:00+00:00",
            "client_timezone": "America/Denver",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Your active target is 2400 calories."
    assert data["proposed_items"] is None
    assert data["saved_meal"] is None
    assert data["data_changed"] is False
