import logging
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, echo=False)


def _backup_database():
    """Copy the SQLite file to a timestamped backup before migrations."""
    if not settings.database_url.startswith("sqlite"):
        return
    db_path = Path(settings.database_url.split("///")[-1])
    if not db_path.exists():
        return
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.{ts}")
    shutil.copy2(db_path, backup_path)
    logger.info("Database backup: %s", backup_path)


def create_db_and_tables():
    import app.models  # noqa: F401

    # Ensure the directory for the SQLite file exists
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _backup_database()
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
    _backfill_rep_completion()
    _seed_data()


def _migrate_add_columns():
    """Add new columns and clean up legacy data (no Alembic)."""
    insp = inspect(engine)
    table_names = insp.get_table_names()

    if "exercises" in table_names:
        _ensure_columns(
            "exercises",
            {
                "load_input_mode": "ALTER TABLE exercises ADD COLUMN load_input_mode TEXT DEFAULT 'external_weight'",
                "bodyweight_fraction": "ALTER TABLE exercises ADD COLUMN bodyweight_fraction FLOAT DEFAULT 0.0",
                "estimated_minutes_per_set": "ALTER TABLE exercises ADD COLUMN estimated_minutes_per_set FLOAT DEFAULT 2.0",
            },
            insp,
        )

    if "exercise_tissues" in table_names:
        _ensure_columns(
            "exercise_tissues",
            {
                "routing_factor": "ALTER TABLE exercise_tissues ADD COLUMN routing_factor FLOAT DEFAULT 1.0",
                "fatigue_factor": "ALTER TABLE exercise_tissues ADD COLUMN fatigue_factor FLOAT DEFAULT 1.0",
                "joint_strain_factor": "ALTER TABLE exercise_tissues ADD COLUMN joint_strain_factor FLOAT DEFAULT 1.0",
                "tendon_strain_factor": "ALTER TABLE exercise_tissues ADD COLUMN tendon_strain_factor FLOAT DEFAULT 1.0",
            },
            insp,
        )

    if "tissues" in table_names:
        _ensure_columns(
            "tissues",
            {
                "region": "ALTER TABLE tissues ADD COLUMN region TEXT DEFAULT 'other'",
            },
            insp,
        )

    if "foods" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("foods")}
        if "brand" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE foods ADD COLUMN brand TEXT"))
                conn.commit()

    # Clean up legacy tissue groups and deduplicate
    if "tissues" in insp.get_table_names():
        with engine.connect() as conn:
            # Delete exercise_tissue rows pointing to group tissues
            conn.execute(text(
                "DELETE FROM exercise_tissues WHERE tissue_id IN "
                "(SELECT id FROM tissues WHERE type IN ('tissue_group', 'muscle_group'))"
            ))
            # Delete group tissues
            conn.execute(text(
                "DELETE FROM tissues WHERE type IN ('tissue_group', 'muscle_group')"
            ))
            # Deduplicate tissues: keep latest row per name, delete older duplicates
            conn.execute(text(
                "DELETE FROM tissues WHERE id NOT IN "
                "(SELECT MAX(id) FROM tissues GROUP BY name)"
            ))
            # Deduplicate exercise_tissues: keep latest row per (exercise_id, tissue_id)
            conn.execute(text(
                "DELETE FROM exercise_tissues WHERE id NOT IN "
                "(SELECT MAX(id) FROM exercise_tissues "
                "GROUP BY exercise_id, tissue_id)"
            ))
            conn.commit()


def _backfill_rep_completion():
    """Backfill rep_completion on workout_sets using program_day_exercises targets.

    For each set with reps but NULL rep_completion, trace:
    workout_set → workout_session → planned_session → program_day_exercises
    to find the target rep range and compute completion status.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ws.id, ws.reps, pde.target_rep_min, pde.target_rep_max "
            "FROM workout_sets ws "
            "JOIN workout_sessions wses ON wses.id = ws.session_id "
            "JOIN planned_sessions ps ON ps.workout_session_id = wses.id "
            "JOIN program_day_exercises pde "
            "  ON pde.program_day_id = ps.program_day_id "
            "  AND pde.exercise_id = ws.exercise_id "
            "WHERE ws.reps IS NOT NULL "
            "  AND ws.rep_completion IS NULL "
            "  AND pde.target_rep_min IS NOT NULL "
            "  AND pde.target_rep_max IS NOT NULL"
        )).fetchall()
        if not rows:
            return
        for ws_id, reps, rep_min, rep_max in rows:
            if reps >= rep_max:
                status = "full"
            elif reps >= rep_min:
                status = "partial"
            else:
                status = "failed"
            conn.execute(
                text(
                    "UPDATE workout_sets SET rep_completion = :status "
                    "WHERE id = :id"
                ),
                {"status": status, "id": ws_id},
            )
        conn.commit()


def _seed_data():
    """Seed reference data after table creation."""
    from app.seed_tissues import (
        seed_default_training_exclusion_windows,
        seed_exercise_tissue_model_defaults,
        seed_hip_machine_tissues,
        seed_tissue_model_configs,
        seed_tissue_regions,
        seed_tissues,
    )

    with Session(engine) as session:
        seed_tissues(session)
        seed_tissue_regions(session)
        seed_hip_machine_tissues(session)
        seed_exercise_tissue_model_defaults(session)
        seed_tissue_model_configs(session)
        seed_default_training_exclusion_windows(session)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def _ensure_columns(
    table_name: str,
    statements_by_column: dict[str, str],
    insp,
) -> None:
    cols = {c["name"] for c in insp.get_columns(table_name)}
    missing = [sql for col, sql in statements_by_column.items() if col not in cols]
    if not missing:
        return
    with engine.connect() as conn:
        for sql in missing:
            conn.execute(text(sql))
        conn.commit()
