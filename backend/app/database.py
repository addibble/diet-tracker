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


def _ensure_sqlite_dir():
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


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

    _ensure_sqlite_dir()
    SQLModel.metadata.create_all(engine)


def apply_db_updates():
    """Apply manual schema/data updates and historical backfills."""
    import app.models  # noqa: F401

    _ensure_sqlite_dir()
    _backup_database()
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
    _drop_obsolete_tables()
    _seed_data()
    _backfill_rep_completion()
    _backfill_special_workout_sets()
    _backfill_historical_bodyweight_anchor()
    _backfill_progression_rep_completion()


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
                "external_load_multiplier": "ALTER TABLE exercises ADD COLUMN external_load_multiplier FLOAT DEFAULT 1.0",
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


def _drop_obsolete_tables():
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tissue_recovery_logs"))
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
        seed_reference_exercises,
        seed_tissue_model_configs,
        seed_tissue_recovery_hours,
        seed_tissue_regions,
        seed_tissues,
    )

    with Session(engine) as session:
        seed_tissues(session)
        seed_tissue_regions(session)
        seed_tissue_recovery_hours(session)
        seed_hip_machine_tissues(session)
        seed_reference_exercises(session)
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


def _backfill_special_workout_sets():
    with engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE workout_sets "
                "SET distance_steps = reps * 2 "
                "WHERE distance_steps IS NULL "
                "  AND reps IS NOT NULL "
                "  AND exercise_id IN ("
                "    SELECT id FROM exercises WHERE name = 'Farmers Carry'"
                "  )"
            )
        )
        conn.execute(
            text(
                "UPDATE workout_sets "
                "SET weight = 0.0 "
                "WHERE COALESCE(weight, 0) > 0 "
                "  AND exercise_id IN ("
                "    SELECT id FROM exercises "
                "    WHERE name = 'Reverse Crunch + isometric crunch'"
                "  )"
            )
        )
        conn.commit()


def _backfill_historical_bodyweight_anchor():
    with engine.connect() as conn:
        latest_weight = conn.execute(
            text(
                "SELECT weight_lb FROM weight_logs "
                "ORDER BY logged_at DESC LIMIT 1"
            )
        ).scalar()
        if latest_weight is None:
            return

        earliest_training_date = conn.execute(
            text(
                "SELECT MIN(wses.date) "
                "FROM workout_sets ws "
                "JOIN workout_sessions wses ON wses.id = ws.session_id "
                "JOIN exercises e ON e.id = ws.exercise_id "
                "WHERE e.load_input_mode IN ('bodyweight', 'mixed', 'assisted_bodyweight') "
                "  AND COALESCE(e.bodyweight_fraction, 0) > 0"
            )
        ).scalar()
        if earliest_training_date is None:
            return

        existing = conn.execute(
            text(
                "SELECT 1 FROM weight_logs "
                "WHERE DATE(logged_at) <= :logged_date "
                "LIMIT 1"
            ),
            {"logged_date": earliest_training_date},
        ).scalar()
        if existing:
            return

        conn.execute(
            text(
                "INSERT INTO weight_logs (weight_lb, logged_at) "
                "VALUES (:weight_lb, :logged_at)"
            ),
            {
                "weight_lb": latest_weight,
                "logged_at": f"{earliest_training_date} 12:00:00",
            },
        )
        conn.commit()


def _shared_progression_metric(
    current: dict[str, object],
    next_session: dict[str, object],
) -> tuple[str, float, float] | None:
    for metric_name in ("weights", "steps", "durations", "reps"):
        current_values = current[metric_name]
        next_values = next_session[metric_name]
        if current_values and next_values:
            return (
                metric_name,
                max(current_values),
                max(next_values),
            )
    return None


def _backfill_progression_rep_completion():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ws.id, ws.session_id, ws.exercise_id, wses.date, ws.weight, "
                "       ws.reps, ws.duration_secs, ws.distance_steps, "
                "       ws.rep_completion, e.load_input_mode "
                "FROM workout_sets ws "
                "JOIN workout_sessions wses ON wses.id = ws.session_id "
                "JOIN exercises e ON e.id = ws.exercise_id "
                "WHERE ws.reps IS NOT NULL "
                "   OR ws.duration_secs IS NOT NULL "
                "   OR ws.distance_steps IS NOT NULL "
                "ORDER BY ws.exercise_id, wses.date, ws.session_id, ws.set_order"
            )
        ).fetchall()
        if not rows:
            return

        sessions_by_exercise: dict[int, list[dict[str, object]]] = {}
        session_lookup: dict[tuple[int, int], dict[str, object]] = {}
        for row in rows:
            (
                set_id,
                session_id,
                exercise_id,
                session_date,
                weight,
                reps,
                duration_secs,
                distance_steps,
                rep_completion,
                load_input_mode,
            ) = row
            key = (exercise_id, session_id)
            session_info = session_lookup.get(key)
            if session_info is None:
                session_info = {
                    "session_id": session_id,
                    "date": session_date,
                    "mode": load_input_mode,
                    "weights": [],
                    "reps": [],
                    "durations": [],
                    "steps": [],
                    "set_ids": [],
                }
                session_lookup[key] = session_info
                sessions_by_exercise.setdefault(exercise_id, []).append(session_info)
            if weight is not None and float(weight) > 0:
                session_info["weights"].append(float(weight))
            if reps is not None and int(reps) > 0:
                session_info["reps"].append(float(reps))
            if duration_secs is not None and int(duration_secs) > 0:
                session_info["durations"].append(float(duration_secs))
            if distance_steps is not None and int(distance_steps) > 0:
                session_info["steps"].append(float(distance_steps))
            if rep_completion is None:
                session_info["set_ids"].append(int(set_id))

        changed = False
        for exercise_sessions in sessions_by_exercise.values():
            exercise_sessions.sort(
                key=lambda item: (item["date"], item["session_id"])
            )
            for index, current in enumerate(exercise_sessions[:-1]):
                next_session = exercise_sessions[index + 1]
                set_ids = current["set_ids"]
                metric = _shared_progression_metric(current, next_session)
                if metric is None or not set_ids:
                    continue
                metric_name, current_value, next_value = metric
                if metric_name == "weights" and current["mode"] == "assisted_bodyweight":
                    delta = current_value - next_value
                else:
                    delta = next_value - current_value
                if delta > 0.001:
                    status = "full"
                elif delta < -0.001:
                    status = "failed"
                else:
                    status = "partial"
                for set_id in set_ids:
                    conn.execute(
                        text(
                            "UPDATE workout_sets SET rep_completion = :status "
                            "WHERE id = :id"
                        ),
                        {"status": status, "id": set_id},
                    )
                changed = True
        if changed:
            conn.commit()
