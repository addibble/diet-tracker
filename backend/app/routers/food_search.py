from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.macros import compute_food_macros
from app.models import Food, Recipe, RecipeComponent

router = APIRouter(prefix="/api/food-search", tags=["food-search"])


@router.get("")
def search_foods_and_recipes(
    search: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    results: list[dict] = []

    # Search foods
    food_stmt = (
        select(Food)
        .where(Food.name.contains(search))  # type: ignore[union-attr]
        .order_by(Food.name)
        .limit(20)
    )
    for f in session.exec(food_stmt).all():
        results.append(
            {
                "type": "food",
                "id": f.id,
                "name": f.name,
                "brand": f.brand,
                "serving_size_grams": f.serving_size_grams,
                "calories_per_serving": f.calories_per_serving,
            }
        )

    # Search recipes
    recipe_stmt = (
        select(Recipe)
        .where(Recipe.name.contains(search))  # type: ignore[union-attr]
        .order_by(Recipe.name)
        .limit(20)
    )
    for r in session.exec(recipe_stmt).all():
        components = session.exec(select(RecipeComponent).where(RecipeComponent.recipe_id == r.id)).all()
        total_grams = 0.0
        total_calories = 0.0
        for comp in components:
            food = session.get(Food, comp.food_id)
            if food:
                macros = compute_food_macros(food, comp.amount_grams)
                total_grams += comp.amount_grams
                total_calories += macros["calories"]
        results.append(
            {
                "type": "recipe",
                "id": r.id,
                "name": r.name,
                "total_grams": round(total_grams, 1),
                "total_calories": round(total_calories, 1),
            }
        )

    # Sort combined by name
    results.sort(key=lambda x: x["name"].lower())
    return results
