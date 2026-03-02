from datetime import UTC, date, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Food(SQLModel, table=True):
    __tablename__ = "foods"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    brand: str | None = Field(default=None)
    serving_size_grams: float = 100
    calories_per_serving: float
    fat_per_serving: float
    saturated_fat_per_serving: float = 0
    cholesterol_per_serving: float = 0  # mg
    sodium_per_serving: float = 0  # mg
    carbs_per_serving: float
    fiber_per_serving: float = 0
    protein_per_serving: float
    source: str = Field(default="custom")  # "usda" or "custom"
    created_at: datetime = Field(default_factory=_utcnow)


class Recipe(SQLModel, table=True):
    __tablename__ = "recipes"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class RecipeComponent(SQLModel, table=True):
    __tablename__ = "recipe_components"
    id: int | None = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipes.id")
    food_id: int = Field(foreign_key="foods.id")
    amount_grams: float


class MealLog(SQLModel, table=True):
    __tablename__ = "meal_logs"
    id: int | None = Field(default=None, primary_key=True)
    date: date
    meal_type: str  # "breakfast", "lunch", "dinner", "snack"
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MealItem(SQLModel, table=True):
    __tablename__ = "meal_items"
    id: int | None = Field(default=None, primary_key=True)
    meal_log_id: int = Field(foreign_key="meal_logs.id")
    food_id: int | None = Field(default=None, foreign_key="foods.id")
    recipe_id: int | None = Field(default=None, foreign_key="recipes.id")
    amount_grams: float


class MealItemOverride(SQLModel, table=True):
    __tablename__ = "meal_item_overrides"
    id: int | None = Field(default=None, primary_key=True)
    meal_item_id: int = Field(foreign_key="meal_items.id")
    original_food_id: int = Field(foreign_key="foods.id")
    replacement_food_id: int = Field(foreign_key="foods.id")
    replacement_grams: float
