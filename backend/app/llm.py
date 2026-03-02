"""OpenRouter LLM client for parsing meal descriptions."""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("parse")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4.5"

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


def _build_system_prompt(known_foods: list[dict] | None) -> str:
    if known_foods:
        food_list = json.dumps(
            [{"id": f["id"], "name": f["name"], "brand": f.get("brand")} for f in known_foods],
            separators=(",", ":"),
        )
        return BASE_SYSTEM_PROMPT + CONTEXT_PROMPT.format(food_list=food_list)
    return BASE_SYSTEM_PROMPT + NO_CONTEXT_PROMPT


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
        content = data["choices"][0]["message"]["content"]
        logger.info("LLM raw content: %s", content)

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

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
