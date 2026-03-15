#!/usr/bin/env python3
"""Audit or import the late-February / early-March 2026 block."""

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
ROUND_COLUMNS = ["Round 1", "Round 2", "Round 3"]
SCHEDULE_DATES = [
    dt.date(2026, 2, 26),
    dt.date(2026, 2, 27),
    dt.date(2026, 2, 28),
    dt.date(2026, 3, 2),
    dt.date(2026, 3, 3),
    dt.date(2026, 3, 4),
    dt.date(2026, 3, 6),
    dt.date(2026, 3, 7),
    dt.date(2026, 3, 8),
]

ALIAS_MAP: dict[str, str] = {
    "abductor machine": "Hip Abduction Machine",
    "adductor machine": "Hip Adduction Machine",
    "cable crunch": "Cable Crunch",
    "cable row": "Seated Cable Row",
    "cable woodchopper": "Cable Woodchoppers (Abs)",
    "ham curl": "Seated Leg Curl",
    "ham curl machine": "Seated Leg Curl",
    "hanging leg raise": "Hanging Leg Raises",
    "rear delt control mini sets": "Pec Deck (Rear Delt)",
    "straight bar cable curl burnout": "Cable Bar Curl",
    "tib raise": "Seated Tib DB Lift",
    "triceps rope pushdown": "Triceps Rope Pushdown",
}

CREATE_NAME_MAP: dict[str, str] = {
    "rear delt control mini sets": "Pec Deck (Rear Delt)",
}
BARE_NUMERIC_REP_EXERCISES = {"Reverse Crunch + isometric crunch"}

PAREN_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<note>[^)]*)\)\s*$")


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    base_name: str
    note_suffix: str | None
    rep_range: str
    group_index: int
    rounds: tuple[str, ...]


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
    normalized = normalized.replace("&", "and").replace("‑", "-").replace("–", "-")
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
    vals = [part.strip() for part in parts if part and part.strip()]
    return "; ".join(vals) if vals else None


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
                    rep_range=(raw.get("Rep Range") or "").strip(),
                    group_index=int((raw.get("Group #") or "0").strip()),
                    rounds=tuple((raw.get(col) or "").strip() for col in ROUND_COLUMNS),
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
        alias = ALIAS_MAP.get(normalized)
        if alias and alias in exact:
            resolved_ids[key] = exact[alias]
            resolved_names[key] = alias
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
                "INSERT INTO exercises (name, equipment, notes, created_at) VALUES (?, NULL, NULL, ?)",
                (created_name, created_at),
            )
            resolved_ids[key] = cursor.lastrowid
            resolved_names[key] = created_name
            exact[created_name] = cursor.lastrowid
            by_norm[normalize_name(created_name)] = (cursor.lastrowid, created_name)
            continue
        unmatched.append(key)
    return resolved_ids, resolved_names, unmatched


def parse_rep_range(text: str) -> tuple[int, int | None]:
    compact = unicodedata.normalize("NFKC", text).lower().replace("–", "-").strip()
    sets_match = re.search(r"(\d+)\s*sets?", compact)
    rep_match = re.search(r"(\d+)", compact)
    sets = int(sets_match.group(1)) if sets_match else 3
    reps = int(rep_match.group(1)) if rep_match else None
    if "seconds" in compact or "sec" in compact:
        reps = None
    return sets, reps


def parse_cell(
    raw_value: str,
    *,
    exercise_name: str,
    default_sets: int,
    default_reps: int | None,
    note_suffix: str | None,
) -> list[tuple[int | None, float | None, int | None, str | None]]:
    value = unicodedata.normalize("NFKC", raw_value).strip()
    lower = value.lower()

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        if exercise_name in BARE_NUMERIC_REP_EXERCISES:
            return [(int(float(value)), 0.0, None, note_suffix)] * default_sets
        return [(default_reps, float(value), None, note_suffix)] * default_sets

    if re.fullmatch(r"\d+\s*no hold", lower):
        reps = int(re.search(r"\d+", lower).group(0))
        return [(reps, 0.0, None, merge_notes(note_suffix, "no hold"))] * default_sets

    if re.fullmatch(r"\d+\s*w\s+.+", lower):
        reps = int(re.search(r"\d+", lower).group(0))
        note = lower.split(" ", 1)[1].replace("w ", "", 1).strip()
        return [(reps, 0.0, None, merge_notes(note_suffix, note))] * default_sets

    if re.fullmatch(r"\d+s", lower):
        secs = int(re.search(r"\d+", lower).group(0))
        return [(1, 0.0, secs, merge_notes(note_suffix, f"{secs}s"))] * default_sets

    if re.fullmatch(r"\d+sec\s*x\s*\d+", lower):
        secs, sets = map(int, re.findall(r"\d+", lower))
        return [(1, 0.0, secs, merge_notes(note_suffix, f"{secs}s"))] * sets

    if re.fullmatch(r"\d+x\d+\s+no hang high lift", lower):
        reps, sets = map(int, re.findall(r"\d+", lower)[:2])
        return [(reps, 0.0, None, merge_notes(note_suffix, "no hang high lift"))] * sets

    if re.fullmatch(r"\d+(?:\.\d+)?\s*\(\d+\s*sets\s*of\s*\d+\)", lower):
        nums = re.findall(r"\d+(?:\.\d+)?", lower)
        weight = float(nums[0])
        sets = int(nums[1])
        reps = int(nums[2])
        return [(reps, weight, None, note_suffix)] * sets

    raise ValueError(f"Unsupported cell value: {raw_value!r}")


def build_sessions(rows: list[ExerciseRow], resolved_ids: dict[str, int]) -> dict[dt.date, list[SetRow]]:
    session_keys: list[tuple[int, int]] = []
    for round_index in range(1, len(ROUND_COLUMNS) + 1):
        for group_index in sorted({row.group_index for row in rows}):
            if any(row.group_index == group_index and row.rounds[round_index - 1] for row in rows):
                session_keys.append((group_index, round_index))
    if len(session_keys) != len(SCHEDULE_DATES):
        raise ValueError(f"Expected {len(SCHEDULE_DATES)} sessions but found {len(session_keys)}.")

    sessions: dict[dt.date, list[SetRow]] = {}
    for date_value, (group_index, round_index) in zip(SCHEDULE_DATES, session_keys):
        bucket: list[SetRow] = []
        for row in rows:
            if row.group_index != group_index:
                continue
            raw_value = row.rounds[round_index - 1]
            if not raw_value:
                continue
            default_sets, default_reps = parse_rep_range(row.rep_range)
            for reps, weight, duration, notes in parse_cell(
                raw_value,
                exercise_name=row.base_name,
                default_sets=default_sets,
                default_reps=default_reps,
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
        sessions[date_value] = bucket
    return sessions


def delete_range(conn: sqlite3.Connection) -> tuple[int, int]:
    start = SCHEDULE_DATES[0]
    end = SCHEDULE_DATES[-1]
    session_ids = [row[0] for row in conn.execute("SELECT id FROM workout_sessions WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat()))]
    deleted_sets = 0
    for sid in session_ids:
        deleted_sets += conn.execute("DELETE FROM workout_sets WHERE session_id = ?", (sid,)).rowcount
    deleted_sessions = conn.execute("DELETE FROM workout_sessions WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat())).rowcount
    return deleted_sets, deleted_sessions


def insert_sessions(conn: sqlite3.Connection, sessions: dict[dt.date, list[SetRow]]) -> tuple[int, int]:
    inserted_sessions = 0
    inserted_sets = 0
    for date_value in sorted(sessions):
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        cursor = conn.execute(
            "INSERT INTO workout_sessions (date, started_at, finished_at, notes, created_at) VALUES (?, NULL, NULL, ?, ?)",
            (date_value.isoformat(), f"Late Feb/March 2026 block {date_value.isoformat()}", created_at),
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
                (session_id, row.exercise_id, row.set_order, row.reps, row.weight, row.duration_secs, row.notes, created_at),
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
            conn.execute("SAVEPOINT late_feb_preview")
        resolved_ids, resolved_names, unmatched = resolve_exercises(conn, rows, create_missing=args.create_missing_exercises)
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
                conn.execute("ROLLBACK TO late_feb_preview")
                conn.execute("RELEASE late_feb_preview")
            print("Audit only.")
            return 0

        if unmatched:
            print("Refusing to import while unmatched exercises remain.")
            return 1

        deleted_sets, deleted_sessions = delete_range(conn)
        inserted_sessions, inserted_sets = insert_sessions(conn, sessions)
        conn.commit()
        print("Rest days: 2026-03-01 and 2026-03-05.")
        print(f"Deleted {deleted_sessions} sessions and {deleted_sets} sets in the late-Feb/March range.")
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
