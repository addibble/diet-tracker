#!/usr/bin/env python3
"""Audit or import the Winter Bulk program CSV."""

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
ROUND_HEADERS = [
    "12-15 Reps",
    "11-14 Reps",
    "10-13 Reps",
    "9-12 Reps",
    "8-11 Reps",
    "7-10 Reps",
    "6-9 Reps",
]
DEFAULT_REPS = [13, 12, 11, 10, 9, 8, 7]
DOUBLE_DATES = {dt.date(2025, 12, 14), dt.date(2025, 12, 15)}
BREAK_END = dt.date(2026, 1, 1)

ALIAS_MAP: dict[str, str] = {
    "barbell curls": "Barbell Curl",
    "cable woodchoppers": "Cable Woodchoppers (Abs)",
    "cable curl": "Straight-Bar Cable Curl",
    "cable bar curl burnout 50 60": "Straight-Bar Cable Curl",
    "db lateral raise": "Lateral Raise",
    "hammer curls": "Hammer Curl",
    "heavy lat pulldown": "Lat Pulldown",
    "incline db press": "Incline Dumbbell Press",
    "lateral raise db": "Lateral Raise",
    "machine shoulder press": "Overhead Press Machine",
    "pec deck machine": "Pec Deck",
    "rear delt machine": "Pec Deck (Rear Delt)",
    "seated cable row": "Seated Cable Row",
    "seated hamstring curl": "Seated Leg Curl",
    "single arm cable lateral raise": "Single-Arm Cable Lateral Raise (Left Only)",
    "tricep rope pushdowns": "Triceps Rope Pushdown",
}

CREATE_NAME_MAP: dict[str, str] = {
    "bench press": "Bench Press",
    "hanging leg raises": "Hanging Leg Raises",
    "machine chest press": "Machine Chest Press",
    "rear delt machine": "Pec Deck (Rear Delt)",
}

PAREN_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<note>[^)]*)\)\s*$")


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    base_name: str
    note_suffix: str | None
    group_index: int
    values: tuple[str, ...]


@dataclass(frozen=True)
class SetRow:
    exercise_id: int
    set_order: int
    reps: int | None
    weight: float | None
    duration_secs: int | None
    notes: str | None


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).lower().strip()
    normalized = normalized.replace("&", "and")
    normalized = normalized.replace("‑", "-").replace("–", "-")
    normalized = re.sub(r"[()]", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def split_name(name: str) -> tuple[str, str | None]:
    match = PAREN_RE.fullmatch(name.strip())
    if match:
        return match["name"].strip(), match["note"].strip()

    special_notes = {
        "heavy lat pulldown": ("Lat Pulldown", "Heavy"),
        "leg press deep": ("Leg Press", "Deep"),
    }
    normalized = normalize_name(name)
    if normalized in special_notes:
        return special_notes[normalized]
    return name.strip(), None


def parse_csv(path: Path) -> list[ExerciseRow]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows: list[ExerciseRow] = []
        for raw in reader:
            exercise_name = (raw.get("Exercise") or "").strip()
            if not exercise_name:
                continue
            base_name, note_suffix = split_name(exercise_name)
            rows.append(
                ExerciseRow(
                    raw_name=exercise_name,
                    base_name=base_name,
                    note_suffix=note_suffix,
                    group_index=int((raw.get("Group") or "0").strip()),
                    values=tuple((raw.get(header) or "").strip() for header in ROUND_HEADERS),
                )
            )
    return rows


def resolve_exercises(
    conn: sqlite3.Connection,
    rows: list[ExerciseRow],
    *,
    create_missing: bool,
) -> tuple[dict[str, int], dict[str, str], list[str]]:
    db_rows = conn.execute("SELECT id, name FROM exercises ORDER BY id").fetchall()
    exact = {name: exercise_id for exercise_id, name in db_rows}
    by_norm = {normalize_name(name): (exercise_id, name) for exercise_id, name in db_rows}

    resolved_ids: dict[str, int] = {}
    resolved_names: dict[str, str] = {}
    unmatched: list[str] = []

    for row in rows:
        key = row.base_name
        if key in resolved_ids:
            continue

        if key in exact:
            resolved_ids[key] = exact[key]
            resolved_names[key] = key
            continue

        normalized = normalize_name(key)
        alias_target = ALIAS_MAP.get(normalized)
        if alias_target and alias_target in exact:
            resolved_ids[key] = exact[alias_target]
            resolved_names[key] = alias_target
            continue

        if normalized in by_norm:
            resolved_ids[key] = by_norm[normalized][0]
            resolved_names[key] = by_norm[normalized][1]
            continue

        if create_missing:
            created_name = CREATE_NAME_MAP.get(normalized, key)
            existing_id = exact.get(created_name)
            if existing_id is not None:
                resolved_ids[key] = existing_id
                resolved_names[key] = created_name
                continue
            created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
            cursor = conn.execute(
                """
                INSERT INTO exercises (name, equipment, notes, created_at)
                VALUES (?, NULL, NULL, ?)
                """,
                (created_name, created_at),
            )
            resolved_ids[key] = cursor.lastrowid
            resolved_names[key] = created_name
            exact[created_name] = cursor.lastrowid
            by_norm[normalize_name(created_name)] = (cursor.lastrowid, created_name)
            continue

        unmatched.append(key)

    return resolved_ids, resolved_names, unmatched


def merge_notes(*parts: str | None) -> str | None:
    values = [part.strip() for part in parts if part and part.strip()]
    if not values:
        return None
    return "; ".join(values)


def parse_cell(
    raw_value: str,
    *,
    default_reps: int,
    row_note: str | None,
) -> tuple[int, int | None, float | None, int | None, str | None]:
    value = unicodedata.normalize("NFKC", raw_value).strip()
    lower = value.lower()

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return 3, default_reps, float(value), None, row_note

    if re.fullmatch(r"\d+\s+reps?", lower):
        reps = int(re.search(r"\d+", lower).group(0))
        return 1, reps, 0.0, None, row_note

    weighted_reps = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+)(?:x(\d+))?", lower)
    if weighted_reps:
        weight = float(weighted_reps.group(1))
        reps = int(weighted_reps.group(2))
        sets = int(weighted_reps.group(3) or 3)
        return sets, reps, weight, None, row_note

    if lower.startswith("bw"):
        return 3, default_reps, 0.0, None, merge_notes(row_note, value)

    hold_match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+hold\s+(.+)", lower)
    if hold_match:
        return 3, default_reps, float(hold_match.group(1)), None, merge_notes(row_note, hold_match.group(2))

    plank_double = re.fullmatch(r"(\d+(?:\.\d+)?)\s*lbs\s*(\d+),(\d+)s", lower)
    if plank_double:
        weight = float(plank_double.group(1))
        first = int(plank_double.group(2))
        second = int(plank_double.group(3))
        return 2, 1, weight, -1, merge_notes(row_note, f"{first}s,{second}s")

    plank_sets = re.fullmatch(r"(\d+(?:\.\d+)?)\s*lbs\s*(\d+)s\s*x(\d+)", lower)
    if plank_sets:
        weight = float(plank_sets.group(1))
        duration = int(plank_sets.group(2))
        sets = int(plank_sets.group(3))
        return sets, 1, weight, duration, row_note

    if re.fullmatch(r"[\d\s\w\+\-.,]+", value):
        return 3, default_reps, 0.0, None, merge_notes(row_note, value)

    raise ValueError(f"Unsupported cell value: {raw_value!r}")


def expand_rows(
    rows: list[ExerciseRow],
    resolved_ids: dict[str, int],
) -> dict[tuple[int, int], list[SetRow]]:
    sessions: dict[tuple[int, int], list[SetRow]] = {}
    for round_index, default_reps in enumerate(DEFAULT_REPS, start=1):
        for group_index in sorted({row.group_index for row in rows}):
            bucket = sessions.setdefault((group_index, round_index), [])
            for row in rows:
                if row.group_index != group_index:
                    continue
                raw_value = row.values[round_index - 1]
                if not raw_value:
                    continue
                set_count, reps, weight, duration, note = parse_cell(
                    raw_value,
                    default_reps=default_reps,
                    row_note=row.note_suffix,
                )
                for idx in range(set_count):
                    set_duration = duration
                    set_note = note
                    if duration == -1 and note:
                        parts = [part.strip() for part in note.split(",")]
                        set_duration = int(parts[idx].rstrip("s"))
                        set_note = merge_notes(row.note_suffix, parts[idx])
                    bucket.append(
                        SetRow(
                            exercise_id=resolved_ids[row.base_name],
                            set_order=len(bucket) + 1,
                            reps=reps,
                            weight=weight,
                            duration_secs=set_duration,
                            notes=set_note,
                        )
                    )
    return {key: value for key, value in sessions.items() if value}


def generate_dates() -> list[dt.date]:
    dates: list[dt.date] = []
    current = dt.date(2025, 12, 1)
    before_break = []
    while current <= dt.date(2025, 12, 15):
        before_break.append(current)
        current += dt.timedelta(days=1)
    for day in before_break:
        dates.append(day)
        if day in DOUBLE_DATES:
            dates.append(day)

    current = BREAK_END + dt.timedelta(days=1)
    while current <= dt.date(2026, 2, 2):
        dates.append(current)
        current += dt.timedelta(days=1)
    return dates


def delete_range(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> tuple[int, int]:
    session_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM workout_sessions WHERE date >= ? AND date <= ?",
            (start.isoformat(), end.isoformat()),
        )
    ]
    deleted_sets = 0
    for session_id in session_ids:
        deleted_sets += conn.execute(
            "DELETE FROM workout_sets WHERE session_id = ?",
            (session_id,),
        ).rowcount
    deleted_sessions = conn.execute(
        "DELETE FROM workout_sessions WHERE date >= ? AND date <= ?",
        (start.isoformat(), end.isoformat()),
    ).rowcount
    return deleted_sets, deleted_sessions


def insert_sessions(
    conn: sqlite3.Connection,
    dates: list[dt.date],
    sessions: dict[tuple[int, int], list[SetRow]],
) -> tuple[int, int]:
    inserted_sessions = 0
    inserted_sets = 0
    ordered_keys = sorted(sessions, key=lambda item: (item[1], item[0]))
    for date_value, (group_index, round_index) in zip(dates, ordered_keys):
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        note = f"Winter Bulk Group {group_index} Round {round_index}"
        cursor = conn.execute(
            """
            INSERT INTO workout_sessions (date, started_at, finished_at, notes, created_at)
            VALUES (?, NULL, NULL, ?, ?)
            """,
            (date_value.isoformat(), note, created_at),
        )
        session_id = cursor.lastrowid
        inserted_sessions += 1
        for row in sessions[(group_index, round_index)]:
            conn.execute(
                """
                INSERT INTO workout_sets (
                    session_id, exercise_id, set_order, reps, weight, duration_secs,
                    distance_steps, rpe, rep_completion, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    session_id,
                    row.exercise_id,
                    row.set_order,
                    row.reps,
                    row.weight,
                    row.duration_secs,
                    row.notes,
                    created_at,
                ),
            )
            inserted_sets += 1
    return inserted_sessions, inserted_sets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--create-missing-exercises", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = parse_csv(args.csv_path.resolve())
    with sqlite3.connect(args.db.resolve()) as conn:
        preview_mode = not args.apply and args.create_missing_exercises
        if preview_mode:
            conn.execute("SAVEPOINT winter_bulk_preview")
        resolved_ids, resolved_names, unmatched = resolve_exercises(
            conn,
            rows,
            create_missing=args.create_missing_exercises,
        )
        sessions = expand_rows(rows, resolved_ids) if not unmatched else {}
        dates = generate_dates()

        print(f"Rows: {len(rows)}")
        print(f"Resolved exercises: {len(resolved_ids)}")
        print(f"Unmatched exercises: {len(unmatched)}")
        print(f"Sessions with data: {len(sessions)}")
        print(f"Scheduled dates: {len(dates)}")
        print()

        print("Resolved mappings:")
        for raw_name in sorted(resolved_names):
            target = resolved_names[raw_name]
            if raw_name != target:
                print(f"  {raw_name} -> {target}")
        print()

        if unmatched:
            print("Unmatched exercises:")
            for name in unmatched:
                print(f"  {name}")
            print()

        if len(sessions) != len(dates):
            if preview_mode:
                conn.execute("ROLLBACK TO winter_bulk_preview")
                conn.execute("RELEASE winter_bulk_preview")
            print(
                f"Schedule mismatch: {len(sessions)} sessions but {len(dates)} scheduled dates."
            )
            return 1

        if not args.apply:
            if preview_mode:
                conn.execute("ROLLBACK TO winter_bulk_preview")
                conn.execute("RELEASE winter_bulk_preview")
            print("Audit only.")
            return 0

        if unmatched:
            print("Refusing to import while unmatched exercises remain.")
            return 1

        deleted_sets, deleted_sessions = delete_range(
            conn,
            dt.date(2025, 12, 1),
            dt.date(2026, 2, 2),
        )
        inserted_sessions, inserted_sets = insert_sessions(conn, dates, sessions)
        conn.commit()
        print("Break window: 2025-12-16 through 2026-01-01.")
        print("Double-session dates: 2025-12-14 and 2025-12-15.")
        print(f"Deleted {deleted_sessions} sessions and {deleted_sets} sets in the winter range.")
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
