from collections.abc import Generator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(settings.database_url, echo=False)


def create_db_and_tables():
    import app.models  # noqa: F401

    # Ensure the directory for the SQLite file exists
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
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
