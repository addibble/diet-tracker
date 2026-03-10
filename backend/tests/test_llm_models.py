from app.llm import CHAT_ALLOWED_MODELS, _filter_chat_models


def _raw_model(
    model_id: str,
    *,
    name: str,
    created: int = 1_799_900_000,
    prompt_cost: float = 0.000001,
    completion_cost: float = 0.000002,
) -> dict:
    return {
        "id": model_id,
        "name": name,
        "created": created,
        "pricing": {
            "prompt": str(prompt_cost),
            "completion": str(completion_cost),
        },
    }


def test_filter_chat_models_returns_only_allowed_models():
    raw_models = [
        _raw_model("anthropic/claude-haiku-4.5", name="Claude Haiku 4.5"),
        _raw_model("anthropic/claude-sonnet-4.6", name="Claude Sonnet 4.6"),
        _raw_model("openai/gpt-5.4", name="GPT-5.4"),
        _raw_model("openai/gpt-5.4-pro", name="GPT-5.4 Pro"),
        _raw_model("google/gemini-3.1-flash-lite-preview", name="Gemini 3.1 Flash Lite"),
        _raw_model("x-ai/grok-4", name="Grok 4"),
        _raw_model("deepseek/deepseek-v3.2", name="DeepSeek V3.2"),
        # Not in allowlist — should be excluded
        _raw_model("openai/gpt-4o-mini", name="GPT-4o Mini"),
        _raw_model("nvidia/llama-3.3", name="NVIDIA Llama 3.3"),
        _raw_model("anthropic/claude-storytelling-1", name="Claude Storytelling 1"),
    ]

    models = _filter_chat_models(raw_models)
    model_ids = {model["id"] for model in models}

    assert "anthropic/claude-haiku-4.5" in model_ids
    assert "openai/gpt-5.4" in model_ids
    assert "x-ai/grok-4" in model_ids
    assert "deepseek/deepseek-v3.2" in model_ids

    assert "openai/gpt-4o-mini" not in model_ids
    assert "nvidia/llama-3.3" not in model_ids
    assert "anthropic/claude-storytelling-1" not in model_ids


def test_filter_chat_models_preserves_allowlist_order():
    # Provide models in reverse order — output should follow CHAT_ALLOWED_MODELS order
    raw_models = [
        _raw_model("deepseek/deepseek-v3.2", name="DeepSeek V3.2"),
        _raw_model("anthropic/claude-haiku-4.5", name="Claude Haiku 4.5"),
        _raw_model("openai/gpt-5.4", name="GPT-5.4"),
    ]

    models = _filter_chat_models(raw_models)
    model_ids = [model["id"] for model in models]

    assert model_ids == [
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5.4",
        "deepseek/deepseek-v3.2",
    ]


def test_filter_chat_models_extracts_pricing_and_provider():
    raw_models = [
        _raw_model(
            "anthropic/claude-haiku-4.5",
            name="Claude Haiku 4.5",
            prompt_cost=0.0000008,
            completion_cost=0.0000012,
        ),
    ]

    models = _filter_chat_models(raw_models)
    assert len(models) == 1

    model = models[0]
    assert model["provider"] == "Anthropic"
    assert model["input_cost_per_million"] == 0.8
    assert model["output_cost_per_million"] == 1.2
    assert "tier" not in model
