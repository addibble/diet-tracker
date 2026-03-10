"""OpenRouter LLM client for parsing meal descriptions."""

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("parse")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODEL = "anthropic/claude-haiku-4.5"
VISION_MODEL = "anthropic/claude-haiku-4.5"
CHAT_MODEL_MAX_INPUT_COST_PER_MILLION = 1000.0
CHAT_MODEL_MAX_OUTPUT_COST_PER_MILLION = 1000.0
CHAT_MODEL_CACHE_TTL_SECONDS = 600.0
CHAT_MODEL_RECENCY_WINDOW_SECONDS = 400 * 24 * 60 * 60
OPENROUTER_CHAT_MAX_RETRIES = 2
OPENROUTER_RETRY_BASE_DELAY_SECONDS = 0.8
OPENROUTER_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
OPENROUTER_CHAT_MAX_TOKENS = 4096
OPENROUTER_CHAT_MAX_TOKENS_REASONING = 50000
OPENROUTER_STREAM_IDLE_TIMEOUT_SECONDS = 90
OPENROUTER_STREAM_IDLE_TIMEOUT_REASONING_SECONDS = 300

CHAT_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "qwen": "Qwen",
}

CHAT_PROVIDER_ORDER = {
    "anthropic": 0,
    "openai": 1,
    "gemini": 2,
    "qwen": 3,
}

CHAT_TIER_ORDER = {
    "low": 0,
    "medium": 1,
    "high_reasoning": 2,
}

CHAT_TIER_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high_reasoning": "High reasoning",
}

CHAT_EXCLUDED_MODEL_TOKENS = {
    "codex",
    "coder",
    "coding",
    "image",
    "story",
    "storytelling",
    "gemma",
    "nvidia",
    "free",
}

_CHAT_MODEL_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "models": None,
}


class LLMUpstreamTimeoutError(RuntimeError):
    """Raised when OpenRouter requests time out after retrying."""


class LLMUpstreamRetryableError(RuntimeError):
    """Raised when retryable upstream errors are exhausted."""


class LLMUpstreamBillingError(RuntimeError):
    """Raised when upstream refuses request for billing/credit reasons."""


class LLMUpstreamCompletionError(RuntimeError):
    """Raised when a streamed completion ends with an upstream generation error."""


BASE_SYSTEM_PROMPT = (
    "You are a meal parsing assistant. Given a description of a meal, "
    "extract each food item with its estimated weight in grams.\n\n"
    "Return ONLY valid JSON — no markdown, no explanation.\n\n"
    "Rules:\n"
    "- If the user specifies grams, use those exact values\n"
    "- If no weight is specified, estimate a reasonable serving size\n"
    "- Break composite foods into individual ingredients when possible\n"
    "- Keep names lowercase for consistency\n"
)

CONTEXT_PROMPT = (
    "The user has the following foods in their database. "
    "PREFER matching to these existing foods — return the matching food's id as food_id. "
    "Only set food_id to null when no existing food is a reasonable match.\n\n"
    "Known foods:\n{food_list}\n\n"
    "Return format:\n"
    '[{{"name": "food name", "amount_grams": 100, "food_id": 42}}, '
    '{{"name": "unknown item", "amount_grams": 50, "food_id": null}}, ...]\n\n'
    "food_id must be an integer id from the list above, or null."
)

NO_CONTEXT_PROMPT = (
    'Return format:\n[{{"name": "food name", "amount_grams": 100}}, ...]\n\n'
    'Use simple, common food names (e.g. "chicken breast").'
)

LABEL_OCR_SYSTEM_PROMPT = """\
You are an OCR assistant for nutrition facts labels.
Read the label in the image and return ONLY valid JSON (no markdown).

Output schema:
{
  "food_name": "string (type of food)",
  "brand": "string or null",
  "serving_size_grams": number,
  "calories_per_serving": number,
  "fat_per_serving": number,
  "saturated_fat_per_serving": number,
  "cholesterol_per_serving": number,
  "sodium_per_serving": number,
  "carbs_per_serving": number,
  "fiber_per_serving": number,
  "protein_per_serving": number
}

Rules:
- Extract values as printed for one serving.
- Use grams for fat/carbs/fiber/protein/saturated fat.
- Use milligrams for cholesterol and sodium.
- If serving size includes grams, use that exact grams value.
- If grams are missing, estimate grams from the label context.
- If a field is missing or unreadable, use 0.
- If brand cannot be identified, use null.
- Keep food_name concise and specific (for example: "greek yogurt", "granola bar").
"""


def _build_system_prompt(known_foods: list[dict] | None) -> str:
    if known_foods:
        food_list = json.dumps(
            [{"id": f["id"], "name": f["name"], "brand": f.get("brand")} for f in known_foods],
            separators=(",", ":"),
        )
        return BASE_SYSTEM_PROMPT + CONTEXT_PROMPT.format(food_list=food_list)
    return BASE_SYSTEM_PROMPT + NO_CONTEXT_PROMPT


def _strip_markdown_fences(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                continue
            chunks.append(str(part))
        return "\n".join(chunks)
    return str(content)


def _normalize_nutrition_label_payload(payload: dict[str, Any]) -> dict[str, Any]:
    food_name_raw = payload.get("food_name") or payload.get("food_type") or payload.get("name")
    food_name = str(food_name_raw).strip() if food_name_raw else ""
    if not food_name:
        food_name = "Imported food"

    brand_raw = payload.get("brand")
    brand = None
    if isinstance(brand_raw, str) and brand_raw.strip():
        brand = brand_raw.strip()

    serving_size_grams = _safe_float(payload.get("serving_size_grams"), 100)
    if serving_size_grams <= 0:
        serving_size_grams = 100

    def non_negative(value: Any) -> float:
        return max(_safe_float(value, 0), 0)

    return {
        "name": food_name,
        "brand": brand,
        "serving_size_grams": serving_size_grams,
        "calories_per_serving": non_negative(payload.get("calories_per_serving")),
        "fat_per_serving": non_negative(payload.get("fat_per_serving")),
        "saturated_fat_per_serving": non_negative(payload.get("saturated_fat_per_serving")),
        "cholesterol_per_serving": non_negative(payload.get("cholesterol_per_serving")),
        "sodium_per_serving": non_negative(payload.get("sodium_per_serving")),
        "carbs_per_serving": non_negative(payload.get("carbs_per_serving")),
        "fiber_per_serving": non_negative(payload.get("fiber_per_serving")),
        "protein_per_serving": non_negative(payload.get("protein_per_serving")),
    }


def _chat_provider_key_for_model(model_id: str) -> str | None:
    lower = model_id.lower()
    if lower.startswith("anthropic/"):
        return "anthropic"
    if lower.startswith("openai/"):
        return "openai"
    if lower.startswith("google/") or "gemini" in lower:
        return "gemini"
    if lower.startswith("qwen/") or "/qwen" in lower or "qwen" in lower:
        return "qwen"
    return None


def _model_contains_excluded_token(model_id: str, model_name: str) -> bool:
    text = f"{model_id} {model_name}".lower()
    tokens = set(re.split(r"[^a-z0-9]+", text))
    for token in CHAT_EXCLUDED_MODEL_TOKENS:
        if token in tokens:
            return True
    return False


def _cost_per_million(value: Any) -> float | None:
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


def _model_total_cost_per_million(model: dict[str, Any]) -> float:
    return float(model["input_cost_per_million"]) + float(model["output_cost_per_million"])


def _model_created_timestamp(value: Any) -> int:
    try:
        created = int(value)
    except (TypeError, ValueError):
        return 0
    if created < 0:
        return 0
    return created


def _model_headers() -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    return headers


def _string_list(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    parsed: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        parsed.add(value.lower().strip())
    return parsed


def _model_modalities(raw_model: dict[str, Any]) -> tuple[set[str], set[str]]:
    architecture = raw_model.get("architecture")
    if not isinstance(architecture, dict):
        return set(), set()
    input_modalities = _string_list(architecture.get("input_modalities"))
    output_modalities = _string_list(architecture.get("output_modalities"))
    return input_modalities, output_modalities


def _model_supported_parameters(raw_model: dict[str, Any]) -> set[str]:
    return _string_list(raw_model.get("supported_parameters"))


def _normalize_chat_model(raw_model: dict[str, Any]) -> dict[str, Any] | None:
    model_id = str(raw_model.get("id") or "").strip()
    if not model_id:
        return None

    provider_key = _chat_provider_key_for_model(model_id)
    if provider_key is None:
        return None

    model_name = str(raw_model.get("name") or model_id)
    if _model_contains_excluded_token(model_id, model_name):
        return None

    input_modalities, output_modalities = _model_modalities(raw_model)
    if "text" not in input_modalities:
        return None
    if output_modalities != {"text"}:
        return None

    supported_parameters = _model_supported_parameters(raw_model)
    if "tools" not in supported_parameters or "tool_choice" not in supported_parameters:
        return None

    pricing = raw_model.get("pricing")
    if not isinstance(pricing, dict):
        return None

    input_cost = _cost_per_million(pricing.get("prompt"))
    output_cost = _cost_per_million(pricing.get("completion"))
    if input_cost is None or output_cost is None:
        return None
    if input_cost > CHAT_MODEL_MAX_INPUT_COST_PER_MILLION:
        return None
    if output_cost > CHAT_MODEL_MAX_OUTPUT_COST_PER_MILLION:
        return None

    return {
        "id": model_id,
        "name": model_name,
        "provider": CHAT_PROVIDER_LABELS[provider_key],
        "input_cost_per_million": round(input_cost, 4),
        "output_cost_per_million": round(output_cost, 4),
        "created": _model_created_timestamp(raw_model.get("created")),
        "supports_reasoning": (
            "reasoning" in supported_parameters or "reasoning_effort" in supported_parameters
        ),
        "_provider_key": provider_key,
    }


def _choose_tier_models(provider_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not provider_models:
        return []

    sorted_by_cost = sorted(
        provider_models,
        key=lambda model: (
            _model_total_cost_per_million(model),
            -int(model["created"]),
            str(model["id"]).lower(),
        ),
    )

    low = sorted_by_cost[0]
    selected_ids = {str(low["id"])}

    reasoning_pool = [model for model in provider_models if bool(model["supports_reasoning"])]
    high_pool = reasoning_pool or provider_models
    high = next(
        (
            model
            for model in sorted(
                high_pool,
                key=lambda candidate: (
                    _model_total_cost_per_million(candidate),
                    int(candidate["created"]),
                    str(candidate["id"]).lower(),
                ),
                reverse=True,
            )
            if str(model["id"]) not in selected_ids
        ),
        None,
    )
    if high is not None:
        selected_ids.add(str(high["id"]))

    median_cost = _model_total_cost_per_million(
        sorted_by_cost[len(sorted_by_cost) // 2],
    )
    medium = next(
        (
            model
            for model in sorted(
                provider_models,
                key=lambda candidate: (
                    abs(_model_total_cost_per_million(candidate) - median_cost),
                    -int(candidate["created"]),
                    str(candidate["id"]).lower(),
                ),
            )
            if str(model["id"]) not in selected_ids
        ),
        None,
    )
    if medium is not None:
        selected_ids.add(str(medium["id"]))

    if high is None:
        high = next(
            (
                model
                for model in sorted(
                    provider_models,
                    key=lambda candidate: (
                        int(candidate["created"]),
                        _model_total_cost_per_million(candidate),
                        str(candidate["id"]).lower(),
                    ),
                    reverse=True,
                )
                if str(model["id"]) not in selected_ids
            ),
            None,
        )
        if high is not None:
            selected_ids.add(str(high["id"]))

    if medium is None:
        medium = next(
            (
                model
                for model in sorted(
                    provider_models,
                    key=lambda candidate: (
                        int(candidate["created"]),
                        _model_total_cost_per_million(candidate),
                        str(candidate["id"]).lower(),
                    ),
                    reverse=True,
                )
                if str(model["id"]) not in selected_ids
            ),
            None,
        )

    tier_pairs = [
        ("low", low),
        ("medium", medium),
        ("high_reasoning", high),
    ]
    selected_models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tier_key, model in tier_pairs:
        if model is None:
            continue
        model_id = str(model["id"])
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        selected_models.append({
            **model,
            "tier": tier_key,
            "tier_label": CHAT_TIER_LABELS[tier_key],
        })
    return selected_models


def _filter_chat_models(raw_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        model = _normalize_chat_model(raw_model)
        if model is None:
            continue
        model_id = str(model["id"])
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        candidate_models.append(model)

    latest_created_by_provider: dict[str, int] = {}
    for model in candidate_models:
        provider_key = str(model["_provider_key"])
        created = int(model["created"])
        latest_created_by_provider[provider_key] = max(
            latest_created_by_provider.get(provider_key, 0),
            created,
        )

    recent_by_provider: dict[str, list[dict[str, Any]]] = {key: [] for key in CHAT_PROVIDER_ORDER}
    for model in candidate_models:
        provider_key = str(model["_provider_key"])
        latest_created = int(latest_created_by_provider.get(provider_key, 0))
        cutoff = latest_created - CHAT_MODEL_RECENCY_WINDOW_SECONDS
        if int(model["created"]) >= cutoff:
            recent_by_provider[provider_key].append(model)

    tiered_models: list[dict[str, Any]] = []
    for provider_key in CHAT_PROVIDER_ORDER:
        provider_models = recent_by_provider.get(provider_key) or []
        if not provider_models:
            continue
        tiered_models.extend(_choose_tier_models(provider_models))

    tiered_models.sort(
        key=lambda model: (
            CHAT_PROVIDER_ORDER[str(model["_provider_key"])],
            CHAT_TIER_ORDER[str(model["tier"])],
            -int(model["created"]),
            str(model["name"]).lower(),
        ),
    )
    for model in tiered_models:
        model.pop("_provider_key", None)
        model.pop("supports_reasoning", None)
    return tiered_models


async def get_chat_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return tiered latest-generation tool-use chat models for target providers."""
    if not force_refresh:
        cached_models = _CHAT_MODEL_CACHE.get("models")
        expires_at = float(_CHAT_MODEL_CACHE.get("expires_at") or 0.0)
        if cached_models and time.monotonic() < expires_at:
            return list(cached_models)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                OPENROUTER_MODELS_URL,
                headers=_model_headers(),
            )
            logger.info("Chat model list response status: %s", resp.status_code)
            if resp.status_code != 200:
                logger.error("Chat model list response body: %s", resp.text[:2000])
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        cached_models = _CHAT_MODEL_CACHE.get("models")
        if cached_models:
            logger.warning("Model list refresh failed; serving cached model list")
            return list(cached_models)
        raise

    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        raise ValueError("OpenRouter model list payload is invalid")

    filtered_models = _filter_chat_models(raw_models)

    _CHAT_MODEL_CACHE["models"] = filtered_models
    _CHAT_MODEL_CACHE["expires_at"] = time.monotonic() + CHAT_MODEL_CACHE_TTL_SECONDS
    return list(filtered_models)


async def parse_meal_description(
    description: str,
    known_foods: list[dict] | None = None,
) -> list[dict]:
    """Parse a natural language meal description into structured food items.

    When known_foods is provided, the LLM will attempt to match items to existing
    foods by returning food_id in the response.
    """
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    system_prompt = _build_system_prompt(known_foods)
    logger.info("Parsing meal description: %s", description)
    if known_foods:
        logger.info("Providing %d known foods to LLM", len(known_foods))

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": description},
                ],
                "temperature": 0,
            },
            timeout=30.0,
        )
        logger.info("LLM response status: %s", resp.status_code)
        if resp.status_code != 200:
            logger.error("LLM response body: %s", resp.text[:2000])
        resp.raise_for_status()
        data = resp.json()
        content = _message_content_to_text(data["choices"][0]["message"]["content"])
        logger.info("LLM raw content: %s", content)
        content = _strip_markdown_fences(content)

        items = json.loads(content)
        if not isinstance(items, list):
            raise ValueError("LLM did not return a list")

        parsed = []
        for item in items:
            if "name" not in item or "amount_grams" not in item:
                continue
            entry: dict = {
                "name": str(item["name"]).lower().strip(),
                "amount_grams": float(item["amount_grams"]),
            }
            if item.get("food_id") is not None:
                entry["food_id"] = int(item["food_id"])
            parsed.append(entry)

        logger.info("Parsed items: %s", parsed)
        return parsed


async def parse_nutrition_label_image(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Extract nutrition facts from a label image via OpenRouter multimodal OCR."""
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")
    if not image_bytes:
        raise ValueError("Image is empty")

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    image_url = f"data:{mime_type};base64,{image_b64}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": VISION_MODEL,
                "messages": [
                    {"role": "system", "content": LABEL_OCR_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extract nutrition facts from this label image.",
                            },
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
                "temperature": 0,
            },
            timeout=45.0,
        )
        logger.info("Label OCR response status: %s", resp.status_code)
        if resp.status_code != 200:
            logger.error("Label OCR response body: %s", resp.text[:2000])
        resp.raise_for_status()
        data = resp.json()
        content = _message_content_to_text(data["choices"][0]["message"]["content"])
        logger.info("Label OCR raw content: %s", content)

    content = _strip_markdown_fences(content)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Could not parse nutrition label") from exc
    if not isinstance(payload, dict):
        raise ValueError("Label OCR did not return an object")
    return _normalize_nutrition_label_payload(payload)


# --- Conversational chat for meal logging ---

CHAT_RUNTIME_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "chat_runtime_context",
    default=None,
)
CHAT_STATUS_CALLBACK: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "chat_status_callback",
    default=None,
)


@contextmanager
def chat_runtime_context(context: dict[str, str] | None):
    token = CHAT_RUNTIME_CONTEXT.set(context)
    try:
        yield
    finally:
        CHAT_RUNTIME_CONTEXT.reset(token)


@contextmanager
def chat_status_callback(callback: Callable[[dict[str, Any]], None] | None):
    token = CHAT_STATUS_CALLBACK.set(callback)
    try:
        yield
    finally:
        CHAT_STATUS_CALLBACK.reset(token)


def _emit_chat_status(event: dict[str, Any]) -> None:
    callback = CHAT_STATUS_CALLBACK.get()
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        logger.exception("chat status callback failed")

CHAT_SYSTEM_PROMPT = """\
You are a database-backed nutrition and workout assistant.

## Tool system
The tool system is table-driven:
- To read records, call get_<table>.
- To create, update, upsert, or delete records, call set_<table>.
- Every getter and setter supports single-record and bulk use.
- Every getter can search by direct fields or relations.
- Getters can use fuzzy matching on human-facing names.
- Setters use exact IDs resolved from a prior getter call.
- Relation tables do not have direct tools. Mutate them through the parent table.

Use getters before relying on existing IDs or existing state.
Before writing any relation, first call the relevant getter to resolve the exact \
foreign-key ID.
Use fuzzy matching in getters. Use exact IDs in setters.
When changing nested relations on editable tables, prefer replacing the full \
current child list unless the user clearly asked for an incremental add or remove.
For append-only log tables, append a new timestamped snapshot instead of \
replacing prior rows.
If a fuzzy match is ambiguous, ask a short disambiguating question instead of \
guessing.
After every setter call, inspect the returned full tree to verify that the write \
matches the user intent.

## Local date/time context
{time_context}
- For new meal logging, avoid asking for date or meal type when this context \
and the message already make it clear.

## Logging new meals
When the user describes what they ate and wants to log a NEW meal:
- Respond naturally and conversationally. Confirm your understanding with specific \
foods, brands, and gram amounts.
- Include a structured breakdown in this XML block:
  <ITEMS>[{{"food_id": 42, "name": "food name", "amount_grams": 60}}, ...]</ITEMS>
- Items can reference a food (food_id) OR a recipe (recipe_id), not both:
  {{"food_id": 42, "name": "chicken breast", "amount_grams": 200}}
  {{"recipe_id": 5, "name": "yogurt and granola breakfast", "amount_grams": 350}}
- food_id/recipe_id must be an integer from the known lists, or null if no match.
- Always include the <ITEMS> block in your first response and whenever you update it.
- If the user says "yes", "looks good", "save it", "confirm", or similar \
affirmation, include <CONFIRM/> in your response.
- Do NOT include <CONFIRM/> unless the user has explicitly confirmed.
- Estimate reasonable gram weights when the user doesn't specify.
- Use the food/recipe names from the database (not generic names) when matched.
- IMPORTANT: Recipes are composite meals (e.g. "yogurt and granola breakfast"). \
When a user refers to a recipe name, use recipe_id — do NOT substitute individual \
ingredient foods.

## Querying and editing the food log
- Use get_meal_logs to query meals by date, meal type, or food/recipe name.
- Use set_meal_logs to create, update (including move date), or delete meals.
- Use set_meal_logs with relations.items to add/remove/replace meal items.
On EVERY interaction:
- Call get_meal_logs before relying on any existing meal facts, totals, or IDs.
- Use the current local date from context when no date is specified.
- If the user references a different date, call get_meal_logs for that date.
- Treat earlier chat messages as potentially stale; rely on fresh tool output.
When modifying meals via tools, briefly confirm what you changed.
Do NOT use <ITEMS>/<CONFIRM/> tags when using tools to edit existing data — \
just use the tools directly.

## Food management
- Use get_foods to search and resolve food IDs before logging meals.
- Use set_foods to create foods from nutrition labels or manual entry.

## Macro target management
- Use get_macro_targets and set_macro_targets for daily target history.
- If the user does not specify a date, use the current local date from context.
- Do NOT use <ITEMS>/<CONFIRM/> tags for macro-target changes.

## Weight logging
- Use get_weight_logs and set_weight_logs for body-weight history.
- If the user gives a bare number with no unit, assume pounds.
- Convert kilograms to pounds before calling the tool when needed.
- Do NOT use <ITEMS>/<CONFIRM/> tags for weight logging.

## Recipe management
- Use get_recipes and set_recipes for recipe definitions.
- When a user refers to a recipe, keep it as a recipe record instead of \
expanding it into ingredient foods unless they explicitly ask to edit the recipe.

## Nutrition label scanning
When the user scans a nutrition label, the OCR results are sent to you for \
verification. You MUST:
1. Present the detected name, brand, and all macros clearly to the user
2. Ask if everything looks correct or if they want to change anything
3. Only call set_foods AFTER the user confirms (or provides corrections)
4. Then log the meal using the newly created food's id
Do NOT save the food without user verification first.

## Function calling
- When using tools, output tool calls only. Do not generate Python, pseudo-code, \
markdown, or XML instead of a tool call.
- Call the tool name exactly as defined. Never prepend namespaces.
- Tool arguments must be valid JSON that matches the provided schema.
- Array fields like "changes" must be actual JSON arrays, not stringified JSON.
- Prefer one tool call at a time unless a batch tool explicitly expects a list.
- After tool results are returned, either call the next tool or answer normally.

{food_context}
{meals_context}

## Workout tracking
KNOWN EXERCISES: {exercise_list}
CURRENT ROUTINE:
{routine_summary}
CURRENT TISSUE CONDITIONS:
{conditions_text}

WORKOUT LOGGING:
- Use set_workout_sessions to log strength sessions with sets, reps, and weights.
- Parse natural language: "incline DB press 3x10 at 45", "leg press 430x5x3", etc.
- After logging, if the result includes rep_check data, emit a \
<REP_CHECK exercises='[...]'/> tag so the frontend shows the rep completion widget.
- Use set_workout_sessions to update rep completion on existing sessions.
- Always record actual reps per set for accurate volume calculations.

READINESS & SUGGESTIONS:
- Use get_tissues with include=["readiness"] to check what is ready to train.
- Use get_routine_exercises to see the planned routine with last performance.
- Show which exercises are available and which are excluded (and why).
- Include rehab work for any tissues in tender/rehabbing status.

INJURY AWARENESS:
- Use set_tissue_conditions when the user reports pain, tenderness, or injury.
- Ask about severity and when it started.
- Follow the injury state machine: healthy -> tender -> injured -> rehabbing -> healthy.
- Use get_tissue_conditions before making recovery or injury claims.

PROGRESSIVE OVERLOAD:
- Use get_exercises with include=["stats"] for progression suggestions.
- 2+ consecutive "full" sessions -> suggest weight increase.
- "failed" -> check tissue conditions, suggest deload or form adjustment.

TISSUE MAPPING CONVENTIONS:
- loading_factor is 0.0–1.0 (fraction of load, NOT a percentage).
- role=primary for main movers, role=secondary for synergists and tendons, \
role=stabilizer for joints.
- Include all relevant tissues: muscles, tendons (e.g. patellar, achilles), \
and joints (e.g. knee, hip, shoulder).
- For isometric bracing (e.g. grip on a crunch machine), use secondary with a \
low loading_factor (0.1–0.2).

DATA IMPORT:
- The user may paste spreadsheet data for historical workout import.
- Parse the data, create exercises and sessions, and assign tissue mappings.
- Always do a dry run summary first before committing.\
"""


def _build_chat_system_prompt(
    known_foods: list[dict] | None,
    known_recipes: list[dict] | None = None,
    recent_meals: list[dict] | None = None,
    runtime_context: dict[str, str] | None = None,
    workout_context: dict[str, str] | None = None,
) -> str:
    if runtime_context:
        time_context = (
            f"- Current local datetime: {runtime_context['client_local_datetime']}\n"
            f"- Current local date: {runtime_context['client_local_date']}\n"
            f"- Time zone: {runtime_context['client_timezone']}\n"
            f"- Default meal type by time: {runtime_context['default_meal_type']}"
        )
    else:
        time_context = "- Local date/time context not provided."

    food_context = ""
    if known_foods:
        food_list = json.dumps(
            [{"id": f["id"], "name": f["name"], "brand": f.get("brand")} for f in known_foods],
            separators=(",", ":"),
        )
        food_context = (
            "Known foods in the database (use their id as food_id):\n"
            f"{food_list}"
        )
    if known_recipes:
        recipe_list = json.dumps(
            [{"id": r["id"], "name": r["name"]} for r in known_recipes],
            separators=(",", ":"),
        )
        food_context += (
            "\n\nKnown recipes (use their id as recipe_id):\n"
            f"{recipe_list}"
        )

    if recent_meals:
        from collections import defaultdict
        by_date: dict[str, list] = defaultdict(list)
        for meal in recent_meals:
            by_date[meal.get("date", "unknown")].append(meal)

        lines = ["Logged meals (recent days):"]
        for date_str in sorted(by_date.keys()):
            lines.append(f"\n{date_str}:")
            for meal in by_date[date_str]:
                items_str = ", ".join(
                    f"{it['name']} {it['grams']}g" for it in meal["items"]
                )
                lines.append(
                    f"  - Meal #{meal['id']} ({meal['meal_type']}): "
                    f"{items_str} [{meal['total_calories']} kcal]"
                )
        meals_context = "\n".join(lines)
    else:
        meals_context = ""

    wctx = workout_context or {}
    return CHAT_SYSTEM_PROMPT.format(
        time_context=time_context,
        food_context=food_context,
        meals_context=meals_context,
        exercise_list=wctx.get("exercise_list", "[]"),
        routine_summary=wctx.get("routine_summary", "  (no routine set)"),
        conditions_text=wctx.get("conditions_text", "  All tissues healthy."),
    )


# Tool definitions and selection are now in llm_tools package
from app.llm_tools import ALL_TOOL_DEFINITIONS, select_tools  # noqa: E402


def _all_chat_tools() -> list[dict]:
    return ALL_TOOL_DEFINITIONS


def _select_chat_tools(messages: list[dict]) -> list[dict]:
    return select_tools(messages)


def _is_reasoning_model(model_id: str) -> bool:
    lower = model_id.lower()
    reasoning_indicators = ("thinking", "reasoning", "preview")
    return any(ind in lower for ind in reasoning_indicators)


def _chat_max_tokens_for_model(model_id: str) -> int:
    if _is_reasoning_model(model_id):
        return OPENROUTER_CHAT_MAX_TOKENS_REASONING
    return OPENROUTER_CHAT_MAX_TOKENS


def _chat_temperature_for_model(model_id: str) -> float:
    # Gemini 3 docs recommend leaving temperature at the model default (1.0).
    if _chat_provider_key_for_model(model_id) == "gemini":
        return 1.0
    return 0.3


def _forced_tool_choice(tools: list[dict]) -> str | dict[str, Any]:
    if len(tools) == 1:
        return {
            "type": "function",
            "function": {"name": tools[0]["function"]["name"]},
        }
    return "required"


def _build_chat_completion_payload(
    *,
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    force_tool_choice: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "temperature": _chat_temperature_for_model(model_id),
        "max_tokens": _chat_max_tokens_for_model(model_id),
    }
    if tools:
        payload["tools"] = tools
        payload["parallel_tool_calls"] = False
        if force_tool_choice:
            payload["tool_choice"] = _forced_tool_choice(tools)
    return payload


async def _post_openrouter_chat_completion(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> httpx.Response:
    raise NotImplementedError("Use _stream_openrouter_chat_completion for chat requests")


def _openrouter_error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return fallback


def _merge_tool_call_delta(
    tool_calls_by_index: dict[int, dict[str, Any]],
    delta_tool_calls: Any,
) -> None:
    if not isinstance(delta_tool_calls, list):
        return
    for entry in delta_tool_calls:
        if not isinstance(entry, dict):
            continue
        index_raw = entry.get("index", 0)
        try:
            index = int(index_raw)
        except (TypeError, ValueError):
            index = len(tool_calls_by_index)
        tool_call = tool_calls_by_index.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {
                    "name": "",
                    "arguments": "",
                },
            },
        )
        tc_id = entry.get("id")
        if isinstance(tc_id, str) and tc_id:
            tool_call["id"] = tc_id
        function = entry.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            tool_call["function"]["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            tool_call["function"]["arguments"] += arguments


def _finalize_tool_calls(tool_calls_by_index: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for index in sorted(tool_calls_by_index):
        tool_call = tool_calls_by_index[index]
        tc_id = str(tool_call.get("id") or f"tool_call_{index}")
        function = tool_call.get("function")
        if not isinstance(function, dict):
            function = {"name": "unknown_tool", "arguments": ""}
        name = str(function.get("name") or "unknown_tool")
        arguments = str(function.get("arguments") or "")
        finalized.append({
            "id": tc_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        })
    return finalized


async def _stream_openrouter_chat_completion(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    model_id: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    effective_model = model_id or payload.get("model", "")
    idle_timeout = (
        OPENROUTER_STREAM_IDLE_TIMEOUT_REASONING_SECONDS
        if _is_reasoning_model(str(effective_model))
        else OPENROUTER_STREAM_IDLE_TIMEOUT_SECONDS
    )
    stream_payload = {**payload, "stream": True}
    last_error: Exception | None = None
    max_attempts = OPENROUTER_CHAT_MAX_RETRIES + 1

    for attempt in range(max_attempts):
        _emit_chat_status({
            "event": "upstream_request_started",
            "attempt": attempt + 1,
            "max_attempts": max_attempts,
        })
        try:
            async with client.stream(
                "POST",
                OPENROUTER_URL,
                headers=headers,
                json=stream_payload,
            ) as resp:
                request_id = (
                    resp.headers.get("x-request-id")
                    or resp.headers.get("request-id")
                    or resp.headers.get("x-openrouter-request-id")
                )
                cf_ray = resp.headers.get("cf-ray")
                _emit_chat_status({
                    "event": "upstream_response_received",
                    "status_code": resp.status_code,
                    "attempt": attempt + 1,
                    "openrouter_request_id": request_id,
                    "cf_ray": cf_ray,
                })
                logger.info("Chat LLM response status: %s", resp.status_code)

                if resp.status_code != 200:
                    body_bytes = await resp.aread()
                    body_text = body_bytes.decode("utf-8", errors="replace")
                    body_preview = body_text[:2000]
                    body_json: Any | None = None
                    try:
                        body_json = json.loads(body_text)
                    except json.JSONDecodeError:
                        body_json = None

                    if resp.status_code == 402:
                        detail = _openrouter_error_message(
                            body_json,
                            "Model request exceeds current provider credit limit",
                        )
                        logger.error("Billing-limited OpenRouter response body: %s", body_preview)
                        _emit_chat_status({
                            "event": "upstream_billing_limited",
                            "status_code": resp.status_code,
                            "attempt": attempt + 1,
                        })
                        raise LLMUpstreamBillingError(detail)

                    if resp.status_code in OPENROUTER_RETRYABLE_STATUS_CODES:
                        logger.warning(
                            "Retryable OpenRouter error status=%s attempt=%d/%d body=%s",
                            resp.status_code,
                            attempt + 1,
                            max_attempts,
                            body_preview[:500],
                        )
                        _emit_chat_status({
                            "event": "upstream_retryable_status",
                            "status_code": resp.status_code,
                            "attempt": attempt + 1,
                        })
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(
                                OPENROUTER_RETRY_BASE_DELAY_SECONDS * (2**attempt),
                            )
                            continue
                        last_error = LLMUpstreamRetryableError(
                            f"OpenRouter returned {resp.status_code} after retries",
                        )
                        break

                    logger.error("Chat LLM response body: %s", body_preview)
                    resp.raise_for_status()

                completion_id: str | None = None
                finish_reason: str | None = None
                native_finish_reason: str | None = None
                stream_error_code: str | int | None = None
                stream_error_message: str | None = None
                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                tool_calls_by_index: dict[int, dict[str, Any]] = {}

                line_count = 0
                line_iter = resp.aiter_lines().__aiter__()
                while True:
                    try:
                        raw_line = await asyncio.wait_for(
                            line_iter.__anext__(),
                            timeout=idle_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        logger.error(
                            "Stream idle timeout after %ds (line %d, finish_reason=%s)",
                            idle_timeout,
                            line_count,
                            finish_reason,
                        )
                        _emit_chat_status({
                            "event": "upstream_stream_idle_timeout",
                            "attempt": attempt + 1,
                            "line_count": line_count,
                        })
                        break
                    line = (raw_line or "").strip()
                    line_count += 1
                    if not line:
                        continue
                    _emit_chat_status({
                        "event": "upstream_raw_line",
                        "attempt": attempt + 1,
                        "line_num": line_count,
                        "stream_line": line[:200],
                    })
                    if line.startswith(":"):
                        _emit_chat_status({
                            "event": "upstream_keepalive_comment",
                            "attempt": attempt + 1,
                            "comment": line[1:].strip(),
                        })
                        continue
                    if not line.startswith("data:"):
                        logger.debug(
                            "Stream line %d not SSE data: %s",
                            line_count, line[:200],
                        )
                        continue

                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        _emit_chat_status({
                            "event": "upstream_stream_done",
                            "attempt": attempt + 1,
                        })
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Could not parse OpenRouter stream chunk: %s",
                            data_str[:300],
                        )
                        continue

                    if not isinstance(chunk, dict):
                        continue

                    chunk_error = chunk.get("error")
                    if isinstance(chunk_error, dict):
                        code = chunk_error.get("code")
                        message = chunk_error.get("message")
                        if isinstance(code, (str, int)):
                            stream_error_code = code
                        if isinstance(message, str) and message.strip():
                            stream_error_message = message.strip()
                        error_code_value = (
                            str(stream_error_code) if stream_error_code is not None else None
                        )
                        _emit_chat_status({
                            "event": "upstream_stream_error_chunk",
                            "attempt": attempt + 1,
                            "error_code": error_code_value,
                        })

                    if completion_id is None:
                        chunk_id = chunk.get("id")
                        if isinstance(chunk_id, str) and chunk_id:
                            completion_id = chunk_id
                            _emit_chat_status({
                                "event": "upstream_completion_id",
                                "attempt": attempt + 1,
                                "openrouter_completion_id": completion_id,
                            })

                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        continue

                    delta = choice.get("delta")
                    if isinstance(delta, dict):
                        # Reasoning / thinking content
                        reasoning_text = _message_content_to_text(
                            delta.get("reasoning")
                        )
                        if not reasoning_text:
                            rd = delta.get("reasoning_details")
                            if isinstance(rd, list):
                                parts = []
                                for item in rd:
                                    if isinstance(item, dict):
                                        parts.append(
                                            str(item.get("text") or "")
                                        )
                                reasoning_text = "".join(parts)
                        if reasoning_text:
                            reasoning_parts.append(reasoning_text)
                            _emit_chat_status({
                                "event": "upstream_reasoning_chunk",
                                "attempt": attempt + 1,
                                "text": reasoning_text,
                            })

                        # Content text
                        text_delta = _message_content_to_text(
                            delta.get("content")
                        )
                        if text_delta:
                            content_parts.append(text_delta)
                            _emit_chat_status({
                                "event": "upstream_content_chunk",
                                "attempt": attempt + 1,
                                "text": text_delta,
                            })

                        # Tool call deltas
                        delta_tc = delta.get("tool_calls")
                        if delta_tc:
                            logger.info(
                                "Stream tool_call delta (line %d): %s",
                                line_count,
                                json.dumps(delta_tc, default=str)[:500],
                            )
                        _merge_tool_call_delta(
                            tool_calls_by_index, delta_tc,
                        )

                    # Also check non-delta "message" (some providers)
                    chunk_message = choice.get("message")
                    if isinstance(chunk_message, dict):
                        text_msg = _message_content_to_text(
                            chunk_message.get("content")
                        )
                        if text_msg:
                            content_parts.append(text_msg)
                            _emit_chat_status({
                                "event": "upstream_content_chunk",
                                "attempt": attempt + 1,
                                "text": text_msg,
                            })
                        msg_tc = chunk_message.get("tool_calls")
                        if msg_tc:
                            logger.info(
                                "Stream message.tool_calls (line %d): %s",
                                line_count,
                                json.dumps(msg_tc, default=str)[:500],
                            )
                            _merge_tool_call_delta(
                                tool_calls_by_index, msg_tc,
                            )

                    fr = choice.get("finish_reason")
                    if isinstance(fr, str) and fr:
                        finish_reason = fr
                        _emit_chat_status({
                            "event": "upstream_finish_reason",
                            "attempt": attempt + 1,
                            "finish_reason": finish_reason,
                        })
                        # Shorten idle timeout — [DONE] should arrive within
                        # seconds after finish_reason.  Avoids breaking early
                        # and leaving [DONE] unread, which causes httpx's
                        # aclose() to drain with no timeout and hang.
                        idle_timeout = min(idle_timeout, 10.0)
                    native_fr = choice.get("native_finish_reason")
                    if not isinstance(native_fr, str) or not native_fr:
                        native_fr = chunk.get("native_finish_reason")
                    if isinstance(native_fr, str) and native_fr:
                        native_finish_reason = native_fr

                if finish_reason is None:
                    # Stream exhausted without finish_reason or [DONE]
                    logger.warning(
                        "Stream ended without finish_reason or [DONE] after %d lines",
                        line_count,
                    )
                    _emit_chat_status({
                        "event": "upstream_stream_exhausted",
                        "attempt": attempt + 1,
                        "line_count": line_count,
                    })

                message: dict[str, Any] = {"content": "".join(content_parts).strip()}
                tool_calls = _finalize_tool_calls(tool_calls_by_index)
                if tool_calls:
                    message["tool_calls"] = tool_calls

                if finish_reason == "error":
                    detail_parts = ["OpenRouter stream ended with finish_reason=error"]
                    if native_finish_reason:
                        detail_parts.append(f"native_finish_reason={native_finish_reason}")
                    if stream_error_code is not None:
                        detail_parts.append(f"code={stream_error_code}")
                    if stream_error_message:
                        detail_parts.append(f"message={stream_error_message}")
                    detail = "; ".join(detail_parts)
                    logger.error("Chat LLM mid-stream generation error: %s", detail)
                    error_code_value = (
                        str(stream_error_code) if stream_error_code is not None else None
                    )
                    _emit_chat_status({
                        "event": "upstream_generation_error",
                        "attempt": attempt + 1,
                        "native_finish_reason": native_finish_reason,
                        "error_code": error_code_value,
                    })
                    raise LLMUpstreamCompletionError(detail)

                if finish_reason == "length":
                    detail_parts = [
                        "Model hit max_tokens limit",
                        f"native_finish_reason={native_finish_reason or 'MAX_TOKENS'}",
                    ]
                    detail = "; ".join(detail_parts)
                    logger.error("Chat LLM hit token limit: %s", detail)
                    _emit_chat_status({
                        "event": "upstream_generation_error",
                        "attempt": attempt + 1,
                        "native_finish_reason": native_finish_reason,
                        "error_code": "length",
                    })
                    raise LLMUpstreamCompletionError(detail)

                if not message["content"] and not tool_calls:
                    detail_parts = ["OpenRouter stream ended without content or tool calls"]
                    if finish_reason:
                        detail_parts.append(f"finish_reason={finish_reason}")
                    if native_finish_reason:
                        detail_parts.append(f"native_finish_reason={native_finish_reason}")
                    if stream_error_message:
                        detail_parts.append(f"message={stream_error_message}")
                    detail_parts.append(f"lines_parsed={line_count}")
                    detail_parts.append(f"content_parts={len(content_parts)}")
                    detail_parts.append(f"tool_call_indices={list(tool_calls_by_index.keys())}")
                    detail = "; ".join(detail_parts)
                    logger.error("Chat LLM empty terminal response: %s", detail)
                    _emit_chat_status({
                        "event": "upstream_empty_terminal_response",
                        "attempt": attempt + 1,
                    })
                    raise LLMUpstreamCompletionError(detail)

                return {
                    "id": completion_id,
                    "choices": [
                        {
                            "message": message,
                            "finish_reason": finish_reason,
                        },
                    ],
                }
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                "OpenRouter request transport error on attempt=%d/%d: %s",
                attempt + 1,
                max_attempts,
                exc,
            )
            _emit_chat_status({
                "event": "upstream_transport_error",
                "attempt": attempt + 1,
                "error": str(exc),
            })
            if attempt < max_attempts - 1:
                await asyncio.sleep(
                    OPENROUTER_RETRY_BASE_DELAY_SECONDS * (2**attempt),
                )
                continue
            if isinstance(exc, httpx.TimeoutException):
                last_error = LLMUpstreamTimeoutError(
                    f"OpenRouter request timed out after {max_attempts} attempts",
                )
            else:
                last_error = LLMUpstreamRetryableError(
                    "OpenRouter network error after retries",
                )
            break

    if last_error is not None:
        raise last_error
    raise LLMUpstreamRetryableError("OpenRouter request failed with unknown retry state")


async def chat_meal(
    messages: list[dict],
    known_foods: list[dict] | None = None,
    known_recipes: list[dict] | None = None,
    recent_meals: list[dict] | None = None,
    tool_executor: Callable[[str, dict], Awaitable[Any]] | None = None,
    model: str | None = None,
    workout_context: dict[str, str] | None = None,
) -> str:
    """Multi-turn conversational meal chat with optional tool use.

    Returns raw LLM text response. When tool_executor is provided, the LLM
    can call tools to query/modify the food log and will loop until it
    produces a final text response.
    """
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    system_prompt = _build_chat_system_prompt(
        known_foods,
        known_recipes,
        recent_meals,
        runtime_context=CHAT_RUNTIME_CONTEXT.get(),
        workout_context=workout_context,
    )
    active_model = model or MODEL
    logger.info(
        "Chat meal: %d messages, %d known foods, %d recipes, model=%s",
        len(messages), len(known_foods or []), len(known_recipes or []), active_model,
    )

    all_messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *messages,
    ]

    max_rounds = 10
    async with httpx.AsyncClient(timeout=None) as client:
        for _round in range(max_rounds):
            round_index = _round + 1
            selected_tools = _select_chat_tools(all_messages) if tool_executor else None
            _emit_chat_status({
                "event": "round_started",
                "round": round_index,
                "max_rounds": max_rounds,
            })
            payload = _build_chat_completion_payload(
                model_id=active_model,
                messages=all_messages,
                tools=selected_tools,
            )
            try:
                data = await _stream_openrouter_chat_completion(client, payload, active_model)
            except LLMUpstreamCompletionError:
                should_force_tool_retry = (
                    _chat_provider_key_for_model(active_model) == "gemini"
                    and selected_tools is not None
                    and bool(selected_tools)
                    and bool(select_tools(all_messages))
                    and not any(message.get("role") == "tool" for message in all_messages)
                )
                if not should_force_tool_retry:
                    raise
                _emit_chat_status({
                    "event": "gemini_forced_tool_retry",
                    "round": round_index,
                    "tool_count": len(selected_tools),
                })
                logger.warning(
                    "Retrying Gemini chat round=%d with forced tool choice after generation error",
                    round_index,
                )
                forced_payload = _build_chat_completion_payload(
                    model_id=active_model,
                    messages=all_messages,
                    tools=selected_tools,
                    force_tool_choice=True,
                )
                data = await _stream_openrouter_chat_completion(
                    client, forced_payload, active_model,
                )
            completion_id = data.get("id")
            if isinstance(completion_id, str) and completion_id:
                _emit_chat_status({
                    "event": "upstream_completion_id",
                    "round": round_index,
                    "openrouter_completion_id": completion_id,
                })
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason:
                _emit_chat_status({
                    "event": "upstream_round_complete",
                    "round": round_index,
                    "finish_reason": finish_reason,
                })
            message = choice["message"]

            tool_calls = message.get("tool_calls")
            if tool_calls and tool_executor:
                logger.info("LLM requested %d tool call(s)", len(tool_calls))
                _emit_chat_status({
                    "event": "tool_calls_received",
                    "round": round_index,
                    "tool_call_count": len(tool_calls),
                })
                assistant_tool_message = {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
                all_messages.append(assistant_tool_message)
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        func_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError as exc:
                        detail = (
                            f"Tool call arguments were not valid JSON for {func_name}: {exc.msg}"
                        )
                        logger.error(detail)
                        _emit_chat_status({
                            "event": "tool_call_arguments_invalid",
                            "round": round_index,
                            "tool_name": func_name,
                        })
                        raise LLMUpstreamCompletionError(detail) from exc
                    # LLMs sometimes send array fields as JSON strings;
                    # coerce them back to lists so handlers don't break.
                    for key, val in func_args.items():
                        if isinstance(val, str) and val.lstrip().startswith("["):
                            try:
                                func_args[key] = json.loads(val)
                            except json.JSONDecodeError:
                                pass
                    logger.info("Executing tool: %s(%s)", func_name, func_args)
                    _emit_chat_status({
                        "event": "tool_call_started",
                        "round": round_index,
                        "tool_name": func_name,
                        "tool_args": json.dumps(
                            func_args, default=str,
                        )[:2000],
                    })
                    try:
                        result = await tool_executor(func_name, func_args)
                    except Exception as exc:
                        logger.exception("Tool execution failed: %s", func_name)
                        result = {"error": str(exc)}
                        _emit_chat_status({
                            "event": "tool_call_failed",
                            "round": round_index,
                            "tool_name": func_name,
                            "error": str(exc),
                        })
                    else:
                        _emit_chat_status({
                            "event": "tool_call_completed",
                            "round": round_index,
                            "tool_name": func_name,
                            "tool_result": json.dumps(
                                result, default=str,
                            )[:2000],
                        })
                    all_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str),
                    })
                continue  # next round — let LLM see tool results

            content = message.get("content") or ""
            logger.info("Chat LLM raw content: %s", content)
            _emit_chat_status({
                "event": "final_response_received",
                "round": round_index,
                "content_chars": len(str(content)),
            })
            return content.strip()

    logger.warning("Chat meal exceeded max tool rounds")
    return "I had trouble processing that request. Please try again."
