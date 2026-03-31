from sqlalchemy import inspect
from sqlmodel import create_engine

import app.database as database


def test_create_db_and_tables_skips_manual_update_helpers(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_ensure_sqlite_dir", lambda: None)

    def fail(name: str):
        def _inner():
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
    monkeypatch.setattr(
        database,
        "_backfill_tracked_tissue_foundation",
        fail("_backfill_tracked_tissue_foundation"),
    )

    database.create_db_and_tables()

    table_names = set(inspect(engine).get_table_names())
    assert "exercises" in table_names
    assert "workout_sets" in table_names


def test_apply_db_updates_runs_manual_update_helpers(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_ensure_sqlite_dir", lambda: None)

    calls: list[str] = []

    def record(name: str):
        def _inner():
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
    monkeypatch.setattr(
        database,
        "_backfill_tracked_tissue_foundation",
        record("_backfill_tracked_tissue_foundation"),
    )

    database.apply_db_updates()

    assert calls == [
        "_backup_database",
        "_migrate_add_columns",
        "_drop_obsolete_tables",
        "_seed_data",
        "_backfill_rep_completion",
        "_backfill_special_workout_sets",
        "_backfill_historical_bodyweight_anchor",
        "_backfill_progression_rep_completion",
        "_backfill_tracked_tissue_foundation",
    ]
