# Shared macro field definitions used across routers.
# Each macro maps to its Food model field suffix (e.g. "calories" → "calories_per_serving").

MACRO_FIELDS = [
    "calories", "fat", "saturated_fat", "cholesterol",
    "sodium", "carbs", "fiber", "protein",
]


def compute_food_macros(food, grams: float) -> dict[str, float]:
    """Compute macros for a given amount of a food, scaling from per-serving values."""
    if food.serving_size_grams > 0:
        ratio = grams / food.serving_size_grams
    else:
        ratio = 0
    return {
        m: round(getattr(food, f"{m}_per_serving") * ratio, 1)
        for m in MACRO_FIELDS
    }


def zero_macros() -> dict[str, float]:
    return {m: 0.0 for m in MACRO_FIELDS}


def sum_macros(items: list[dict], prefix: str = "total_") -> dict[str, float]:
    """Sum macro values across a list of dicts, returning with optional prefix."""
    return {
        f"{prefix}{m}": round(sum(i.get(m, 0) for i in items), 1)
        for m in MACRO_FIELDS
    }
