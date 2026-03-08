"""OpenRouter LLM client for parsing meal descriptions."""

import base64
import json
import logging
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
CHAT_MODEL_MAX_INPUT_COST_PER_MILLION = 1.0
CHAT_MODEL_MAX_OUTPUT_COST_PER_MILLION = 2.0
CHAT_MODEL_CACHE_TTL_SECONDS = 600.0

CHAT_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "nvidia": "NVIDIA",
    "bytedance": "Bytedance",
    "qwen": "Qwen",
}

CHAT_PROVIDER_ORDER = {
    "anthropic": 0,
    "gemini": 1,
    "nvidia": 2,
    "bytedance": 3,
    "qwen": 4,
}

_CHAT_MODEL_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "models": None,
}

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
    if lower.startswith("google/") or "gemini" in lower:
        return "gemini"
    if lower.startswith("nvidia/"):
        return "nvidia"
    if lower.startswith("bytedance/") or "doubao" in lower:
        return "bytedance"
    if lower.startswith("qwen/") or "/qwen" in lower or "qwen" in lower:
        return "qwen"
    return None


def _cost_per_million(value: Any) -> float | None:
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


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


async def get_chat_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return affordable OpenRouter chat models for approved provider families."""
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

    filtered_models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id") or "").strip()
        if not model_id or model_id in seen_ids:
            continue
        provider_key = _chat_provider_key_for_model(model_id)
        if provider_key is None:
            continue

        pricing = raw.get("pricing")
        if not isinstance(pricing, dict):
            continue

        input_cost = _cost_per_million(pricing.get("prompt"))
        output_cost = _cost_per_million(pricing.get("completion"))
        if input_cost is None or output_cost is None:
            continue
        if input_cost > CHAT_MODEL_MAX_INPUT_COST_PER_MILLION:
            continue
        if output_cost > CHAT_MODEL_MAX_OUTPUT_COST_PER_MILLION:
            continue

        seen_ids.add(model_id)
        filtered_models.append({
            "id": model_id,
            "name": str(raw.get("name") or model_id),
            "provider": CHAT_PROVIDER_LABELS[provider_key],
            "input_cost_per_million": round(input_cost, 4),
            "output_cost_per_million": round(output_cost, 4),
            "created": _model_created_timestamp(raw.get("created")),
            "_provider_key": provider_key,
        })

    filtered_models.sort(
        key=lambda m: (
            -int(m["created"]),
            CHAT_PROVIDER_ORDER[m["_provider_key"]],
            str(m["name"]).lower(),
        ),
    )
    for model in filtered_models:
        model.pop("_provider_key", None)

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


@contextmanager
def chat_runtime_context(context: dict[str, str] | None):
    token = CHAT_RUNTIME_CONTEXT.set(context)
    try:
        yield
    finally:
        CHAT_RUNTIME_CONTEXT.reset(token)

CHAT_SYSTEM_PROMPT = """\
You are a friendly meal-logging assistant. Help the user log what they ate and \
manage their food log.

## Local date/time context
Use the user's local context for default assumptions unless they explicitly ask \
for a different date/time/meal type.
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
You have tools to query and modify the food log database. Use them when:
- The user asks what they ate on any date (use query_food_log)
- The user wants to move meals between dates (use move_meal_to_date)
- The user wants to add/remove items from existing meals (use add_item_to_meal, \
delete_meal_item)
- The user wants to create or delete entire meals (use create_meal, delete_meal)

On EVERY interaction:
- Call query_food_log before relying on any existing meal facts, totals, or IDs.
- Use the current local date from context when no date is specified.
- If the user references a different date, call query_food_log for that date.
- Treat earlier chat messages as potentially stale; rely on fresh tool output.

When modifying meals via tools, briefly confirm what you changed.
Do NOT use <ITEMS>/<CONFIRM/> tags when using tools to edit existing data — \
just use the tools directly.

## Macro target management
You can set daily macro targets that apply from their day until the next target.
- Use set_macro_target when the user asks to set or update daily targets.
- If the user does not specify a date, use the current local date from context.
- When updating a day, unspecified fields should remain unchanged.
- Do NOT use <ITEMS>/<CONFIRM/> tags for macro-target changes.
- After saving, confirm which day and targets were recorded.

## Weight logging
You also have a tool to log body weight. Use it when the user asks to record or \
log their weight.
- Call log_weight directly once you know the number.
- If the user gives a bare number with no unit, assume pounds.
- Convert kilograms to pounds before calling the tool when needed.
- If the user does not specify a time, omit logged_at so the server uses the \
current timestamp.
- Do NOT use <ITEMS>/<CONFIRM/> tags for weight logging.
- After logging, confirm the recorded weight in pounds.

## Nutrition label scanning
When the user scans a nutrition label, the OCR results are sent to you for \
verification. You MUST:
1. Present the detected name, brand, and all macros clearly to the user
2. Ask if everything looks correct or if they want to change anything
3. Only call create_food AFTER the user confirms (or provides corrections)
4. Then log the meal using the newly created food's id
Do NOT save the food without user verification first.

{food_context}
{meals_context}\
"""


def _build_chat_system_prompt(
    known_foods: list[dict] | None,
    known_recipes: list[dict] | None = None,
    recent_meals: list[dict] | None = None,
    runtime_context: dict[str, str] | None = None,
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

    return CHAT_SYSTEM_PROMPT.format(
        time_context=time_context,
        food_context=food_context,
        meals_context=meals_context,
    )


CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_food_log",
            "description": (
                "Look up all meals and food items logged on a"
                " specific date. Returns meals with items and macro totals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_meal_to_date",
            "description": "Move an entire meal (with all its items) to a different date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_id": {"type": "integer", "description": "The meal ID to move"},
                    "new_date": {
                        "type": "string",
                        "description": "Target date YYYY-MM-DD",
                    },
                },
                "required": ["meal_id", "new_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_meal_item",
            "description": "Remove a specific food item from a meal by its item ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "The meal item ID to remove"},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_item_to_meal",
            "description": (
                "Add a food or recipe to an existing meal."
                " Provide food_id OR recipe_id, not both."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_id": {
                        "type": "integer",
                        "description": "The meal ID to add the item to",
                    },
                    "food_id": {
                        "type": "integer",
                        "description": "Food ID (use for individual foods)",
                    },
                    "recipe_id": {
                        "type": "integer",
                        "description": "Recipe ID (use for recipes)",
                    },
                    "amount_grams": {
                        "type": "number",
                        "description": "Amount in grams",
                    },
                },
                "required": ["meal_id", "amount_grams"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_meal",
            "description": "Create a new meal entry with food/recipe items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format",
                    },
                    "meal_type": {
                        "type": "string",
                        "enum": [
                            "breakfast", "lunch", "dinner", "snack",
                        ],
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "food_id": {"type": "integer"},
                                "recipe_id": {"type": "integer"},
                                "amount_grams": {"type": "number"},
                            },
                            "required": ["amount_grams"],
                        },
                    },
                },
                "required": ["date", "meal_type", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_food",
            "description": (
                "Create a new food in the database with nutrition info."
                " Use after verifying OCR results with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Food name (concise, lowercase)",
                    },
                    "brand": {
                        "type": "string",
                        "description": "Brand name, or null",
                    },
                    "serving_size_grams": {
                        "type": "number",
                        "description": "Serving size in grams",
                    },
                    "calories_per_serving": {"type": "number"},
                    "fat_per_serving": {"type": "number"},
                    "saturated_fat_per_serving": {"type": "number"},
                    "cholesterol_per_serving": {
                        "type": "number",
                        "description": "In milligrams",
                    },
                    "sodium_per_serving": {
                        "type": "number",
                        "description": "In milligrams",
                    },
                    "carbs_per_serving": {"type": "number"},
                    "fiber_per_serving": {"type": "number"},
                    "protein_per_serving": {"type": "number"},
                },
                "required": [
                    "name", "serving_size_grams",
                    "calories_per_serving", "fat_per_serving",
                    "saturated_fat_per_serving",
                    "cholesterol_per_serving",
                    "sodium_per_serving", "carbs_per_serving",
                    "fiber_per_serving", "protein_per_serving",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_macro_target",
            "description": (
                "Create or update a macro target day."
                " If a target already exists for that day, supplied fields overwrite it."
                " Omitted fields remain unchanged."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "description": "Target start date in YYYY-MM-DD format",
                    },
                    "calories": {"type": "number", "description": "Calories (kcal)"},
                    "fat": {"type": "number", "description": "Fat (g)"},
                    "saturated_fat": {"type": "number", "description": "Saturated fat (g)"},
                    "cholesterol": {"type": "number", "description": "Cholesterol (mg)"},
                    "sodium": {"type": "number", "description": "Sodium (mg)"},
                    "carbs": {"type": "number", "description": "Carbohydrates (g)"},
                    "fiber": {"type": "number", "description": "Fiber (g)"},
                    "protein": {"type": "number", "description": "Protein (g)"},
                },
                "required": ["day"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_meal",
            "description": "Delete an entire meal and all its items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meal_id": {"type": "integer", "description": "The meal ID to delete"},
                },
                "required": ["meal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_weight",
            "description": (
                "Record a body-weight entry in pounds."
                " If time is omitted, the server uses the current timestamp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "weight_lb": {
                        "type": "number",
                        "description": "Body weight in pounds",
                    },
                    "logged_at": {
                        "type": "string",
                        "description": (
                            "Optional ISO 8601 timestamp when the weight was taken"
                        ),
                    },
                },
                "required": ["weight_lb"],
            },
        },
    },
]


async def chat_meal(
    messages: list[dict],
    known_foods: list[dict] | None = None,
    known_recipes: list[dict] | None = None,
    recent_meals: list[dict] | None = None,
    tool_executor: Callable[[str, dict], Awaitable[Any]] | None = None,
    model: str | None = None,
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
    async with httpx.AsyncClient(timeout=60.0) as client:
        for _round in range(max_rounds):
            payload: dict[str, Any] = {
                "model": active_model,
                "messages": all_messages,
                "temperature": 0.3,
            }
            if tool_executor:
                payload["tools"] = CHAT_TOOLS

            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            logger.info("Chat LLM response status: %s", resp.status_code)
            if resp.status_code != 200:
                logger.error("Chat LLM response body: %s", resp.text[:2000])
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]

            tool_calls = message.get("tool_calls")
            if tool_calls and tool_executor:
                logger.info("LLM requested %d tool call(s)", len(tool_calls))
                all_messages.append(message)
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    func_args = json.loads(tc["function"]["arguments"])
                    logger.info("Executing tool: %s(%s)", func_name, func_args)
                    try:
                        result = await tool_executor(func_name, func_args)
                    except Exception as exc:
                        logger.exception("Tool execution failed: %s", func_name)
                        result = {"error": str(exc)}
                    all_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str),
                    })
                continue  # next round — let LLM see tool results

            content = message.get("content") or ""
            logger.info("Chat LLM raw content: %s", content)
            return content.strip()

    logger.warning("Chat meal exceeded max tool rounds")
    return "I had trouble processing that request. Please try again."
