"""Table-driven LLM tool system.

Provides 22 tools (11 get/set pairs) organized by domain:
- Nutrition: foods, recipes, meal_logs, weight_logs, macro_targets
- Workout: exercises, tissues, tissue_conditions, workout_sessions,
  routine_exercises, workouts

Usage:
    from app.llm_tools import TOOL_HANDLERS, select_tools

    # Get tools for a message
    tools = select_tools(messages)

    # Execute a tool
    result = TOOL_HANDLERS[tool_name](args, session)
"""

import re
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from .nutrition import (
    NUTRITION_TOOL_DEFINITIONS,
    NUTRITION_TOOL_HANDLERS,
)
from .workout import (
    WORKOUT_TOOL_DEFINITIONS,
    WORKOUT_TOOL_HANDLERS,
    get_workout_context,
)

# ── Combined registries ───────────────────────────────────────────────

ALL_TOOL_DEFINITIONS: list[dict] = (
    NUTRITION_TOOL_DEFINITIONS + WORKOUT_TOOL_DEFINITIONS
)

TOOL_HANDLERS: dict[str, Callable[[dict, Session], dict]] = {
    **NUTRITION_TOOL_HANDLERS,
    **WORKOUT_TOOL_HANDLERS,
}

# ── Domain-family tool selection ──────────────────────────────────────

_WORKOUT_PATTERN = re.compile(
    r"\b("
    r"workout|train|training|exercise|lift|lifting|plan|planner|"
    r"bench|squat|deadlift|press|curl|row|"
    r"run|running|walk|walking|cardio|routine|"
    r"rep|reps|set|sets|rpe|"
    r"tissue|pain|painful|sore|soreness|tight|tightness|"
    r"injur|rehab|recovery"
    r")\b",
    re.IGNORECASE,
)

_NUTRITION_PATTERN = re.compile(
    r"\b("
    r"eat|ate|eaten|food|meal|breakfast|lunch|dinner|snack|"
    r"calori|macro|protein|carb|fat|fiber|sodium|cholesterol|"
    r"weight|weigh|lb|lbs|pound|kg|"
    r"nutrition|label|recipe|ingredient|"
    r"target|diet|log.*(?:food|meal)"
    r")\b",
    re.IGNORECASE,
)

_REPSET_PATTERN = re.compile(
    r"\b\d+\s*x\s*\d+(?:\s*x\s*\d+)?\b", re.IGNORECASE
)


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def select_tools(messages: list[dict[str, Any]]) -> list[dict]:
    """Select tools by domain family based on the latest user message.

    - Workout-only messages → workout tools
    - Nutrition-only messages → nutrition tools
    - Mixed or ambiguous → all tools
    """
    text = _latest_user_text(messages)
    is_workout = bool(
        _WORKOUT_PATTERN.search(text) or _REPSET_PATTERN.search(text)
    )
    is_nutrition = bool(_NUTRITION_PATTERN.search(text))

    if is_workout and not is_nutrition:
        return WORKOUT_TOOL_DEFINITIONS
    if is_nutrition and not is_workout:
        return NUTRITION_TOOL_DEFINITIONS
    return ALL_TOOL_DEFINITIONS


__all__ = [
    "ALL_TOOL_DEFINITIONS",
    "NUTRITION_TOOL_DEFINITIONS",
    "WORKOUT_TOOL_DEFINITIONS",
    "TOOL_HANDLERS",
    "select_tools",
    "get_workout_context",
]
