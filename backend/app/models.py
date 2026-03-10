from datetime import UTC, date, datetime

from sqlalchemy import UniqueConstraint
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


class Workout(SQLModel, table=True):
    __tablename__ = "workouts"
    id: int | None = Field(default=None, primary_key=True)
    # Client-provided deduplication key (e.g. "{date}T{start_time}_{type}")
    sync_key: str = Field(unique=True, index=True)
    date: date
    workout_type: str          # "Running", "Cycling", "Strength Training", etc.
    duration_minutes: float
    active_calories: float     # calories burned during the workout
    total_calories: float | None = None   # active + resting during workout
    distance_km: float | None = None
    source: str | None = None  # "Apple Watch", "iPhone", etc.
    created_at: datetime = Field(default_factory=_utcnow)


class MealItemOverride(SQLModel, table=True):
    __tablename__ = "meal_item_overrides"
    id: int | None = Field(default=None, primary_key=True)
    meal_item_id: int = Field(foreign_key="meal_items.id")
    original_food_id: int = Field(foreign_key="foods.id")
    replacement_food_id: int = Field(foreign_key="foods.id")
    replacement_grams: float


class WeightLog(SQLModel, table=True):
    __tablename__ = "weight_logs"
    id: int | None = Field(default=None, primary_key=True)
    weight_lb: float
    logged_at: datetime = Field(default_factory=_utcnow, index=True)


# ── Workout Tracking Models ──


class Exercise(SQLModel, table=True):
    __tablename__ = "exercises"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    equipment: str | None = None  # dumbbell, cable, barbell, machine, bodyweight, etc.
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Tissue(SQLModel, table=True):
    __tablename__ = "tissues"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    display_name: str
    type: str = "muscle"  # "muscle", "tendon", "joint"
    recovery_hours: float = 48.0
    notes: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class ExerciseTissue(SQLModel, table=True):
    __tablename__ = "exercise_tissues"
    __table_args__ = (
        UniqueConstraint("exercise_id", "tissue_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    exercise_id: int = Field(foreign_key="exercises.id")
    tissue_id: int = Field(foreign_key="tissues.id")
    role: str = "primary"  # "primary", "secondary", "stabilizer"
    loading_factor: float = 1.0  # 0.0-1.0
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkoutSession(SQLModel, table=True):
    __tablename__ = "workout_sessions"
    id: int | None = Field(default=None, primary_key=True)
    date: date
    started_at: datetime | None = None
    finished_at: datetime | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class WorkoutSet(SQLModel, table=True):
    __tablename__ = "workout_sets"
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="workout_sessions.id")
    exercise_id: int = Field(foreign_key="exercises.id")
    set_order: int
    reps: int | None = None  # null for timed sets
    weight: float | None = None  # lbs, null for bodyweight
    duration_secs: int | None = None
    distance_steps: int | None = None
    rpe: float | None = None  # 1-10
    rep_completion: str | None = None  # "full", "partial", "failed"
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class RoutineExercise(SQLModel, table=True):
    __tablename__ = "routine_exercises"
    id: int | None = Field(default=None, primary_key=True)
    exercise_id: int = Field(foreign_key="exercises.id")
    target_sets: int
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    sort_order: int = 0
    active: int = 1  # 0 = temporarily disabled
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class TissueCondition(SQLModel, table=True):
    """LOG TABLE: append-only. Query latest per tissue_id for current state."""
    __tablename__ = "tissue_conditions"
    id: int | None = Field(default=None, primary_key=True)
    tissue_id: int = Field(foreign_key="tissues.id")
    status: str  # "healthy", "tender", "injured", "rehabbing"
    severity: int = 0  # 0-4
    max_loading_factor: float | None = None
    recovery_hours_override: float | None = None
    rehab_protocol: str | None = None
    notes: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class MacroTarget(SQLModel, table=True):
    __tablename__ = "macro_targets"
    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(index=True, unique=True)
    calories: float
    fat: float
    saturated_fat: float
    cholesterol: float
    sodium: float
    carbs: float
    fiber: float
    protein: float
    created_at: datetime = Field(default_factory=_utcnow)
