from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.models import Food, MealItem, MealLog, Recipe

router = APIRouter(tags=["meal-items"])


class MealItemUpdate(BaseModel):
    amount_grams: float | None = None


class MealItemCreate(BaseModel):
    food_id: int | None = None
    recipe_id: int | None = None
    amount_grams: float


def _item_response(item: MealItem, session: Session) -> dict:
    from app.routers.meals import _compute_item_macros

    return _compute_item_macros(item, session)


def _meal_response(meal: MealLog, session: Session) -> dict:
    from app.routers.meals import _build_meal_response

    return _build_meal_response(meal, session)


@router.patch("/api/meal-items/{item_id}")
def update_meal_item(
    item_id: int,
    data: MealItemUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    item = session.get(MealItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Meal item not found")
    if data.amount_grams is not None:
        item.amount_grams = data.amount_grams
    session.add(item)
    session.commit()
    session.refresh(item)
    meal = session.get(MealLog, item.meal_log_id)
    return _meal_response(meal, session)  # type: ignore[arg-type]


@router.post("/api/meals/{meal_id}/items", status_code=201)
def add_meal_item(
    meal_id: int,
    data: MealItemCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    meal = session.get(MealLog, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    if not data.food_id and not data.recipe_id:
        raise HTTPException(
            status_code=400, detail="food_id or recipe_id required"
        )
    if data.food_id:
        food = session.get(Food, data.food_id)
        if not food:
            raise HTTPException(
                status_code=400, detail=f"Food {data.food_id} not found"
            )
    if data.recipe_id:
        recipe = session.get(Recipe, data.recipe_id)
        if not recipe:
            raise HTTPException(
                status_code=400,
                detail=f"Recipe {data.recipe_id} not found",
            )
    item = MealItem(
        meal_log_id=meal_id,
        food_id=data.food_id,
        recipe_id=data.recipe_id,
        amount_grams=data.amount_grams,
    )
    session.add(item)
    session.commit()
    return _meal_response(meal, session)


@router.delete("/api/meal-items/{item_id}", status_code=204)
def delete_meal_item(
    item_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    item = session.get(MealItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Meal item not found")
    session.delete(item)
    session.commit()
