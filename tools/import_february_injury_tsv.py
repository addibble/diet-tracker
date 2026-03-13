#!/usr/bin/env python3
"""Audit or import the February 2026 shoulder-injury block."""

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

PHASE_DATES = {
    (1, "6-9 Reps"): dt.date(2026, 2, 5),
    (2, "6-9 Reps"): dt.date(2026, 2, 6),
    (3, "6-9 Reps"): dt.date(2026, 2, 7),
}
COMBINED_DATE = dt.date(2026, 2, 14)
COMBINED_COLUMN = "5-8 Reps"

ALIAS_MAP: dict[str, str] = {
    "cable woodchoppers": "Cable Woodchoppers (Abs)",
    "cable lat raise": "Single-Arm Cable Lateral Raise (Left Only)",
    "incline barbell press": "Incline Barbell Press",
    "lat pulldown": "Lat Pulldown",
    "rear delt machine": "Pec Deck (Rear Delt)",
}

CREATE_NAME_MAP: dict[str, str] = {
    "hanging leg raises": "Hanging Leg Raises",
    "incline barbell press": "Incline Barbell Press",
    "rear delt machine": "Pec Deck (Rear Delt)",
}

PAREN_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<note>[^)]*)\)\s*$")


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    base_name: str
    note_suffix: str | None
    min_reps: int
    group_index: int
    six_to_nine: str
    five_to_eight: str


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
    return name.strip(), None


def merge_notes(*parts: str | None) -> str | None:
    values = [part.strip() for part in parts if part and part.strip()]
    if not values:
        return None
    return "; ".join(values)


def parse_tsv(path: Path) -> list[ExerciseRow]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows: list[ExerciseRow] = []
        for raw in reader:
            raw_name = (raw.get("Exercise") or "").strip()
            if not raw_name:
                continue
            base_name, note_suffix = split_name(raw_name)
            rows.append(
                ExerciseRow(
                    raw_name=raw_name,
                    base_name=base_name,
                    note_suffix=note_suffix,
                    min_reps=int((raw.get("Min Reps") or "0").strip()),
                    group_index=int((raw.get("Group") or "0").strip()),
                    six_to_nine=(raw.get("6-9 Reps") or "").strip(),
                    five_to_eight=(raw.get("5-8 Reps") or "").strip(),
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


def parse_weight_with_override(
    raw_value: str,
    *,
    default_reps: int,
    note_suffix: str | None,
) -> list[tuple[int | None, float | None, int | None, str | None]]:
    value = unicodedata.normalize("NFKC", raw_value).strip()
    lower = value.lower()

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return [(default_reps, float(value), None, note_suffix)] * 3

    weight_reps = re.fullmatch(r"(\d+(?:\.\d+)?)@(\d+(?:,\d+)*)", lower)
    if weight_reps:
        weight = float(weight_reps.group(1))
        reps_list = [int(part) for part in weight_reps.group(2).split(",")]
        return [(reps, weight, None, note_suffix) for reps in reps_list]

    plank = re.fullmatch(r"(\d+(?:\.\d+)?)@(\d+)s,(\d+)s", lower)
    if plank:
        weight = float(plank.group(1))
        return [
            (1, weight, int(plank.group(2)), merge_notes(note_suffix, f"{plank.group(2)}s")),
            (1, weight, int(plank.group(3)), merge_notes(note_suffix, f"{plank.group(3)}s")),
        ]

    if lower.startswith("hold "):
        return [(default_reps, 0.0, None, merge_notes(note_suffix, value))] * 3

    raise ValueError(f"Unsupported value: {raw_value!r}")


def build_sessions(
    rows: list[ExerciseRow],
    resolved_ids: dict[str, int],
) -> dict[dt.date, list[SetRow]]:
    sessions: dict[dt.date, list[SetRow]] = {date_value: [] for date_value in PHASE_DATES.values()}
    sessions[COMBINED_DATE] = []

    for row in rows:
        if row.six_to_nine:
            date_value = PHASE_DATES[(row.group_index, "6-9 Reps")]
            bucket = sessions[date_value]
            for reps, weight, duration, notes in parse_weight_with_override(
                row.six_to_nine,
                default_reps=row.min_reps,
                note_suffix=row.note_suffix,
            ):
                bucket.append(
                    SetRow(
                        exercise_id=resolved_ids[row.base_name],
                        set_order=len(bucket) + 1,
                        reps=reps,
                        weight=weight,
                        duration_secs=duration,
                        notes=notes,
                    )
                )

        if row.five_to_eight:
            bucket = sessions[COMBINED_DATE]
            for reps, weight, duration, notes in parse_weight_with_override(
                row.five_to_eight,
                default_reps=row.min_reps,
                note_suffix=row.note_suffix,
            ):
                bucket.append(
                    SetRow(
                        exercise_id=resolved_ids[row.base_name],
                        set_order=len(bucket) + 1,
                        reps=reps,
                        weight=weight,
                        duration_secs=duration,
                        notes=notes,
                    )
                )

    return {date_value: rows for date_value, rows in sessions.items() if rows}


def delete_range(conn: sqlite3.Connection) -> tuple[int, int]:
    start = dt.date(2026, 2, 5)
    end = dt.date(2026, 2, 14)
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


def insert_sessions(conn: sqlite3.Connection, sessions: dict[dt.date, list[SetRow]]) -> tuple[int, int]:
    inserted_sessions = 0
    inserted_sets = 0
    for date_value in sorted(sessions):
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        if date_value == COMBINED_DATE:
            note = "Shoulder injury follow-up: combined 5-8 rep session"
        else:
            group_index = {
                dt.date(2026, 2, 5): 1,
                dt.date(2026, 2, 6): 2,
                dt.date(2026, 2, 7): 3,
            }[date_value]
            note = f"Shoulder injury block Group {group_index} 6-9 reps"
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
    parser.add_argument("tsv_path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--create-missing-exercises", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = parse_tsv(args.tsv_path.resolve())
    with sqlite3.connect(args.db.resolve()) as conn:
        preview_mode = not args.apply and args.create_missing_exercises
        if preview_mode:
            conn.execute("SAVEPOINT feb_injury_preview")

        resolved_ids, resolved_names, unmatched = resolve_exercises(
            conn,
            rows,
            create_missing=args.create_missing_exercises,
        )
        sessions = build_sessions(rows, resolved_ids) if not unmatched else {}

        print(f"Rows: {len(rows)}")
        print(f"Resolved exercises: {len(resolved_ids)}")
        print(f"Unmatched exercises: {len(unmatched)}")
        print(f"Sessions with data: {len(sessions)}")
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

        if not args.apply:
            if preview_mode:
                conn.execute("ROLLBACK TO feb_injury_preview")
                conn.execute("RELEASE feb_injury_preview")
            print("Audit only.")
            return 0

        if unmatched:
            print("Refusing to import while unmatched exercises remain.")
            return 1

        deleted_sets, deleted_sessions = delete_range(conn)
        inserted_sessions, inserted_sets = insert_sessions(conn, sessions)
        conn.commit()
        print(f"Deleted {deleted_sessions} sessions and {deleted_sets} sets in the injury range.")
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
