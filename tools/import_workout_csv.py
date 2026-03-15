#!/usr/bin/env python3
"""Audit or import historical workout CSV logs.

This script reads a date-column workout CSV, resolves exercise names against the
database (with a small alias table for known spreadsheet shorthand), reports any
unmatched exercises, and can optionally delete and rebuild historical sessions.

Default behavior is audit-only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parents[1] / "diet_tracker.db"

ALIAS_MAP: dict[str, str] = {
    "ab crunches": "Laying Down Crunches",
    "abdominal curls": "Ab Crunch Machine",
    "incl db press heavy": "Incline Dumbbell Press",
    "incl db press": "Incline Dumbbell Press",
    "chest supported row heavy": "Chest-Supported Row",
    "cable fly mid": "Cable Fly",
    "push ups slow": "Push-ups",
    "low high cable flys": "Low-High Cable Fly",
    "rope pushdown": "Triceps Rope Pushdown",
    "incline db curl": "Incline Dumbbell Curl",
    "hammer curl heavy": "Hammer Curl",
    "leg press heavy": "Leg Press",
    "cable pull through": "Cable Pull Through",
    "glute drive": "Glute Drive Machine",
    "hip abduction": "Hip Abduction Machine",
    "hip adduction": "Hip Adduction Machine",
    "landmine press heavy": "Landmine Press",
    "overhead press": "Overhead Press Machine",
    "face pulls cable band": "Face Pulls",
    "lateral raise light controlled": "Lateral Raise",
    "rear delt row": "Pec Deck (Rear Delt)",
    "back extensions": "Back Extension Machine",
    "single leg ham curl": "Single Leg Hamstring Curl",
    "dumbbell press heavy": "Flat Dumbbell Press",
    "dumbbell press": "Flat Dumbbell Press",
    "calf raises": "Seated Calf Raise",
    "hanging knee raises": "Hanging Leg Raises",
}

CREATE_NAME_MAP: dict[str, str] = {}


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    sets_reps: str
    max_value: str
    values_by_date: dict[dt.date, str]


@dataclass(frozen=True)
class SetRow:
    exercise_id: int
    set_order: int
    reps: int | None
    weight: float | None
    duration_secs: int | None
    distance_steps: int | None
    notes: str | None


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).lower().strip()
    normalized = normalized.replace("&", "and")
    normalized = normalized.replace("‑", "-")
    normalized = re.sub(r"[()]", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def parse_date_header(header: str) -> dt.date | None:
    text = header.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(path: Path) -> tuple[list[dt.date], list[ExerciseRow]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")

        date_columns: list[tuple[str, dt.date]] = []
        for name in reader.fieldnames:
            parsed = parse_date_header(name)
            if parsed is not None:
                date_columns.append((name, parsed))

        rows: list[ExerciseRow] = []
        for raw in reader:
            exercise_name = (raw.get("Exercise") or "").strip()
            if not exercise_name:
                continue
            values_by_date = {
                parsed_date: (raw.get(header) or "").strip()
                for header, parsed_date in date_columns
                if (raw.get(header) or "").strip()
            }
            rows.append(
                ExerciseRow(
                    raw_name=exercise_name,
                    sets_reps=(raw.get("SetsxReps") or "").strip(),
                    max_value=(raw.get("max") or "").strip(),
                    values_by_date=values_by_date,
                )
            )

    return [date for _, date in date_columns], rows


def resolve_exercise_names(
    conn: sqlite3.Connection,
    rows: list[ExerciseRow],
) -> tuple[dict[str, int], dict[str, str], list[str]]:
    db_rows = conn.execute("SELECT id, name FROM exercises ORDER BY id").fetchall()
    db_by_exact = {name: exercise_id for exercise_id, name in db_rows}
    db_by_norm = {normalize_name(name): (exercise_id, name) for exercise_id, name in db_rows}

    resolved_ids: dict[str, int] = {}
    resolved_names: dict[str, str] = {}
    unmatched: list[str] = []

    for row in rows:
        if row.raw_name in resolved_ids:
            continue

        if row.raw_name in db_by_exact:
            resolved_ids[row.raw_name] = db_by_exact[row.raw_name]
            resolved_names[row.raw_name] = row.raw_name
            continue

        normalized = normalize_name(row.raw_name)
        alias_target = ALIAS_MAP.get(normalized)
        if alias_target and alias_target in db_by_exact:
            resolved_ids[row.raw_name] = db_by_exact[alias_target]
            resolved_names[row.raw_name] = alias_target
            continue

        if normalized in db_by_norm:
            resolved_ids[row.raw_name] = db_by_norm[normalized][0]
            resolved_names[row.raw_name] = db_by_norm[normalized][1]
            continue

        unmatched.append(row.raw_name)

    return resolved_ids, resolved_names, unmatched


def create_exercise(conn: sqlite3.Connection, name: str) -> int:
    existing = conn.execute(
        "SELECT id FROM exercises WHERE name = ?",
        (name,),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
    cursor = conn.execute(
        """
        INSERT INTO exercises (name, equipment, notes, created_at)
        VALUES (?, NULL, NULL, ?)
        """,
        (name, created_at),
    )
    return cursor.lastrowid


def created_name_for(raw_name: str) -> str:
    return CREATE_NAME_MAP.get(normalize_name(raw_name), raw_name)


def parse_default_sets_reps(text: str) -> tuple[int, int | None, int | None, bool]:
    compact = unicodedata.normalize("NFKC", text).lower().strip()
    compact = compact.replace("–", "-").replace("×", "x")
    compact = compact.replace(" / leg", "").replace("/ leg", "")
    compact = compact.replace(" / side", "").replace("/ side", "")
    is_steps = "step" in compact
    match = re.search(r"(?P<sets>\d+)\s*x\s*(?P<low>\d+)(?:\s*-\s*(?P<high>\d+))?", compact)
    if not match:
        match = re.search(r"(?P<low>\d+)(?:\s*-\s*(?P<high>\d+))?", compact)
        if match:
            return 1, int(match["low"]), int(match["high"] or match["low"]), is_steps
        return 1, None, None, is_steps

    return (
        int(match["sets"]),
        int(match["low"]),
        int(match["high"] or match["low"]),
        is_steps,
    )


def try_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def parse_cell(
    raw_value: str,
    *,
    default_sets: int,
    default_low_reps: int | None,
    default_high_reps: int | None,
    is_steps: bool,
) -> tuple[int, int | None, float | None, int | None, int | None, str | None]:
    value = unicodedata.normalize("NFKC", raw_value).strip()
    lower = value.lower()

    if try_float(value) is not None:
        reps = default_low_reps
        weight = try_float(value)
        return default_sets, reps, weight, None, None, None

    explicit_sets_reps = re.fullmatch(r"(?P<sets>\d+)\s*x\s*(?P<reps>\d+)", lower)
    if explicit_sets_reps:
        sets = int(explicit_sets_reps["sets"])
        reps = int(explicit_sets_reps["reps"])
        return sets, reps, 0.0, None, None, value

    sets_only = re.fullmatch(r"(?P<sets>\d+)\s*sets?", lower)
    if sets_only:
        sets = int(sets_only["sets"])
        reps = default_high_reps or default_low_reps
        return sets, reps, 0.0, None, None, value

    bodyweight = "body weight" in lower or lower == "bw"
    if bodyweight:
        rep_match = re.search(r"(?P<reps>\d+)\s*reps?", lower)
        reps = int(rep_match["reps"]) if rep_match else (default_high_reps or default_low_reps)
        return default_sets, reps, 0.0, None, None, value

    if is_steps:
        steps_match = re.search(r"(?P<sets>\d+)\s*x\s*(?P<steps>\d+)", lower)
        if steps_match:
            return int(steps_match["sets"]), None, None, None, int(steps_match["steps"]), value
        return default_sets, None, None, None, default_low_reps, value

    return default_sets, default_low_reps, 0.0, None, None, value


def build_session_rows(
    rows: list[ExerciseRow],
    resolved_ids: dict[str, int],
) -> dict[dt.date, list[SetRow]]:
    sessions: dict[dt.date, list[SetRow]] = {}

    for row in rows:
        if row.raw_name not in resolved_ids:
            continue

        default_sets, default_low_reps, default_high_reps, is_steps = parse_default_sets_reps(
            row.sets_reps
        )
        exercise_id = resolved_ids[row.raw_name]

        for date_value, raw_cell in sorted(row.values_by_date.items()):
            set_count, reps, weight, duration_secs, distance_steps, note = parse_cell(
                raw_cell,
                default_sets=default_sets,
                default_low_reps=default_low_reps,
                default_high_reps=default_high_reps,
                is_steps=is_steps,
            )
            bucket = sessions.setdefault(date_value, [])
            for _ in range(set_count):
                bucket.append(
                    SetRow(
                        exercise_id=exercise_id,
                        set_order=len(bucket) + 1,
                        reps=reps,
                        weight=weight,
                        duration_secs=duration_secs,
                        distance_steps=distance_steps,
                        notes=note,
                    )
                )

    return sessions


def historical_note(csv_path: Path, date_value: dt.date) -> str:
    return f"Historical CSV import: {csv_path.stem} ({date_value.isoformat()})"


def delete_before(conn: sqlite3.Connection, cutoff: dt.date) -> tuple[int, int]:
    session_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM workout_sessions WHERE date < ? ORDER BY id",
            (cutoff.isoformat(),),
        ).fetchall()
    ]
    if not session_ids:
        return 0, 0

    deleted_sets = 0
    for session_id in session_ids:
        deleted_sets += conn.execute(
            "DELETE FROM workout_sets WHERE session_id = ?",
            (session_id,),
        ).rowcount
    deleted_sessions = conn.execute(
        "DELETE FROM workout_sessions WHERE date < ?",
        (cutoff.isoformat(),),
    ).rowcount
    return deleted_sets, deleted_sessions


def insert_sessions(
    conn: sqlite3.Connection,
    csv_path: Path,
    sessions: dict[dt.date, list[SetRow]],
) -> tuple[int, int]:
    inserted_sessions = 0
    inserted_sets = 0

    for date_value in sorted(sessions):
        note = historical_note(csv_path, date_value)
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        cursor = conn.execute(
            """
            INSERT INTO workout_sessions (date, started_at, finished_at, notes, created_at)
            VALUES (?, NULL, NULL, ?, ?)
            """,
            (date_value.isoformat(), note, created_at),
        )
        session_id = cursor.lastrowid
        inserted_sessions += 1

        for row in sessions[date_value]:
            conn.execute(
                """
                INSERT INTO workout_sets (
                    session_id, exercise_id, set_order, reps, weight, duration_secs,
                    distance_steps, rpe, rep_completion, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    session_id,
                    row.exercise_id,
                    row.set_order,
                    row.reps,
                    row.weight,
                    row.duration_secs,
                    row.distance_steps,
                    row.notes,
                    created_at,
                ),
            )
            inserted_sets += 1

    return inserted_sessions, inserted_sets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Workout CSV to audit/import.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path.")
    parser.add_argument(
        "--delete-before",
        type=dt.date.fromisoformat,
        default=None,
        help="Delete workout sessions and sets before this date before import.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletes/import. Without this flag the script is audit-only.",
    )
    parser.add_argument(
        "--create-missing-exercises",
        action="store_true",
        help="Create unmatched exercise rows using the CSV names during --apply.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = args.csv_path.resolve()
    db_path = args.db.resolve()

    if not csv_path.exists():
        raise SystemExit(f"CSV does not exist: {csv_path}")
    if not db_path.exists():
        raise SystemExit(f"Database does not exist: {db_path}")

    _, rows = parse_csv(csv_path)
    with sqlite3.connect(db_path) as conn:
        resolved_ids, resolved_names, unmatched = resolve_exercise_names(conn, rows)
        sessions = build_session_rows(rows, resolved_ids)

        print(f"CSV rows: {len(rows)}")
        print(f"Resolved exercises: {len(resolved_ids)}")
        print(f"Unmatched exercises: {len(unmatched)}")
        print(f"Import sessions: {len(sessions)}")
        print(f"Import sets: {sum(len(items) for items in sessions.values())}")
        print()

        if resolved_names:
            print("Resolved mappings:")
            for raw_name in sorted(resolved_names):
                target = resolved_names[raw_name]
                if raw_name == target:
                    continue
                print(f"  {raw_name} -> {target}")
            print()

        if unmatched:
            print("Unmatched exercises:")
            for name in unmatched:
                print(f"  {name}")
            print()

        if not args.apply:
            print("Audit only. Re-run with --apply after reviewing unmatched exercises.")
            return 0

        if unmatched:
            if not args.create_missing_exercises:
                print("Refusing to import while unmatched exercises remain.")
                return 1
            for raw_name in unmatched:
                created_name = created_name_for(raw_name)
                exercise_id = create_exercise(conn, created_name)
                resolved_ids[raw_name] = exercise_id
                resolved_names[raw_name] = created_name
            sessions = build_session_rows(rows, resolved_ids)

        deleted_sets = 0
        deleted_sessions = 0
        if args.delete_before is not None:
            deleted_sets, deleted_sessions = delete_before(conn, args.delete_before)

        inserted_sessions, inserted_sets = insert_sessions(conn, csv_path, sessions)
        conn.commit()

        print(
            f"Deleted {deleted_sessions} sessions and {deleted_sets} sets before "
            f"{args.delete_before.isoformat() if args.delete_before else 'N/A'}."
        )
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
