from app.llm import _filter_chat_models


def _raw_model(
    model_id: str,
    *,
    name: str,
    created: int,
    prompt_cost: float,
    completion_cost: float,
    supported_parameters: list[str],
    input_modalities: list[str] | None = None,
    output_modalities: list[str] | None = None,
) -> dict:
    return {
        "id": model_id,
        "name": name,
        "created": created,
        "pricing": {
            "prompt": str(prompt_cost),
            "completion": str(completion_cost),
        },
        "supported_parameters": supported_parameters,
        "architecture": {
            "modality": "text->text",
            "input_modalities": input_modalities or ["text"],
            "output_modalities": output_modalities or ["text"],
        },
    }


def test_filter_chat_models_returns_tiered_models_for_each_provider():
    raw_models = [
        _raw_model(
            "anthropic/claude-haiku-4.5",
            name="Claude Haiku 4.5",
            created=1_799_800_000,
            prompt_cost=0.0000008,
            completion_cost=0.0000012,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "anthropic/claude-sonnet-4.5",
            name="Claude Sonnet 4.5",
            created=1_799_850_000,
            prompt_cost=0.000003,
            completion_cost=0.000009,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "anthropic/claude-opus-4.6",
            name="Claude Opus 4.6",
            created=1_799_900_000,
            prompt_cost=0.000015,
            completion_cost=0.000065,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "openai/gpt-5.2-mini",
            name="GPT-5.2 Mini",
            created=1_799_800_000,
            prompt_cost=0.00000025,
            completion_cost=0.00000075,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "openai/gpt-5.4",
            name="GPT-5.4",
            created=1_799_850_000,
            prompt_cost=0.0000025,
            completion_cost=0.0000075,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "openai/gpt-5.4-pro",
            name="GPT-5.4 Pro",
            created=1_799_900_000,
            prompt_cost=0.000012,
            completion_cost=0.000038,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "google/gemini-3.1-flash",
            name="Gemini 3.1 Flash",
            created=1_799_800_000,
            prompt_cost=0.0000002,
            completion_cost=0.0000003,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "google/gemini-3.1-pro",
            name="Gemini 3.1 Pro",
            created=1_799_850_000,
            prompt_cost=0.0000015,
            completion_cost=0.0000025,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "google/gemini-3.1-ultra",
            name="Gemini 3.1 Ultra",
            created=1_799_900_000,
            prompt_cost=0.0000075,
            completion_cost=0.0000125,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "qwen/qwen3.5-flash",
            name="Qwen 3.5 Flash",
            created=1_799_800_000,
            prompt_cost=0.0000001,
            completion_cost=0.0000003,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "qwen/qwen3.5-plus",
            name="Qwen 3.5 Plus",
            created=1_799_850_000,
            prompt_cost=0.000001,
            completion_cost=0.000002,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "qwen/qwen3.5-max",
            name="Qwen 3.5 Max",
            created=1_799_900_000,
            prompt_cost=0.000006,
            completion_cost=0.000012,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
    ]

    models = _filter_chat_models(raw_models)

    assert len(models) == 12
    assert all(model.get("tier_label") for model in models)

    grouped: dict[str, set[str]] = {}
    for model in models:
        grouped.setdefault(model["provider"], set()).add(model["tier"])

    assert grouped == {
        "Anthropic": {"low", "medium", "high_reasoning"},
        "OpenAI": {"low", "medium", "high_reasoning"},
        "Gemini": {"low", "medium", "high_reasoning"},
        "Qwen": {"low", "medium", "high_reasoning"},
    }


def test_filter_chat_models_excludes_coding_image_story_and_gemma_models():
    raw_models = [
        _raw_model(
            "openai/gpt-5.2-mini",
            name="GPT-5.2 Mini",
            created=1_799_900_000,
            prompt_cost=0.0000002,
            completion_cost=0.0000006,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "openai/gpt-5-codex",
            name="GPT-5 Codex",
            created=1_799_900_000,
            prompt_cost=0.000001,
            completion_cost=0.000003,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "google/gemini-3.1-flash-image",
            name="Gemini 3.1 Flash Image",
            created=1_799_900_000,
            prompt_cost=0.0000005,
            completion_cost=0.000001,
            supported_parameters=["tools", "tool_choice"],
            output_modalities=["image"],
        ),
        _raw_model(
            "anthropic/claude-storytelling-1",
            name="Claude Storytelling 1",
            created=1_799_900_000,
            prompt_cost=0.000001,
            completion_cost=0.000002,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "google/gemma-3n",
            name="Gemma 3N",
            created=1_799_900_000,
            prompt_cost=0.000001,
            completion_cost=0.000002,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "nvidia/llama-3.3",
            name="NVIDIA Llama 3.3",
            created=1_799_900_000,
            prompt_cost=0.000001,
            completion_cost=0.000002,
            supported_parameters=["tools", "tool_choice"],
        ),
    ]

    models = _filter_chat_models(raw_models)
    model_ids = {model["id"] for model in models}

    assert "openai/gpt-5.2-mini" in model_ids
    assert "openai/gpt-5-codex" not in model_ids
    assert "google/gemini-3.1-flash-image" not in model_ids
    assert "anthropic/claude-storytelling-1" not in model_ids
    assert "google/gemma-3n" not in model_ids
    assert "nvidia/llama-3.3" not in model_ids


def test_filter_chat_models_excludes_old_generation_models_per_provider():
    raw_models = [
        _raw_model(
            "openai/gpt-4o-mini",
            name="GPT-4o Mini",
            created=1_740_000_000,
            prompt_cost=0.0000002,
            completion_cost=0.0000006,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "openai/gpt-4o",
            name="GPT-4o",
            created=1_740_100_000,
            prompt_cost=0.0000025,
            completion_cost=0.0000075,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "openai/gpt-4.1",
            name="GPT-4.1",
            created=1_740_200_000,
            prompt_cost=0.000008,
            completion_cost=0.000024,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "openai/gpt-5.2-mini",
            name="GPT-5.2 Mini",
            created=1_799_800_000,
            prompt_cost=0.00000025,
            completion_cost=0.00000075,
            supported_parameters=["tools", "tool_choice"],
        ),
        _raw_model(
            "openai/gpt-5.4",
            name="GPT-5.4",
            created=1_799_850_000,
            prompt_cost=0.0000025,
            completion_cost=0.0000075,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
        _raw_model(
            "openai/gpt-5.4-pro",
            name="GPT-5.4 Pro",
            created=1_799_900_000,
            prompt_cost=0.000012,
            completion_cost=0.000038,
            supported_parameters=["tools", "tool_choice", "reasoning"],
        ),
    ]

    models = _filter_chat_models(raw_models)
    model_ids = {model["id"] for model in models}

    assert model_ids == {"openai/gpt-5.2-mini", "openai/gpt-5.4", "openai/gpt-5.4-pro"}
