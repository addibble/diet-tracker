"""Endpoint for LLM-powered meal parsing with USDA lookup."""

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.llm import (
    MODEL,
    LLMUpstreamBillingError,
    LLMUpstreamCompletionError,
    LLMUpstreamRetryableError,
    LLMUpstreamTimeoutError,
    chat_meal,
    chat_runtime_context,
    chat_status_callback,
    get_chat_models,
    parse_meal_description,
)
from app.llm_tools import TOOL_HANDLERS, get_workout_context
from app.macros import MACRO_FIELDS
from app.models import Food, MealItem, MealLog, Recipe
from app.usda import search_usda

logger = logging.getLogger("parse")

router = APIRouter(prefix="/api/meals", tags=["parse"])

SERVING_FIELDS = [f"{m}_per_serving" for m in MACRO_FIELDS]
CHAT_STREAM_HEARTBEAT_SECONDS = 2.5


def _chat_activity_source(event_name: str | None) -> str | None:
    if not event_name:
        return None
    if event_name.startswith("tool_call_") or event_name == "tool_calls_received":
        return "local_tool"
    if event_name in {"final_response_received", "upstream_round_complete"}:
        return "finalizing"
    if event_name.startswith("upstream_") or event_name == "gemini_forced_tool_retry":
        return "openrouter"
    if event_name == "round_started":
        return "backend"
    return "backend"


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
    date: str | None = None
    meal_type: str | None = None
    notes: str | None = None
    client_now_iso: str | None = None
    client_timezone: str | None = None
    model: str | None = None


@router.get("/chat/models")
async def chat_models_endpoint(
    _user: str = Depends(get_current_user),
):
    """List affordable chat models for the Log Meal interface."""
    try:
        models = await get_chat_models()
    except Exception as e:
        logger.exception("Chat model list fetch failed")
        raise HTTPException(status_code=502, detail=f"Model list fetch failed: {e}")

    return {
        "default_model": MODEL,
        "models": models,
    }


def _messages_user_text(messages: list[ChatMessage]) -> str:
    return " ".join(m.content.lower() for m in messages if m.role == "user")


def _resolve_client_now(
    client_now_iso: str | None,
    client_timezone: str | None,
) -> tuple[datetime, str]:
    now = datetime.now(UTC)
    if client_now_iso:
        try:
            parsed = datetime.fromisoformat(client_now_iso.replace("Z", "+00:00"))
            now = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            logger.warning("Invalid client_now_iso=%r; using server UTC now", client_now_iso)

    tz_name = (client_timezone or "").strip()
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
            return now.astimezone(tz), tz_name
        except ZoneInfoNotFoundError:
            logger.warning("Invalid client_timezone=%r; using UTC", client_timezone)

    # Fall back to server-local timezone when the client timezone is unavailable.
    # This avoids date shifts around UTC midnight for natural-language phrases
    # like "yesterday" when callers omit client timezone metadata.
    local_now = now.astimezone()
    local_tz_name = local_now.tzname() or "local"
    return local_now, local_tz_name


def _infer_request_date(
    messages: list[ChatMessage],
    requested_date: str | None,
    reference_now: datetime,
) -> date_type:
    if requested_date:
        try:
            return date_type.fromisoformat(requested_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    text = _messages_user_text(messages)
    today = reference_now.date()

    explicit_date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if explicit_date_match:
        try:
            return date_type.fromisoformat(explicit_date_match.group(1))
        except ValueError:
            logger.warning("Ignoring invalid date in chat text: %s", explicit_date_match.group(1))

    if "yesterday" in text:
        return today - timedelta(days=1)
    if "tomorrow" in text:
        return today + timedelta(days=1)
    return today


def _infer_meal_type(
    messages: list[ChatMessage],
    requested_meal_type: str | None,
    reference_now: datetime,
) -> str:
    if requested_meal_type and requested_meal_type.strip():
        return requested_meal_type.strip().lower()

    text = _messages_user_text(messages)
    keyword_map = {
        "breakfast": ("breakfast", "brunch", "morning"),
        "lunch": ("lunch", "noon"),
        "dinner": ("dinner", "supper", "tonight"),
        "snack": ("snack",),
    }
    for meal_type, keywords in keyword_map.items():
        if any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords):
            return meal_type

    hour = reference_now.hour
    if hour < 11:
        return "breakfast"
    if hour < 16:
        return "lunch"
    if hour < 21:
        return "dinner"
    return "snack"


class _ToolState:
    """Tracks whether any tool call mutated the database."""
    def __init__(self) -> None:
        self.data_changed = False
        # True if the LLM already created a meal log via the set_meal_logs tool.
        # Used to suppress the <CONFIRM/> auto-save so we don't double-write.
        self.meal_saved_via_tools = False
        # Captures the workout session ID when set_workout_sessions creates/updates one.
        self.workout_session_id: int | None = None


def _make_tool_executor(
    session: Session,
    state: _ToolState,
    default_target_day: date_type,
):
    """Create a tool executor closure with DB access.

    Routes all tool calls through the table-driven TOOL_HANDLERS
    registry from llm_tools.
    """
    async def executor(name: str, args: dict):
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}

        result = handler(args, session)

        # Any setter marks data as changed
        if name.startswith("set_"):
            state.data_changed = True

        # Capture workout session ID from workout_sessions setter
        if name == "set_workout_sessions":
            for match in result.get("matches", []):
                if isinstance(match, dict) and "id" in match:
                    state.workout_session_id = match["id"]
                    break

        # Track meal creation via tools to prevent duplicate saves
        if name == "set_meal_logs":
            for change in args.get("changes", []):
                if change.get("operation") == "create":
                    state.meal_saved_via_tools = True
                    break

        return result

    return executor


def _resolve_chat_items(raw_items: list[dict], session: Session) -> list[dict]:
    """Resolve LLM-proposed items to full food/recipe details with macros."""
    from app.routers.recipes import _build_recipe_response

    result = []
    for item in raw_items:
        food_id = item.get("food_id")
        recipe_id = item.get("recipe_id")
        name = item.get("name", "unknown")
        grams = float(item.get("amount_grams", 0))

        if food_id is not None:
            food = session.get(Food, food_id)
            if food:
                result.append(_food_to_parsed_item(food, grams, "db"))
                continue

        if recipe_id is not None:
            recipe = session.get(Recipe, recipe_id)
            if recipe:
                rdata = _build_recipe_response(recipe, session)
                total_g = rdata.get("total_grams", 0) or 1
                result.append({
                    "name": rdata["name"],
                    "amount_grams": grams,
                    "food_id": None,
                    "recipe_id": recipe_id,
                    "source": "db",
                    "serving_size_grams": total_g,
                    "macros_per_serving": {
                        m: rdata.get(f"total_{m}", 0)
                        for m in MACRO_FIELDS
                    },
                })
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


def _ndjson_line(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), default=str) + "\n"


@router.post("/chat/stream")
async def chat_meal_stream_endpoint(
    data: ChatRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Stream chat progress heartbeats, then emit the final chat response payload."""
    if not data.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    async def _stream():
        run_id = uuid.uuid4().hex[:12]
        start = time.monotonic()
        last_upstream_event_monotonic: float | None = None
        last_activity_event_monotonic: float | None = None
        status_queue: deque[str] = deque()
        status_ready = asyncio.Event()
        latest_upstream: dict[str, str | int | None] = {
            "event": None,
            "status_code": None,
            "openrouter_request_id": None,
            "openrouter_completion_id": None,
            "cf_ray": None,
            "attempt": None,
            "round": None,
        }
        latest_activity: dict[str, str | int | None] = {
            "event": None,
            "source": "backend",
            "tool_name": None,
            "round": None,
        }

        def _status_payload(
            *,
            stage: str,
            message: str,
            now: float | None = None,
            text: str | None = None,
            tool_args: str | None = None,
            tool_result: str | None = None,
        ) -> dict:
            snapshot_now = time.monotonic() if now is None else now
            if last_activity_event_monotonic is None:
                activity_age_ms = None
            else:
                activity_age_ms = int((snapshot_now - last_activity_event_monotonic) * 1000)
            if last_upstream_event_monotonic is None:
                upstream_age_ms = None
            else:
                upstream_age_ms = int((snapshot_now - last_upstream_event_monotonic) * 1000)
            elapsed_ms = int((snapshot_now - start) * 1000)
            return {
                "type": "status",
                "run_id": run_id,
                "stage": stage,
                "message": message,
                "elapsed_ms": elapsed_ms,
                "activity_source": latest_activity["source"],
                "last_activity_event": latest_activity["event"],
                "last_activity_event_age_ms": activity_age_ms,
                "active_tool_name": latest_activity["tool_name"],
                "last_upstream_event": latest_upstream["event"],
                "last_upstream_event_age_ms": upstream_age_ms,
                "last_upstream_status_code": latest_upstream["status_code"],
                "openrouter_request_id": latest_upstream["openrouter_request_id"],
                "openrouter_completion_id": latest_upstream["openrouter_completion_id"],
                "upstream_cf_ray": latest_upstream["cf_ray"],
                "upstream_attempt": latest_upstream["attempt"],
                "upstream_round": latest_upstream["round"],
                "text": text,
                "tool_args": tool_args,
                "tool_result": tool_result,
            }

        def _on_chat_status(event: dict):
            nonlocal last_upstream_event_monotonic
            nonlocal last_activity_event_monotonic
            now = time.monotonic()
            last_activity_event_monotonic = now
            event_name = event.get("event")
            if isinstance(event_name, str):
                latest_activity["event"] = event_name
                latest_activity["source"] = _chat_activity_source(event_name)
                if latest_activity["source"] != "local_tool":
                    latest_activity["tool_name"] = None
                if latest_activity["source"] == "openrouter":
                    last_upstream_event_monotonic = now
                    latest_upstream["event"] = event_name
            for key in (
                "status_code",
                "openrouter_request_id",
                "openrouter_completion_id",
                "cf_ray",
                "attempt",
                "round",
            ):
                value = event.get(key)
                if value is not None:
                    latest_upstream[key] = value
            for key in ("tool_name", "round"):
                value = event.get(key)
                if value is not None:
                    latest_activity[key] = value
            status_queue.append(_ndjson_line(_status_payload(
                stage="processing",
                message="Processing your request...",
                now=now,
                text=event.get("text"),
                tool_args=event.get("tool_args"),
                tool_result=event.get("tool_result"),
            )))
            status_ready.set()

        yield _ndjson_line(_status_payload(
            stage="queued",
            message="Submitting request to model provider...",
            now=start,
        ))

        async def _run_chat_with_status():
            with chat_status_callback(_on_chat_status):
                return await chat_meal_endpoint(data, session, _user)

        task = asyncio.create_task(_run_chat_with_status())
        try:
            while not task.done():
                if status_queue:
                    yield status_queue.popleft()
                    if not status_queue:
                        status_ready.clear()
                    continue
                try:
                    await asyncio.wait_for(
                        status_ready.wait(),
                        timeout=CHAT_STREAM_HEARTBEAT_SECONDS,
                    )
                except TimeoutError:
                    pass
                if status_queue:
                    continue
                yield _ndjson_line(_status_payload(
                    stage="processing",
                    message="Processing your request...",
                ))

            while status_queue:
                yield status_queue.popleft()
            status_ready.clear()

            result = await task
            yield _ndjson_line({
                "type": "result",
                "run_id": run_id,
                "data": result,
            })
        except HTTPException as exc:
            yield _ndjson_line({
                "type": "error",
                "run_id": run_id,
                "status": exc.status_code,
                "detail": str(exc.detail),
            })
        except Exception:
            logger.exception("chat stream failed unexpectedly")
            yield _ndjson_line({
                "type": "error",
                "run_id": run_id,
                "status": 500,
                "detail": "Unexpected chat streaming error",
            })
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat")
async def chat_meal_endpoint(
    data: ChatRequest,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Conversational meal logging with LLM."""
    if not data.messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    # Fetch known foods and recipes for LLM context
    all_foods = session.exec(select(Food).order_by(Food.name)).all()
    known_foods = [{"id": f.id, "name": f.name, "brand": f.brand} for f in all_foods]

    all_recipes = session.exec(select(Recipe).order_by(Recipe.name)).all()
    known_recipes = [{"id": r.id, "name": r.name} for r in all_recipes]
    from app.routers.meals import _build_meal_response

    client_now, client_timezone = _resolve_client_now(
        data.client_now_iso,
        data.client_timezone,
    )
    request_date = _infer_request_date(data.messages, data.date, client_now)
    meal_type = _infer_meal_type(data.messages, data.meal_type, client_now)

    llm_time_context = {
        "client_local_datetime": client_now.isoformat(timespec="minutes"),
        "client_local_date": request_date.isoformat(),
        "client_timezone": client_timezone,
        "default_meal_type": meal_type,
    }

    tool_state = _ToolState()
    tool_executor = _make_tool_executor(session, tool_state, client_now.date())

    # Build workout context for the system prompt
    wctx = get_workout_context(session)

    messages = [{"role": m.role, "content": m.content} for m in data.messages]
    try:
        with chat_runtime_context(llm_time_context):
            if data.model:
                raw_response = await chat_meal(
                    messages, known_foods, known_recipes,
                    None, tool_executor, model=data.model,
                    workout_context=wctx,
                )
            else:
                raw_response = await chat_meal(
                    messages, known_foods, known_recipes,
                    None, tool_executor,
                    workout_context=wctx,
                )
    except LLMUpstreamTimeoutError as e:
        logger.exception("LLM chat timed out")
        raise HTTPException(status_code=504, detail=f"LLM chat timed out: {e}")
    except LLMUpstreamRetryableError as e:
        logger.exception("LLM chat upstream error")
        raise HTTPException(status_code=502, detail=f"LLM chat upstream error: {e}")
    except LLMUpstreamBillingError as e:
        logger.exception("LLM chat credit/billing limit reached")
        raise HTTPException(status_code=402, detail=f"LLM provider credit limit: {e}")
    except LLMUpstreamCompletionError as e:
        logger.exception("LLM chat completion failed")
        raise HTTPException(status_code=502, detail=f"LLM provider generation error: {e}")
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

    # Auto-save if confirmed and we have valid items.
    # Skip if the LLM already created the meal via set_meal_logs to avoid duplicates.
    saved_meal = None
    if confirmed and proposed_items and not tool_state.meal_saved_via_tools:
        saveable = [
            i for i in proposed_items
            if i.get("food_id") is not None or i.get("recipe_id") is not None
        ]
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
                            food_id=item.get("food_id"),
                            recipe_id=item.get("recipe_id"),
                            amount_grams=item["amount_grams"],
                        ))
                    session.commit()
                    saved_meal = _build_meal_response(meal, session)
                    logger.info(
                        "Updated meal id=%s with %d items",
                        meal.id, len(saveable),
                    )
            else:
                # Create new meal
                meal = MealLog(
                    date=request_date,
                    meal_type=meal_type,
                    notes=data.notes,
                )
                session.add(meal)
                session.commit()
                session.refresh(meal)
                for item in saveable:
                    session.add(MealItem(
                        meal_log_id=meal.id,
                        food_id=item.get("food_id"),
                        recipe_id=item.get("recipe_id"),
                        amount_grams=item["amount_grams"],
                    ))
                session.commit()
                saved_meal = _build_meal_response(meal, session)
                logger.info(
                    "Auto-saved meal id=%s with %d items",
                    meal.id, len(saveable),
                )

    # Parse <REP_CHECK> tag for workout rep completion widget
    rep_check = None
    rep_check_match = re.search(r"<REP_CHECK\s+exercises='(.*?)'\s*/>", raw_response, re.DOTALL)
    if rep_check_match:
        try:
            rep_check = json.loads(rep_check_match.group(1))
        except Exception:
            logger.exception("Failed to parse REP_CHECK tag")

    # Strip tags from message text
    clean_message = re.sub(r"<ITEMS>.*?</ITEMS>", "", raw_response, flags=re.DOTALL)
    clean_message = re.sub(r"<CONFIRM\s*/>", "", clean_message)
    clean_message = re.sub(r"<EDIT\s+meal_id=\d+\s*/>", "", clean_message)
    clean_message = re.sub(r"<REP_CHECK\s+exercises='.*?'\s*/>", "", clean_message, flags=re.DOTALL)
    clean_message = clean_message.strip()

    return {
        "message": clean_message,
        "proposed_items": proposed_items,
        "saved_meal": saved_meal,
        "edit_meal_id": edit_meal_id,
        "data_changed": tool_state.data_changed,
        "rep_check": rep_check,
        "workout_session_id": tool_state.workout_session_id,
    }
