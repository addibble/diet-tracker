"""Endpoint for LLM-powered meal parsing with USDA lookup."""

import json
import logging
import re
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.llm import chat_meal, parse_meal_description
from app.macros import MACRO_FIELDS
from app.models import Food, MealItem, MealLog
from app.usda import search_usda

logger = logging.getLogger("parse")

router = APIRouter(prefix="/api/meals", tags=["parse"])

SERVING_FIELDS = [f"{m}_per_serving" for m in MACRO_FIELDS]


class ParseRequest(BaseModel):
    description: str


@router.post("/parse")
async def parse_meal(
    data: ParseRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Parse a meal description using LLM, look up foods in DB or USDA."""
    if not data.description.strip():
        raise HTTPException(status_code=400, detail="Description cannot be empty")

    # Fetch all foods to give the LLM context for matching
    all_foods = session.exec(select(Food).order_by(Food.name)).all()
    known_foods = [
        {"id": f.id, "name": f.name, "brand": f.brand}
        for f in all_foods
    ]

    try:
        parsed_items = await parse_meal_description(data.description, known_foods)
    except Exception as e:
        logger.exception("LLM parsing failed")
        raise HTTPException(status_code=502, detail=f"LLM parsing failed: {e}")

    result_items: list[dict] = []
    new_foods: list[dict] = []

    for item in parsed_items:
        name = item["name"]
        grams = item["amount_grams"]
        llm_food_id = item.get("food_id")
        logger.info("Processing item: %s (%.1fg, food_id=%s)", name, grams, llm_food_id)

        # If LLM matched a known food, look it up directly
        if llm_food_id is not None:
            food = session.get(Food, llm_food_id)
            if food:
                logger.info("LLM-matched DB food: %s (id=%s)", food.name, food.id)
                result_items.append(_food_to_parsed_item(food, grams, "db"))
                continue
            else:
                logger.warning(
                    "LLM returned invalid food_id=%s for '%s', falling back to USDA",
                    llm_food_id, name,
                )

        # Fall back to USDA lookup for unmatched items
        try:
            usda_data = await search_usda(name)
        except Exception:
            logger.exception("USDA lookup failed for: %s", name)
            usda_data = None

        if usda_data:
            food = Food(
                name=usda_data["name"],
                source="usda",
                serving_size_grams=usda_data.get("serving_size_grams", 100),
                **{f: usda_data.get(f, 0) for f in SERVING_FIELDS},
            )
            session.add(food)
            session.commit()
            session.refresh(food)
            logger.info("Created food from USDA: %s (id=%s)", food.name, food.id)

            result_items.append(_food_to_parsed_item(food, grams, "usda"))
            new_foods.append({
                "id": food.id, "name": food.name, "source": "usda",
                "serving_size_grams": food.serving_size_grams,
                **{f: getattr(food, f) for f in SERVING_FIELDS},
            })
        else:
            logger.warning("No match for: %s", name)
            result_items.append({
                "name": name,
                "amount_grams": grams,
                "food_id": None,
                "source": "unknown",
                "macros_per_serving": {m: 0 for m in MACRO_FIELDS},
                "serving_size_grams": 100,
            })

    logger.info("Parse complete: %d items, %d new foods", len(result_items), len(new_foods))
    return {"items": result_items, "new_foods": new_foods}


def _food_to_parsed_item(food: Food, grams: float, source: str) -> dict:
    return {
        "name": food.name,
        "amount_grams": grams,
        "food_id": food.id,
        "source": source,
        "serving_size_grams": food.serving_size_grams,
        "macros_per_serving": {
            m: getattr(food, f"{m}_per_serving") for m in MACRO_FIELDS
        },
    }


# --- Conversational chat endpoint ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    date: str
    meal_type: str
    notes: str | None = None


def _resolve_chat_items(raw_items: list[dict], session: Session) -> list[dict]:
    """Resolve LLM-proposed items to full food details with macros."""
    result = []
    for item in raw_items:
        food_id = item.get("food_id")
        name = item.get("name", "unknown")
        grams = float(item.get("amount_grams", 0))

        if food_id is not None:
            food = session.get(Food, food_id)
            if food:
                result.append(_food_to_parsed_item(food, grams, "db"))
                continue

        result.append({
            "name": name,
            "amount_grams": grams,
            "food_id": None,
            "source": "unknown",
            "serving_size_grams": 100,
            "macros_per_serving": {m: 0 for m in MACRO_FIELDS},
        })
    return result


@router.post("/chat")
async def chat_meal_endpoint(
    data: ChatRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Conversational meal logging with LLM."""
    if not data.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    # Fetch known foods for LLM context
    all_foods = session.exec(select(Food).order_by(Food.name)).all()
    known_foods = [{"id": f.id, "name": f.name, "brand": f.brand} for f in all_foods]

    # Fetch today's meals so the LLM can reference/edit them
    from app.routers.meals import _build_meal_response

    request_date = date_type.fromisoformat(data.date)
    todays_logs = session.exec(
        select(MealLog).where(MealLog.date == request_date)
    ).all()
    todays_meals = [_build_meal_response(m, session) for m in todays_logs]

    messages = [{"role": m.role, "content": m.content} for m in data.messages]
    try:
        raw_response = await chat_meal(messages, known_foods, todays_meals)
    except Exception as e:
        logger.exception("LLM chat failed")
        raise HTTPException(status_code=502, detail=f"LLM chat failed: {e}")

    # Parse <ITEMS> block
    proposed_items = None
    items_match = re.search(r"<ITEMS>(.*?)</ITEMS>", raw_response, re.DOTALL)
    if items_match:
        try:
            raw_items = json.loads(items_match.group(1).strip())
            proposed_items = _resolve_chat_items(raw_items, session)
        except Exception:
            logger.exception("Failed to parse ITEMS block")

    # Check for <EDIT meal_id=X/>
    edit_match = re.search(r"<EDIT\s+meal_id=(\d+)\s*/>", raw_response)
    edit_meal_id = int(edit_match.group(1)) if edit_match else None

    # Check for <CONFIRM/>
    confirmed = bool(re.search(r"<CONFIRM\s*/>", raw_response))

    # Auto-save if confirmed and we have valid items
    saved_meal = None
    if confirmed and proposed_items:
        saveable = [i for i in proposed_items if i.get("food_id") is not None]
        if saveable:
            if edit_meal_id:
                # Update existing meal
                meal = session.get(MealLog, edit_meal_id)
                if not meal:
                    logger.warning("Edit target meal_id=%s not found", edit_meal_id)
                else:
                    old_items = session.exec(
                        select(MealItem).where(MealItem.meal_log_id == meal.id)
                    ).all()
                    for i in old_items:
                        session.delete(i)
                    for item in saveable:
                        session.add(MealItem(
                            meal_log_id=meal.id,
                            food_id=item["food_id"],
                            amount_grams=item["amount_grams"],
                        ))
                    session.commit()
                    saved_meal = _build_meal_response(meal, session)
                    logger.info(
                        "Updated meal id=%s with %d items", meal.id, len(saveable)
                    )
            else:
                # Create new meal
                meal = MealLog(
                    date=request_date,
                    meal_type=data.meal_type,
                    notes=data.notes,
                )
                session.add(meal)
                session.commit()
                session.refresh(meal)
                for item in saveable:
                    session.add(MealItem(
                        meal_log_id=meal.id,
                        food_id=item["food_id"],
                        amount_grams=item["amount_grams"],
                    ))
                session.commit()
                saved_meal = _build_meal_response(meal, session)
                logger.info("Auto-saved meal id=%s with %d items", meal.id, len(saveable))

    # Strip tags from message text
    clean_message = re.sub(r"<ITEMS>.*?</ITEMS>", "", raw_response, flags=re.DOTALL)
    clean_message = re.sub(r"<CONFIRM\s*/>", "", clean_message)
    clean_message = re.sub(r"<EDIT\s+meal_id=\d+\s*/>", "", clean_message).strip()

    return {
        "message": clean_message,
        "proposed_items": proposed_items,
        "saved_meal": saved_meal,
        "edit_meal_id": edit_meal_id,
    }
