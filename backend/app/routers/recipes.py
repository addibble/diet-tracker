from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.macros import compute_food_macros, sum_macros
from app.models import Food, Recipe, RecipeComponent

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


class ComponentInput(BaseModel):
    food_id: int
    amount_grams: float


class RecipeCreate(BaseModel):
    name: str
    components: list[ComponentInput]


class RecipeUpdate(BaseModel):
    name: str | None = None
    components: list[ComponentInput] | None = None


def _build_recipe_response(recipe: Recipe, session: Session) -> dict:
    components = session.exec(
        select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
    ).all()
    component_details = []
    total_grams = 0.0
    for comp in components:
        food = session.get(Food, comp.food_id)
        if food:
            macros = compute_food_macros(food, comp.amount_grams)
            total_grams += comp.amount_grams
            component_details.append({
                "id": comp.id,
                "food_id": comp.food_id,
                "food_name": food.name,
                "amount_grams": comp.amount_grams,
                **macros,
            })
    totals = sum_macros(component_details)
    return {
        "id": recipe.id,
        "name": recipe.name,
        "created_at": recipe.created_at,
        "components": component_details,
        "total_grams": round(total_grams, 1),
        **totals,
    }


@router.get("")
def list_recipes(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    recipes = session.exec(select(Recipe).order_by(Recipe.name)).all()
    return [_build_recipe_response(r, session) for r in recipes]


@router.get("/{recipe_id}")
def get_recipe(
    recipe_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _build_recipe_response(recipe, session)


@router.post("", status_code=201)
def create_recipe(
    data: RecipeCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    recipe = Recipe(name=data.name)
    session.add(recipe)
    session.commit()
    session.refresh(recipe)
    for comp in data.components:
        food = session.get(Food, comp.food_id)
        if not food:
            raise HTTPException(status_code=400, detail=f"Food {comp.food_id} not found")
        session.add(RecipeComponent(
            recipe_id=recipe.id, food_id=comp.food_id, amount_grams=comp.amount_grams
        ))
    session.commit()
    return _build_recipe_response(recipe, session)


@router.put("/{recipe_id}")
def update_recipe(
    recipe_id: int,
    data: RecipeUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if data.name is not None:
        recipe.name = data.name
    if data.components is not None:
        old = session.exec(
            select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
        ).all()
        for c in old:
            session.delete(c)
        for comp in data.components:
            food = session.get(Food, comp.food_id)
            if not food:
                raise HTTPException(status_code=400, detail=f"Food {comp.food_id} not found")
            session.add(RecipeComponent(
                recipe_id=recipe.id, food_id=comp.food_id, amount_grams=comp.amount_grams
            ))
    session.add(recipe)
    session.commit()
    return _build_recipe_response(recipe, session)


@router.delete("/{recipe_id}", status_code=204)
def delete_recipe(
    recipe_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    components = session.exec(
        select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
    ).all()
    for c in components:
        session.delete(c)
    session.delete(recipe)
    session.commit()
