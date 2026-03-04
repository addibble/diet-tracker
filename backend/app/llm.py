"""OpenRouter LLM client for parsing meal descriptions."""

import base64
import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("parse")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4.5"
VISION_MODEL = "anthropic/claude-haiku-4.5"

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

CHAT_SYSTEM_PROMPT = """\
You are a friendly meal-logging assistant. Help the user log what they ate by \
understanding their description and matching it to foods in their database.

Rules:
- Respond naturally and conversationally. Confirm your understanding of the meal \
with specific foods, brands, and gram amounts.
- When you have identified the specific foods and amounts, include a structured \
breakdown in this exact XML block:
  <ITEMS>[{{"food_id": 42, "name": "food name", "amount_grams": 60}}, ...]</ITEMS>
- food_id must be an integer from the known foods list, or null if no match.
- Always include the <ITEMS> block in your first response and whenever you update the breakdown.
- If the user says "yes", "looks good", "save it", "confirm", or similar \
affirmation, include <CONFIRM/> in your response.
- Do NOT include <CONFIRM/> unless the user has explicitly confirmed.
- When the user requests adjustments, update the <ITEMS> block accordingly.
- Estimate reasonable gram weights when the user doesn't specify.
- Use the food names from the database (not generic names) when matched.

Editing existing meals:
- You can see today's logged meals below (if any). The user may ask to edit a \
previously logged meal (e.g. "add cheese to my sandwich from lunch", \
"change lunch to dinner", "remove the bread").
- To edit an existing meal, include <EDIT meal_id=ID/> before your <ITEMS> block, \
where ID is the meal's id number.
- When editing, the <ITEMS> block must contain ALL items for that meal (both \
changed and unchanged items), because it replaces the full item list.
- If the user is logging a NEW meal (not editing), do NOT include the <EDIT> tag.

{food_context}
{meals_context}\
"""


def _build_chat_system_prompt(
    known_foods: list[dict] | None,
    todays_meals: list[dict] | None = None,
) -> str:
    if known_foods:
        food_list = json.dumps(
            [{"id": f["id"], "name": f["name"], "brand": f.get("brand")} for f in known_foods],
            separators=(",", ":"),
        )
        food_context = f"Known foods in the database (use their id as food_id):\n{food_list}"
    else:
        food_context = ""

    if todays_meals:
        lines = ["Today's logged meals:"]
        for meal in todays_meals:
            items_str = ", ".join(
                f"{it['name']} {it['grams']}g" for it in meal["items"]
            )
            lines.append(
                f"- Meal #{meal['id']} ({meal['meal_type']}): "
                f"{items_str} [{meal['total_calories']} kcal]"
            )
        meals_context = "\n".join(lines)
    else:
        meals_context = ""

    return CHAT_SYSTEM_PROMPT.format(food_context=food_context, meals_context=meals_context)


async def chat_meal(
    messages: list[dict],
    known_foods: list[dict] | None = None,
    todays_meals: list[dict] | None = None,
) -> str:
    """Multi-turn conversational meal chat. Returns raw LLM text response."""
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    system_prompt = _build_chat_system_prompt(known_foods, todays_meals)
    logger.info("Chat meal: %d messages, %d known foods", len(messages), len(known_foods or []))

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
                    *messages,
                ],
                "temperature": 0.3,
            },
            timeout=30.0,
        )
        logger.info("Chat LLM response status: %s", resp.status_code)
        if resp.status_code != 200:
            logger.error("Chat LLM response body: %s", resp.text[:2000])
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("Chat LLM raw content: %s", content)
        return content.strip()
