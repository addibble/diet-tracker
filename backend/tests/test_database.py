from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

import app.database as database
import app.models  # noqa: F401  -- ensures all SQLModel tables are registered


def test_create_db_and_tables_skips_manual_update_helpers(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "_ensure_sqlite_dir", lambda e: None)

    def fail(name: str):
        def _inner(*args, **kwargs):
            raise AssertionError(f"{name} should not run during startup bootstrap")

        return _inner

    monkeypatch.setattr(database, "_backup_database", fail("_backup_database"))
    monkeypatch.setattr(database, "_migrate_add_columns", fail("_migrate_add_columns"))
    monkeypatch.setattr(database, "_drop_obsolete_tables", fail("_drop_obsolete_tables"))
    monkeypatch.setattr(database, "_seed_data", fail("_seed_data"))
    monkeypatch.setattr(database, "_backfill_rep_completion", fail("_backfill_rep_completion"))
    monkeypatch.setattr(
        database,
        "_backfill_special_workout_sets",
        fail("_backfill_special_workout_sets"),
    )
    monkeypatch.setattr(
        database,
        "_backfill_historical_bodyweight_anchor",
        fail("_backfill_historical_bodyweight_anchor"),
    )
    monkeypatch.setattr(
        database,
        "_backfill_progression_rep_completion",
        fail("_backfill_progression_rep_completion"),
    )

    database.create_db_and_tables(engine)

    table_names = set(inspect(engine).get_table_names())
    assert "exercises" in table_names
    assert "workout_sets" in table_names


def test_apply_db_updates_runs_manual_update_helpers(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "_ensure_sqlite_dir", lambda e: None)

    calls: list[str] = []

    def record(name: str):
        def _inner(*args, **kwargs):
            calls.append(name)

        return _inner

    monkeypatch.setattr(database, "_backup_database", record("_backup_database"))
    monkeypatch.setattr(database, "_migrate_add_columns", record("_migrate_add_columns"))
    monkeypatch.setattr(database, "_drop_obsolete_tables", record("_drop_obsolete_tables"))
    monkeypatch.setattr(database, "_seed_data", record("_seed_data"))
    monkeypatch.setattr(database, "_backfill_rep_completion", record("_backfill_rep_completion"))
    monkeypatch.setattr(
        database,
        "_backfill_special_workout_sets",
        record("_backfill_special_workout_sets"),
    )
    monkeypatch.setattr(
        database,
        "_backfill_historical_bodyweight_anchor",
        record("_backfill_historical_bodyweight_anchor"),
    )
    monkeypatch.setattr(
        database,
        "_backfill_progression_rep_completion",
        record("_backfill_progression_rep_completion"),
    )

    database.apply_db_updates(engine)

    assert calls == [
        "_backup_database",
        "_migrate_add_columns",
        "_drop_obsolete_tables",
        "_seed_data",
        "_backfill_rep_completion",
        "_backfill_special_workout_sets",
        "_backfill_historical_bodyweight_anchor",
        "_backfill_progression_rep_completion",
    ]


def test_ensure_runtime_db_ready_runs_updates_when_schema_is_stale(monkeypatch):
    engine = create_engine("sqlite://")
    calls: list[str] = []

    monkeypatch.setattr(
        database, "_ensure_sqlite_dir", lambda e: calls.append("_ensure_sqlite_dir")
    )
    monkeypatch.setattr(
        database.SQLModel.metadata,
        "create_all",
        lambda engine, **kw: calls.append("create_all"),
    )
    monkeypatch.setattr(database, "_runtime_db_needs_manual_updates", lambda e: True)
    monkeypatch.setattr(database, "apply_db_updates", lambda e: calls.append("apply_db_updates"))

    database.ensure_runtime_db_ready(engine)

    assert calls == [
        "_ensure_sqlite_dir",
        "create_all",
        "apply_db_updates",
    ]


def test_ensure_runtime_db_ready_skips_updates_when_schema_is_current(monkeypatch):
    engine = create_engine("sqlite://")
    calls: list[str] = []

    monkeypatch.setattr(
        database, "_ensure_sqlite_dir", lambda e: calls.append("_ensure_sqlite_dir")
    )
    monkeypatch.setattr(
        database.SQLModel.metadata,
        "create_all",
        lambda engine, **kw: calls.append("create_all"),
    )
    monkeypatch.setattr(database, "_runtime_db_needs_manual_updates", lambda e: False)
    monkeypatch.setattr(database, "apply_db_updates", lambda e: calls.append("apply_db_updates"))

    database.ensure_runtime_db_ready(engine)

    assert calls == [
        "_ensure_sqlite_dir",
        "create_all",
    ]


def test_runtime_required_columns_match_orm_columns():
    """Regression: when a new column is added to a SQLModel table the
    matching entry in RUNTIME_REQUIRED_COLUMNS must be updated, otherwise
    `_runtime_db_needs_manual_updates` will not detect the missing column
    on existing prod databases and the migration will silently skip,
    leaving the live schema out of sync with the ORM (causes 500s on any
    query that touches the new column).

    This test asserts that every column listed in RUNTIME_REQUIRED_COLUMNS
    actually exists on the corresponding ORM model. We don't enforce the
    inverse direction (every model column must be listed) because some
    columns are nullable and harmless to omit; but since this set is
    exactly the trigger for migration, missing entries here are bugs.
    """
    metadata = SQLModel.metadata
    for table_name, required in database.RUNTIME_REQUIRED_COLUMNS.items():
        table = metadata.tables.get(table_name)
        assert table is not None, f"Unknown table {table_name} in RUNTIME_REQUIRED_COLUMNS"
        orm_cols = {c.name for c in table.columns}
        missing = required - orm_cols
        assert not missing, (
            f"RUNTIME_REQUIRED_COLUMNS[{table_name!r}] lists columns "
            f"{sorted(missing)} that don't exist on the ORM model."
        )


def test_runtime_check_includes_recent_v4_columns():
    """Specific guard: the v4 strength model added curve_delta and
    readiness_beta. Both must trip the runtime migration check on an
    existing DB that doesn't have them yet."""
    assert "curve_delta" in database.RUNTIME_REQUIRED_COLUMNS["exercises"]
    assert "readiness_beta" in database.RUNTIME_REQUIRED_COLUMNS["workout_sessions"]

