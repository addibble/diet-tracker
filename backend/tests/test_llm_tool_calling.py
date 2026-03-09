import json
from unittest.mock import patch

import pytest

from app.llm import (
    CHAT_TOOLS,
    LLMUpstreamCompletionError,
    _select_chat_tools,
    chat_meal,
    chat_status_callback,
)


def _tool_names(tools: list[dict]) -> list[str]:
    return [tool["function"]["name"] for tool in tools]


def test_select_chat_tools_uses_chat_tools_for_meal_logging():
    tools = _select_chat_tools([
        {"role": "user", "content": "I had eggs and toast for breakfast"},
    ])

    names = _tool_names(tools)

    assert names == _tool_names(CHAT_TOOLS)
    assert "log_workout" not in names


def test_select_chat_tools_narrows_to_weight_tool():
    tools = _select_chat_tools([
        {"role": "user", "content": "Log my weight as 180.4 pounds"},
    ])

    assert _tool_names(tools) == ["log_weight"]


def test_select_chat_tools_uses_workout_tools_for_workout_turn():
    tools = _select_chat_tools([
        {"role": "user", "content": "Bench 225x5x3 and incline dumbbell press 3x10"},
    ])

    names = _tool_names(tools)

    assert "log_workout" in names
    assert "query_food_log" not in names


@pytest.mark.anyio
async def test_chat_meal_uses_protocol_correct_tool_messages_for_gemini():
    payloads: list[dict] = []

    async def fake_stream(_client, payload, _model_id=None):
        payloads.append(payload)
        if len(payloads) == 1:
            return {
                "id": "cmp_1",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tool_1",
                                    "type": "function",
                                    "function": {
                                        "name": "log_weight",
                                        "arguments": json.dumps({"weight_lb": 180.4}),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            }
        return {
            "id": "cmp_2",
            "choices": [
                {
                    "message": {"content": "Logged your weight at 180.4 lb."},
                    "finish_reason": "stop",
                },
            ],
        }

    async def fake_tool_executor(name: str, args: dict):
        assert name == "log_weight"
        assert args == {"weight_lb": 180.4}
        return {"success": True, "weight_lb": 180.4}

    with patch("app.llm._stream_openrouter_chat_completion", side_effect=fake_stream):
        result = await chat_meal(
            [{"role": "user", "content": "Log my weight as 180.4 pounds"}],
            known_foods=[],
            known_recipes=[],
            tool_executor=fake_tool_executor,
            model="google/gemini-2.5-flash",
        )

    assert result == "Logged your weight at 180.4 lb."
    assert payloads[0]["temperature"] == 1.0
    assert payloads[0]["parallel_tool_calls"] is False
    assert _tool_names(payloads[0]["tools"]) == ["log_weight"]

    second_messages = payloads[1]["messages"]
    assistant_message = next(msg for msg in second_messages if msg.get("role") == "assistant")
    tool_message = next(msg for msg in second_messages if msg.get("role") == "tool")
    assert assistant_message["tool_calls"][0]["function"]["name"] == "log_weight"
    assert json.loads(tool_message["content"])["success"] is True


@pytest.mark.anyio
async def test_chat_meal_emits_local_tool_status_events():
    status_events: list[dict] = []
    call_count = 0

    async def fake_stream(_client, _payload, _model_id=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "id": "cmp_1",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tool_1",
                                    "type": "function",
                                    "function": {
                                        "name": "log_weight",
                                        "arguments": json.dumps({"weight_lb": 180.4}),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            }
        return {
            "id": "cmp_2",
            "choices": [
                {
                    "message": {"content": "Logged your weight at 180.4 lb."},
                    "finish_reason": "stop",
                },
            ],
        }

    async def fake_tool_executor(name: str, args: dict):
        assert name == "log_weight"
        assert args == {"weight_lb": 180.4}
        return {"success": True}

    with patch("app.llm._stream_openrouter_chat_completion", side_effect=fake_stream):
        with chat_status_callback(status_events.append):
            result = await chat_meal(
                [{"role": "user", "content": "Log my weight as 180.4 pounds"}],
                known_foods=[],
                known_recipes=[],
                tool_executor=fake_tool_executor,
                model="google/gemini-2.5-flash",
            )

    assert result == "Logged your weight at 180.4 lb."
    event_names = [event["event"] for event in status_events]
    assert "tool_call_started" in event_names
    assert "tool_call_completed" in event_names


@pytest.mark.anyio
async def test_chat_meal_retries_gemini_with_forced_tool_choice_after_generation_error():
    payloads: list[dict] = []

    async def fake_stream(_client, payload, _model_id=None):
        payloads.append(payload)
        if len(payloads) == 1:
            raise LLMUpstreamCompletionError(
                "finish_reason=error; native_finish_reason=MALFORMED_FUNCTION_CALL",
            )
        if len(payloads) == 2:
            assert payload["tool_choice"] == {
                "type": "function",
                "function": {"name": "log_weight"},
            }
            return {
                "id": "cmp_retry",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tool_retry",
                                    "type": "function",
                                    "function": {
                                        "name": "log_weight",
                                        "arguments": json.dumps({"weight_lb": 180.4}),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            }
        return {
            "id": "cmp_final",
            "choices": [
                {
                    "message": {"content": "Logged your weight at 180.4 lb."},
                    "finish_reason": "stop",
                },
            ],
        }

    async def fake_tool_executor(name: str, args: dict):
        assert name == "log_weight"
        assert args == {"weight_lb": 180.4}
        return {"success": True, "weight_lb": 180.4}

    with patch("app.llm._stream_openrouter_chat_completion", side_effect=fake_stream):
        result = await chat_meal(
            [{"role": "user", "content": "Log my weight as 180.4 pounds"}],
            known_foods=[],
            known_recipes=[],
            tool_executor=fake_tool_executor,
            model="google/gemini-2.5-flash",
        )

    assert result == "Logged your weight at 180.4 lb."
    assert len(payloads) == 3
