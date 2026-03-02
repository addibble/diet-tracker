"""USDA FoodData Central API client for looking up nutritional data."""

import logging

import httpx

logger = logging.getLogger("parse")

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_API_KEY = "DEMO_KEY"  # Free tier, rate-limited but sufficient

# USDA nutrient ID → our field name mapping
# USDA returns values per 100g, so serving_size_grams=100 for USDA foods.
NUTRIENT_MAP = {
    1008: "calories_per_serving",      # Energy (kcal)
    1004: "fat_per_serving",           # Total lipid (fat)
    1258: "saturated_fat_per_serving",  # Fatty acids, total saturated
    1253: "cholesterol_per_serving",    # Cholesterol
    1093: "sodium_per_serving",         # Sodium, Na
    1005: "carbs_per_serving",          # Carbohydrate, by difference
    1079: "fiber_per_serving",          # Fiber, total dietary
    1003: "protein_per_serving",        # Protein
}


async def search_usda(query: str) -> dict | None:
    """Search USDA for a food. Returns macros per 100g serving."""
    logger.info("USDA search: %s", query)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            USDA_SEARCH_URL,
            params={
                "api_key": USDA_API_KEY,
                "query": query,
                "pageSize": 5,
            },
            timeout=10.0,
        )
        logger.info("USDA response status: %s", resp.status_code)
        if resp.status_code != 200:
            logger.warning("USDA search failed: %s %s", resp.status_code, resp.text[:500])
            return None
        data = resp.json()
        foods = data.get("foods", [])
        if not foods:
            logger.warning("USDA returned no results for: %s", query)
            return None

        food = foods[0]
        logger.info("USDA matched: %s (fdcId=%s)", food.get("description"), food.get("fdcId"))
        nutrients = {
            n["nutrientId"]: n.get("value", 0)
            for n in food.get("foodNutrients", [])
        }

        result: dict = {
            "name": food.get("description", query).title(),
            "serving_size_grams": 100,  # USDA data is per 100g
        }
        for nutrient_id, field in NUTRIENT_MAP.items():
            result[field] = round(nutrients.get(nutrient_id, 0), 2)

        logger.info("USDA result: %s", result)
        return result
