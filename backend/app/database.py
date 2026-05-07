import logging
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

logger = logging.getLogger(__name__)

RUNTIME_REQUIRED_TABLES = {
    "tissue_region_links",
    "chart_cache",
}

RUNTIME_REQUIRED_COLUMNS = {
    "exercises": {
        "allow_heavy_loading",
        "load_input_mode",
        "laterality",
        "bodyweight_fraction",
        "external_load_multiplier",
        "variant_group",
        "grip_style",
        "grip_width",
        "support_style",
        "set_metric_mode",
        "estimated_minutes_per_set",
        "curve_delta",
    },
    "exercise_tissues": {
        "routing_factor",
        "fatigue_factor",
        "joint_strain_factor",
        "tendon_strain_factor",
        "laterality_mode",
    },
    "tissues": {
        "region",
        "tracking_mode",
    },
    "workout_sets": {
        "performed_side",
        "started_at",
        "completed_at",
        "training_mode",
        "endurance_value",
    },
    "workout_sessions": {
        "readiness_beta",
    },
    "tissue_conditions": {
        "tracked_tissue_id",
    },
    "recovery_check_ins": {
        "tracked_tissue_id",
    },
}


RUNTIME_REQUIRED_COLUMNS_ABSENT = {
    # Columns that MUST NOT exist anymore. If present, the runtime updater
    # runs to drop them. Used to drive Phase 6's one-shot DROP COLUMN.
    "workout_sets": {"reps", "duration_secs", "distance_steps"},
}


def _engine_sqlite_path(engine: Engine) -> Path | None:
    url = engine.url
    if url.drivername.startswith("sqlite"):
        if url.database and url.database != ":memory:":
            return Path(url.database)
    return None


def _ensure_sqlite_dir(engine: Engine) -> None:
    path = _engine_sqlite_path(engine)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def _backup_database(engine: Engine) -> None:
    """Copy the SQLite file to a timestamped backup before migrations."""
    path = _engine_sqlite_path(engine)
    if path is None or not path.exists():
        return
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.{ts}")
    shutil.copy2(path, backup_path)
    logger.info("Database backup: %s", backup_path)
    _rotate_backups(path)


def _rotate_backups(path: Path, keep: int = 10) -> None:
    """Keep only the most recent *keep* timestamped backups for *path*."""

    prefix = f"{path.name}."
    candidates = sorted(
        (p for p in path.parent.glob(f"{path.name}.*") if p.name.startswith(prefix)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            logger.exception("Failed to rotate backup %s", stale)


def create_db_and_tables(engine: Engine) -> None:
    import app.models  # noqa: F401
    from app.auth_models import AUTH_TABLE_NAMES

    _ensure_sqlite_dir(engine)
    data_tables = [
        t for name, t in SQLModel.metadata.tables.items() if name not in AUTH_TABLE_NAMES
    ]
    SQLModel.metadata.create_all(engine, tables=data_tables)


def ensure_runtime_db_ready(engine: Engine) -> None:
    import app.models  # noqa: F401

    create_db_and_tables(engine)
    if _runtime_db_needs_manual_updates(engine):
        logger.info("Applying pending runtime database updates")
        apply_db_updates(engine)


def apply_db_updates(engine: Engine) -> None:
    """Apply manual schema/data updates and historical backfills."""
    import app.models  # noqa: F401

    _ensure_sqlite_dir(engine)
    _backup_database(engine)
    create_db_and_tables(engine)
    _migrate_add_columns(engine)
    _drop_obsolete_tables(engine)
    _seed_data(engine)
    _backfill_heavy_loading_defaults(engine)
    _backfill_rep_completion(engine)
    _backfill_special_workout_sets(engine)
    _backfill_historical_bodyweight_anchor(engine)
    _backfill_progression_rep_completion(engine)
    _migrate_legacy_metric_modes(engine)
    _backfill_endurance_value(engine)
    _drop_legacy_metric_columns(engine)


def _runtime_db_needs_manual_updates(engine: Engine) -> bool:
    insp = inspect(engine)
    table_names = set(insp.get_table_names())

    if not table_names:
        logger.info("Database has no tables yet; runtime updates required")
        return True

    missing_tables = sorted(RUNTIME_REQUIRED_TABLES - table_names)
    if missing_tables:
        logger.info("Database missing runtime tables: %s", ", ".join(missing_tables))
        return True

    for table_name, required_columns in RUNTIME_REQUIRED_COLUMNS.items():
        if table_name not in table_names:
            logger.info("Database missing required table: %s", table_name)
            return True
        existing_columns = {column["name"] for column in insp.get_columns(table_name)}
        missing_columns = sorted(required_columns - existing_columns)
        if missing_columns:
            logger.info(
                "Database table %s missing runtime columns: %s",
                table_name,
                ", ".join(missing_columns),
            )
            return True

    for table_name, absent_columns in RUNTIME_REQUIRED_COLUMNS_ABSENT.items():
        if table_name not in table_names:
            continue
        existing_columns = {column["name"] for column in insp.get_columns(table_name)}
        present = sorted(absent_columns & existing_columns)
        if present:
            logger.info(
                "Database table %s has obsolete columns to drop: %s",
                table_name,
                ", ".join(present),
            )
            return True

    with engine.connect() as conn:
        exercise_count = conn.execute(text("SELECT COUNT(*) FROM exercises")).scalar() or 0
        tissue_count = conn.execute(text("SELECT COUNT(*) FROM tissues")).scalar() or 0

    if exercise_count == 0 or tissue_count == 0:
        logger.info(
            "Database missing seeded reference data (exercises=%s tissues=%s)",
            exercise_count,
            tissue_count,
        )
        return True

    # Trigger the seed pipeline whenever any tissue carries a non-canonical
    # region value. This catches cases where the canonical set in
    # tissue_regions.py has been refined (regions consolidated/renamed) but
    # the DB still holds the old values. The seed functions are idempotent
    # and cheap, so running them on each such boot is safe.
    from app.tissue_regions import CANONICAL_REGION_ORDER

    with engine.connect() as conn:
        distinct_regions = {
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT region FROM tissues WHERE region IS NOT NULL")
            )
        }
    non_canonical = distinct_regions - set(CANONICAL_REGION_ORDER)
    if non_canonical:
        logger.info(
            "Database has non-canonical tissue regions: %s",
            ", ".join(sorted(non_canonical)),
        )
        return True

    return False


def _migrate_add_columns(engine: Engine):
    """Add new columns and clean up legacy data (no Alembic)."""
    insp = inspect(engine)
    table_names = insp.get_table_names()

    if "exercises" in table_names:
        _ensure_columns(
            "exercises",
            {
                "allow_heavy_loading": "ALTER TABLE exercises ADD COLUMN allow_heavy_loading BOOLEAN DEFAULT 1",
                "load_input_mode": "ALTER TABLE exercises ADD COLUMN load_input_mode TEXT DEFAULT 'external_weight'",
                "laterality": "ALTER TABLE exercises ADD COLUMN laterality TEXT DEFAULT 'bilateral'",
                "bodyweight_fraction": "ALTER TABLE exercises ADD COLUMN bodyweight_fraction FLOAT DEFAULT 0.0",
                "external_load_multiplier": "ALTER TABLE exercises ADD COLUMN external_load_multiplier FLOAT DEFAULT 1.0",
                "variant_group": "ALTER TABLE exercises ADD COLUMN variant_group TEXT",
                "grip_style": "ALTER TABLE exercises ADD COLUMN grip_style TEXT DEFAULT 'none'",
                "grip_width": "ALTER TABLE exercises ADD COLUMN grip_width TEXT DEFAULT 'none'",
                "support_style": "ALTER TABLE exercises ADD COLUMN support_style TEXT DEFAULT 'none'",
                "set_metric_mode": "ALTER TABLE exercises ADD COLUMN set_metric_mode TEXT DEFAULT 'reps'",
                "estimated_minutes_per_set": "ALTER TABLE exercises ADD COLUMN estimated_minutes_per_set FLOAT DEFAULT 2.0",
                "curve_delta": "ALTER TABLE exercises ADD COLUMN curve_delta FLOAT DEFAULT 0.0",
            },
            insp,
            engine,
        )

    if "exercise_tissues" in table_names:
        _ensure_columns(
            "exercise_tissues",
            {
                "routing_factor": "ALTER TABLE exercise_tissues ADD COLUMN routing_factor FLOAT DEFAULT 1.0",
                "fatigue_factor": "ALTER TABLE exercise_tissues ADD COLUMN fatigue_factor FLOAT DEFAULT 1.0",
                "joint_strain_factor": "ALTER TABLE exercise_tissues ADD COLUMN joint_strain_factor FLOAT DEFAULT 1.0",
                "tendon_strain_factor": "ALTER TABLE exercise_tissues ADD COLUMN tendon_strain_factor FLOAT DEFAULT 1.0",
                "laterality_mode": "ALTER TABLE exercise_tissues ADD COLUMN laterality_mode TEXT DEFAULT 'bilateral_equal'",
            },
            insp,
            engine,
        )

    if "tissues" in table_names:
        _ensure_columns(
            "tissues",
            {
                "region": "ALTER TABLE tissues ADD COLUMN region TEXT DEFAULT 'core'",
                "tracking_mode": "ALTER TABLE tissues ADD COLUMN tracking_mode TEXT DEFAULT 'paired'",
            },
            insp,
            engine,
        )

    if "workout_sets" in table_names:
        _ensure_columns(
            "workout_sets",
            {
                "performed_side": "ALTER TABLE workout_sets ADD COLUMN performed_side TEXT",
                "started_at": "ALTER TABLE workout_sets ADD COLUMN started_at TIMESTAMP",
                "completed_at": "ALTER TABLE workout_sets ADD COLUMN completed_at TIMESTAMP",
                "training_mode": "ALTER TABLE workout_sets ADD COLUMN training_mode TEXT",
                "endurance_value": "ALTER TABLE workout_sets ADD COLUMN endurance_value REAL",
            },
            insp,
            engine,
        )

    if "workout_sessions" in table_names:
        _ensure_columns(
            "workout_sessions",
            {
                "readiness_beta": "ALTER TABLE workout_sessions ADD COLUMN readiness_beta REAL",
            },
            insp,
            engine,
        )

    if "tissue_conditions" in table_names:
        _ensure_columns(
            "tissue_conditions",
            {
                "tracked_tissue_id": "ALTER TABLE tissue_conditions ADD COLUMN tracked_tissue_id INTEGER",
            },
            insp,
            engine,
        )

    if "recovery_check_ins" in table_names:
        _ensure_columns(
            "recovery_check_ins",
            {
                "tracked_tissue_id": "ALTER TABLE recovery_check_ins ADD COLUMN tracked_tissue_id INTEGER",
            },
            insp,
            engine,
        )

    if "foods" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("foods")}
        if "brand" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE foods ADD COLUMN brand TEXT"))

    # Clean up legacy tissue groups and deduplicate
    if "tissues" in insp.get_table_names():
        with engine.begin() as conn:
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


def _drop_obsolete_tables(engine: Engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tissue_recovery_logs"))


def _backfill_rep_completion(engine: Engine):
    """Backfill rep_completion on workout_sets using program_day_exercises targets.

    For each set with reps but NULL rep_completion, trace:
    workout_set → workout_session → planned_session → program_day_exercises
    to find the target rep range and compute completion status.

    No-op once the legacy ``reps`` column has been dropped (Phase 6+).
    """
    insp = inspect(engine)
    if "workout_sets" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("workout_sets")}
    if "reps" not in cols:
        return
    with engine.begin() as conn:
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


def _seed_data(engine: Engine):
    """Seed reference data after table creation."""
    from app.seed_tissues import (
        load_seed_sql,
        seed_default_training_exclusion_windows,
        seed_exercise_laterality_defaults,
        seed_exercise_tissue_model_defaults,
        seed_hip_machine_tissues,
        seed_reference_exercises,
        seed_tissue_model_configs,
        seed_tissue_recovery_hours,
        seed_tissue_region_links,
        seed_tissue_regions,
        seed_tissue_relationship_defaults,
        seed_tissues,
        seed_tracked_tissue_defaults,
    )

    with Session(engine) as session:
        # Bulk-load catalog rows from production snapshot (idempotent,
        # only runs on fresh DB). Provides the bulk of tissues, exercises,
        # mappings, and model configs without running the large hardcoded
        # Python seeds.
        load_seed_sql(session)
        # Fixups and rows for tables not captured by the SQL dump
        # (tissue_region_links, tissue_relationships). All functions are
        # idempotent upserts.
        seed_tissues(session)
        seed_tissue_regions(session)
        seed_tissue_region_links(session)
        seed_tissue_recovery_hours(session)
        seed_hip_machine_tissues(session)
        seed_reference_exercises(session)
        seed_exercise_laterality_defaults(session)
        seed_exercise_tissue_model_defaults(session)
        seed_tissue_relationship_defaults(session)
        seed_tissue_model_configs(session)
        seed_tracked_tissue_defaults(session)
        seed_default_training_exclusion_windows(session)


def get_session(
    user_id: str = Depends(lambda: _require_auth_wired()),  # noqa: B008
) -> Generator[Session, None, None]:
    """FastAPI dep: yield a Session bound to the current user's engine.

    The default value is a lambda that raises until ``_wire_auth_dep`` swaps
    in the real ``get_current_user_id`` dependency. Tests that override
    ``get_session`` via ``app.dependency_overrides`` bypass this entirely.
    """

    from app.db_engines import user_engine

    eng = user_engine(user_id)
    with Session(eng) as session:
        yield session


def _require_auth_wired() -> str:  # pragma: no cover - replaced at startup
    raise RuntimeError(
        "get_session was invoked before _wire_auth_dep() ran; "
        "ensure app.main calls it at startup."
    )


def _wire_auth_dep() -> None:
    """Re-bind the user_id dep on ``get_session`` to ``get_current_user_id``.

    Called once from ``app.main`` after all modules (including ``app.auth``)
    have been imported, so we avoid the circular-import risk of pulling
    ``app.auth`` at module load time.
    """

    import inspect as _inspect

    from app.auth import get_current_user_id

    sig = _inspect.signature(get_session)
    new_params = [
        p.replace(default=Depends(get_current_user_id)) if p.name == "user_id" else p
        for p in sig.parameters.values()
    ]
    get_session.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]


def get_session_for_user(user_id: str) -> Generator[Session, None, None]:
    """Helper for non-request contexts (scripts, tests, bootstrap)."""

    from app.db_engines import user_engine

    eng = user_engine(user_id)
    with Session(eng) as session:
        yield session


def _ensure_columns(
    table_name: str,
    statements_by_column: dict[str, str],
    insp,
    engine: Engine,
) -> None:
    cols = {c["name"] for c in insp.get_columns(table_name)}
    missing = [sql for col, sql in statements_by_column.items() if col not in cols]
    if not missing:
        return
    with engine.begin() as conn:
        for sql in missing:
            conn.execute(text(sql))


def _backfill_special_workout_sets(engine: Engine):
    insp = inspect(engine)
    if "workout_sets" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("workout_sets")}
    if not {"reps", "distance_steps"}.issubset(cols):
        return
    with engine.begin() as conn:
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


def _backfill_historical_bodyweight_anchor(engine: Engine):
    with engine.begin() as conn:
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


def _backfill_heavy_loading_defaults(engine: Engine):
    from app.exercise_rules import backfill_heavy_loading_defaults

    with Session(engine) as runtime_session:
        backfill_heavy_loading_defaults(runtime_session)


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


def _backfill_progression_rep_completion(engine: Engine):
    insp = inspect(engine)
    if "workout_sets" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("workout_sets")}
    if not ({"reps", "duration_secs", "distance_steps"} & cols):
        return
    with engine.begin() as conn:
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
            pass  # auto-commits via engine.begin() context manager


def _backfill_tracked_tissue_foundation():
    """Deprecated: rehab subsystem removed. Kept as no-op for safety."""
    return


def _migrate_legacy_metric_modes(engine: Engine) -> None:
    """Collapse obsolete Exercise modes:

    - ``set_metric_mode='hybrid'`` → ``'reps'`` (tempo work is out of scope)
    - ``load_input_mode='carry'`` → ``'external_weight'``
      (mathematically identical; ``set_metric_mode='distance'`` carries the
      UI signal). ``external_load_multiplier`` is left untouched — carries
      already have mult=2 for paired DBs.
    """
    with engine.begin() as conn:
        insp = inspect(engine)
        if "exercises" not in insp.get_table_names():
            return
        conn.execute(text(
            "UPDATE exercises SET set_metric_mode = 'reps' "
            "WHERE set_metric_mode = 'hybrid'"
        ))
        conn.execute(text(
            "UPDATE exercises SET load_input_mode = 'external_weight' "
            "WHERE load_input_mode = 'carry'"
        ))


def _backfill_endurance_value(engine: Engine) -> None:
    """Populate ``workout_sets.endurance_value`` from legacy metric columns.

    The unit is determined by the owning exercise's ``set_metric_mode``:

    - ``reps``     → ``endurance_value = reps``
    - ``duration`` → ``endurance_value = COALESCE(duration_secs, reps)``
      (Weighted Plank historically logged seconds in the ``reps`` column.)
    - ``distance`` → ``endurance_value = COALESCE(distance_steps, reps)``

    Idempotent — only writes rows where ``endurance_value IS NULL``, so
    running this multiple times is safe and ongoing writes through the
    application-layer dual-write path are never overwritten. No-op once
    the legacy columns have been dropped (Phase 6+).
    """
    with engine.begin() as conn:
        insp = inspect(engine)
        if "workout_sets" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("workout_sets")}
        if "endurance_value" not in cols:
            return
        # Once the legacy columns are gone there's nothing to backfill from.
        if not ({"reps", "duration_secs", "distance_steps"} & cols):
            return
        has_reps = "reps" in cols
        has_dur = "duration_secs" in cols
        has_dist = "distance_steps" in cols

        if has_reps:
            # reps mode: prefer reps
            conn.execute(text(
                "UPDATE workout_sets SET endurance_value = reps "
                "WHERE endurance_value IS NULL AND reps IS NOT NULL "
                "  AND exercise_id IN ("
                "    SELECT id FROM exercises "
                "    WHERE COALESCE(set_metric_mode, 'reps') = 'reps'"
                "  )"
            ))
        if has_dur:
            # duration mode: prefer duration_secs, fall back to reps
            src = "COALESCE(duration_secs, reps)" if has_reps else "duration_secs"
            conn.execute(text(
                f"UPDATE workout_sets SET endurance_value = {src} "
                f"WHERE endurance_value IS NULL AND {src} IS NOT NULL "
                "  AND exercise_id IN ("
                "    SELECT id FROM exercises WHERE set_metric_mode = 'duration'"
                "  )"
            ))
        if has_dist:
            # distance mode: prefer distance_steps, fall back to reps
            src = "COALESCE(distance_steps, reps)" if has_reps else "distance_steps"
            conn.execute(text(
                f"UPDATE workout_sets SET endurance_value = {src} "
                f"WHERE endurance_value IS NULL AND {src} IS NOT NULL "
                "  AND exercise_id IN ("
                "    SELECT id FROM exercises WHERE set_metric_mode = 'distance'"
                "  )"
            ))


def _drop_legacy_metric_columns(engine: Engine) -> None:
    """Drop ``workout_sets.{reps,duration_secs,distance_steps}`` (Phase 6).

    Idempotent: only drops columns that still exist. Requires SQLite ≥3.35
    (supports ``ALTER TABLE ... DROP COLUMN``); the runtime ``inspect``
    pre-check makes this a no-op on already-migrated DBs.

    Safety: ``apply_db_updates`` runs ``_backfill_endurance_value`` first,
    so by the time this function executes every row that previously had a
    value in the legacy columns has been reflected onto ``endurance_value``.
    """
    insp = inspect(engine)
    if "workout_sets" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("workout_sets")}
    legacy = ["reps", "duration_secs", "distance_steps"]
    to_drop = [c for c in legacy if c in cols]
    if not to_drop:
        return
    logger.info("Dropping legacy workout_sets columns: %s", ", ".join(to_drop))
    with engine.begin() as conn:
        for col in to_drop:
            conn.execute(text(f"ALTER TABLE workout_sets DROP COLUMN {col}"))


