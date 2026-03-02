from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.models import Food

router = APIRouter(prefix="/api/foods", tags=["foods"])


class FoodCreate(BaseModel):
    name: str
    brand: str | None = None
    serving_size_grams: float = 100
    calories_per_serving: float
    fat_per_serving: float
    saturated_fat_per_serving: float = 0
    cholesterol_per_serving: float = 0
    sodium_per_serving: float = 0
    carbs_per_serving: float
    fiber_per_serving: float = 0
    protein_per_serving: float
    source: str = "custom"


class FoodUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    serving_size_grams: float | None = None
    calories_per_serving: float | None = None
    fat_per_serving: float | None = None
    saturated_fat_per_serving: float | None = None
    cholesterol_per_serving: float | None = None
    sodium_per_serving: float | None = None
    carbs_per_serving: float | None = None
    fiber_per_serving: float | None = None
    protein_per_serving: float | None = None
    source: str | None = None


@router.get("")
def list_foods(
    search: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(Food)
    if search:
        stmt = stmt.where(
            Food.name.contains(search) | Food.brand.contains(search)  # type: ignore[union-attr]
        )
    stmt = stmt.order_by(Food.name)
    return session.exec(stmt).all()


@router.get("/{food_id}")
def get_food(
    food_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    return food


@router.post("", status_code=201)
def create_food(
    data: FoodCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = Food(**data.model_dump())
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


@router.put("/{food_id}")
def update_food(
    food_id: int,
    data: FoodUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(food, key, value)
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


@router.delete("/{food_id}", status_code=204)
def delete_food(
    food_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    session.delete(food)
    session.commit()
