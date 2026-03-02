from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.macros import MACRO_FIELDS, compute_food_macros, sum_macros, zero_macros
from app.models import Food, MealItem, MealLog, Recipe, RecipeComponent

router = APIRouter(prefix="/api/meals", tags=["meals"])


class MealItemInput(BaseModel):
    food_id: int | None = None
    recipe_id: int | None = None
    amount_grams: float


class MealCreate(BaseModel):
    date: date
    meal_type: str
    notes: str | None = None
    items: list[MealItemInput]


class MealUpdate(BaseModel):
    meal_type: str | None = None
    notes: str | None = None
    items: list[MealItemInput] | None = None


def _compute_item_macros(item: MealItem, session: Session) -> dict:
    """Compute macros for a single meal item (food or recipe)."""
    if item.food_id:
        food = session.get(Food, item.food_id)
        if not food:
            return {"name": "Unknown", "grams": item.amount_grams, **zero_macros()}
        macros = compute_food_macros(food, item.amount_grams)
        return {
            "id": item.id, "food_id": item.food_id, "recipe_id": None,
            "name": food.name, "grams": item.amount_grams, **macros,
        }
    elif item.recipe_id:
        recipe = session.get(Recipe, item.recipe_id)
        if not recipe:
            return {"name": "Unknown recipe", "grams": item.amount_grams, **zero_macros()}
        components = session.exec(
            select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
        ).all()
        recipe_totals = {m: 0.0 for m in MACRO_FIELDS}
        recipe_grams = 0.0
        for comp in components:
            food = session.get(Food, comp.food_id)
            if food:
                comp_macros = compute_food_macros(food, comp.amount_grams)
                for m in MACRO_FIELDS:
                    recipe_totals[m] += comp_macros[m]
                recipe_grams += comp.amount_grams
        scale = item.amount_grams / recipe_grams if recipe_grams > 0 else 0
        scaled = {m: round(recipe_totals[m] * scale, 1) for m in MACRO_FIELDS}
        return {
            "id": item.id, "food_id": None, "recipe_id": item.recipe_id,
            "name": recipe.name, "grams": item.amount_grams, **scaled,
        }
    return {"name": "Empty", "grams": 0, **zero_macros()}


def _build_meal_response(meal: MealLog, session: Session) -> dict:
    items = session.exec(
        select(MealItem).where(MealItem.meal_log_id == meal.id)
    ).all()
    item_details = [_compute_item_macros(item, session) for item in items]
    totals = sum_macros(item_details)
    return {
        "id": meal.id, "date": str(meal.date), "meal_type": meal.meal_type,
        "notes": meal.notes, "created_at": meal.created_at,
        "items": item_details, **totals,
    }


@router.get("")
def list_meals(
    date: date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(MealLog)
    if date:
        stmt = stmt.where(MealLog.date == date)
    stmt = stmt.order_by(MealLog.date.desc(), MealLog.created_at.desc())  # type: ignore[union-attr]
    meals = session.exec(stmt).all()
    return [_build_meal_response(m, session) for m in meals]


@router.get("/{meal_id}")
def get_meal(
    meal_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    meal = session.get(MealLog, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    return _build_meal_response(meal, session)


@router.post("", status_code=201)
def create_meal(
    data: MealCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    meal = MealLog(date=data.date, meal_type=data.meal_type, notes=data.notes)
    session.add(meal)
    session.commit()
    session.refresh(meal)
    for item in data.items:
        if not item.food_id and not item.recipe_id:
            raise HTTPException(status_code=400, detail="Each item needs food_id or recipe_id")
        session.add(MealItem(
            meal_log_id=meal.id,
            food_id=item.food_id,
            recipe_id=item.recipe_id,
            amount_grams=item.amount_grams,
        ))
    session.commit()
    return _build_meal_response(meal, session)


@router.put("/{meal_id}")
def update_meal(
    meal_id: int,
    data: MealUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    meal = session.get(MealLog, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    if data.meal_type is not None:
        meal.meal_type = data.meal_type
    if data.notes is not None:
        meal.notes = data.notes
    if data.items is not None:
        old_items = session.exec(
            select(MealItem).where(MealItem.meal_log_id == meal.id)
        ).all()
        for i in old_items:
            session.delete(i)
        for item in data.items:
            if not item.food_id and not item.recipe_id:
                raise HTTPException(status_code=400, detail="Each item needs food_id or recipe_id")
            session.add(MealItem(
                meal_log_id=meal.id,
                food_id=item.food_id,
                recipe_id=item.recipe_id,
                amount_grams=item.amount_grams,
            ))
    session.add(meal)
    session.commit()
    return _build_meal_response(meal, session)


@router.delete("/{meal_id}", status_code=204)
def delete_meal(
    meal_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    meal = session.get(MealLog, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    items = session.exec(
        select(MealItem).where(MealItem.meal_log_id == meal.id)
    ).all()
    for i in items:
        session.delete(i)
    session.delete(meal)
    session.commit()
