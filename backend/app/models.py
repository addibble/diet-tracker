import datetime as dt
from datetime import UTC, datetime

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
    date: dt.date
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
    date: dt.date
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
    load_input_mode: str = "external_weight"
    laterality: str = "bilateral"  # "bilateral", "unilateral", "either"
    bodyweight_fraction: float = 0.0
    external_load_multiplier: float = 1.0
    variant_group: str | None = Field(default=None, index=True)
    grip_style: str = "none"  # "none", "neutral", "pronated", "supinated", "mixed"
    grip_width: str = "none"  # "none", "narrow", "shoulder_width", "wide", "variable"
    support_style: str = "none"  # "none", "unsupported", "chest_supported", "bench_supported", "cable_stabilized", "machine"
    set_metric_mode: str = "reps"  # "reps", "duration", "distance", "hybrid"
    estimated_minutes_per_set: float = 2.0
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Tissue(SQLModel, table=True):
    __tablename__ = "tissues"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    display_name: str
    type: str = "muscle"  # "muscle", "tendon", "joint"
    region: str = "other"  # shoulders, upper_back, lower_back, chest, hips, knees, quads, hamstrings, calves, arms, core, neck, ankles, other
    tracking_mode: str = "paired"  # "paired", "center"
    recovery_hours: float = 48.0
    notes: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class TrackedTissue(SQLModel, table=True):
    __tablename__ = "tracked_tissues"
    __table_args__ = (
        UniqueConstraint("tissue_id", "side"),
    )
    id: int | None = Field(default=None, primary_key=True)
    tissue_id: int = Field(foreign_key="tissues.id", index=True)
    side: str = Field(index=True)  # "left", "right", "center"
    display_name: str
    active: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
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
    routing_factor: float = 1.0
    fatigue_factor: float = 1.0
    joint_strain_factor: float = 1.0
    tendon_strain_factor: float = 1.0
    laterality_mode: str = "bilateral_equal"  # "bilateral_equal", "selected_side_only", "selected_side_primary", "contralateral_carryover"
    updated_at: datetime = Field(default_factory=_utcnow)


class TissueRelationship(SQLModel, table=True):
    __tablename__ = "tissue_relationships"
    __table_args__ = (
        UniqueConstraint("source_tissue_id", "target_tissue_id", "relationship_type"),
    )
    id: int | None = Field(default=None, primary_key=True)
    source_tissue_id: int = Field(foreign_key="tissues.id", index=True)
    target_tissue_id: int = Field(foreign_key="tissues.id", index=True)
    relationship_type: str = Field(index=True)  # "muscle_to_tendon", "tendon_to_joint", "shares_neural_pathway", "agonist_chain"
    required_for_mapping_warning: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class WorkoutSession(SQLModel, table=True):
    __tablename__ = "workout_sessions"
    id: int | None = Field(default=None, primary_key=True)
    date: dt.date
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
    performed_side: str | None = None  # "left", "right", "center", "bilateral"
    reps: int | None = None  # null for timed sets
    weight: float | None = None  # lbs, null for bodyweight
    duration_secs: int | None = None
    distance_steps: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rpe: float | None = None  # 1-10
    rep_completion: str | None = None  # "full", "partial", "failed"
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class WorkoutSetTissueFeedback(SQLModel, table=True):
    __tablename__ = "workout_set_tissue_feedback"
    __table_args__ = (
        UniqueConstraint("workout_set_id", "tracked_tissue_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    workout_set_id: int = Field(foreign_key="workout_sets.id", index=True)
    tracked_tissue_id: int = Field(foreign_key="tracked_tissues.id", index=True)
    pain_0_10: int = 0
    symptom_note: str | None = None
    recorded_at: datetime = Field(default_factory=_utcnow, index=True)


class TissueCondition(SQLModel, table=True):
    """LOG TABLE: append-only. Query latest per tissue_id for current state."""
    __tablename__ = "tissue_conditions"
    id: int | None = Field(default=None, primary_key=True)
    tissue_id: int = Field(foreign_key="tissues.id")
    tracked_tissue_id: int | None = Field(default=None, foreign_key="tracked_tissues.id")
    status: str  # "healthy", "tender", "injured", "rehabbing"
    severity: int = 0  # 0-4
    max_loading_factor: float | None = None
    recovery_hours_override: float | None = None
    rehab_protocol: str | None = None
    notes: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class TissueModelConfig(SQLModel, table=True):
    __tablename__ = "tissue_model_configs"
    tissue_id: int = Field(foreign_key="tissues.id", primary_key=True)
    capacity_prior: float = 1.0
    recovery_tau_days: float = 3.0
    fatigue_tau_days: float = 2.0
    collapse_drop_threshold: float = 0.45
    ramp_sensitivity: float = 1.0
    risk_sensitivity: float = 1.0
    updated_at: datetime = Field(default_factory=_utcnow)


class TrainingExclusionWindow(SQLModel, table=True):
    __tablename__ = "training_exclusion_windows"
    __table_args__ = (UniqueConstraint("start_date", "end_date", "kind"),)
    id: int | None = Field(default=None, primary_key=True)
    start_date: dt.date = Field(index=True)
    end_date: dt.date = Field(index=True)
    kind: str
    notes: str | None = None
    exclude_from_model: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class RecoveryCheckIn(SQLModel, table=True):
    __tablename__ = "recovery_check_ins"
    id: int | None = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    region: str = Field(index=True)  # body region
    soreness_0_10: int = 0
    pain_0_10: int = 0
    stiffness_0_10: int = 0
    readiness_0_10: int = 5
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class RehabPlan(SQLModel, table=True):
    __tablename__ = "rehab_plans"
    id: int | None = Field(default=None, primary_key=True)
    tracked_tissue_id: int = Field(foreign_key="tracked_tissues.id", index=True)
    protocol_id: str = Field(index=True)
    stage_id: str = Field(index=True)
    status: str = Field(default="active", index=True)  # "active", "paused", "completed"
    pain_monitoring_threshold: int = 3
    max_next_day_flare: int = 2
    sessions_per_week_target: float | None = None
    max_weekly_set_progression: int | None = None
    max_load_progression_pct: float | None = None
    notes: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RehabCheckIn(SQLModel, table=True):
    __tablename__ = "rehab_check_ins"
    id: int | None = Field(default=None, primary_key=True)
    tracked_tissue_id: int = Field(foreign_key="tracked_tissues.id", index=True)
    rehab_plan_id: int | None = Field(default=None, foreign_key="rehab_plans.id")
    pain_0_10: int = 0
    stiffness_0_10: int = 0
    weakness_0_10: int = 0
    neural_symptoms_0_10: int = 0
    during_load_pain_0_10: int = 0
    next_day_flare: int = 0
    confidence_0_10: int = 5
    notes: str | None = None
    recorded_at: datetime = Field(default_factory=_utcnow, index=True)


class TrainingProgram(SQLModel, table=True):
    __tablename__ = "training_programs"
    id: int | None = Field(default=None, primary_key=True)
    name: str
    active: int = 1  # 0 = inactive
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ProgramDay(SQLModel, table=True):
    __tablename__ = "program_days"
    id: int | None = Field(default=None, primary_key=True)
    program_id: int = Field(foreign_key="training_programs.id")
    day_label: str  # "A", "B", "Push", "Pull", "Upper", "Lower"
    target_regions: str | None = None  # JSON list: ["chest", "shoulders", "arms"]
    sort_order: int = 0
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ProgramDayExercise(SQLModel, table=True):
    __tablename__ = "program_day_exercises"
    __table_args__ = (UniqueConstraint("program_day_id", "exercise_id"),)
    id: int | None = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="program_days.id")
    exercise_id: int = Field(foreign_key="exercises.id")
    target_sets: int = 3
    target_rep_min: int | None = None
    target_rep_max: int | None = None
    sort_order: int = 0
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class PlannedSession(SQLModel, table=True):
    __tablename__ = "planned_sessions"
    id: int | None = Field(default=None, primary_key=True)
    program_day_id: int = Field(foreign_key="program_days.id")
    date: dt.date = Field(index=True)
    status: str = "planned"  # planned, completed, skipped
    workout_session_id: int | None = Field(
        default=None, foreign_key="workout_sessions.id"
    )
    notes: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MacroTarget(SQLModel, table=True):
    __tablename__ = "macro_targets"
    id: int | None = Field(default=None, primary_key=True)
    day: dt.date = Field(index=True, unique=True)
    calories: float
    fat: float
    saturated_fat: float
    cholesterol: float
    sodium: float
    carbs: float
    fiber: float
    protein: float
    created_at: datetime = Field(default_factory=_utcnow)
