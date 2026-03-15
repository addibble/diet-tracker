#!/usr/bin/env python3
"""Audit or import Winter Bulk Phase 3 CSV."""

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
PHASE_COLUMNS = [("2/16", dt.date(2026, 2, 15), dt.date(2026, 2, 16)), ("Week 2", dt.date(2026, 2, 23), dt.date(2026, 2, 24))]
GROUP_SPLIT = 3

ALIAS_MAP: dict[str, str] = {
    "abductor machine": "Hip Abduction Machine",
    "adductor machine": "Hip Adduction Machine",
    "cable bar curl": "Cable Bar Curl",
    "cable fly": "Cable Fly",
    "cable lateral raise": "Single-Arm Cable Lateral Raise (Left Only)",
    "cable row": "Seated Cable Row",
    "cable woodchopper heavy": "Cable Woodchoppers (Abs)",
    "cable woodchoppers burnout": "Cable Woodchoppers (Abs)",
    "face pull": "Face Pulls",
    "ham curl machine": "Seated Leg Curl",
    "neutral grip lat pulldown": "Lat Pulldown",
    "pull ups": "Assisted Pull-Ups",
    "rear delt machine": "Pec Deck (Rear Delt)",
    "reverse crunch burnout": "Reverse Crunch + isometric crunch",
    "seated tib raises": "Seated Tib DB Lift",
    "smith machine incline press": "Incline Barbell Press",
    "weighted plank burnout": "Weighted Plank",
}

CREATE_NAME_MAP: dict[str, str] = {
    "high to low cable fly": "High-to-Low Cable Fly",
    "hanging leg raises": "Hanging Leg Raises",
    "incline neutral grip db press": "Incline Dumbbell Press",
    "low cable chest fly burnout": "Low-High Cable Fly",
    "machine chest press": "Machine Chest Press",
    "rope cable curl burnout": "Rope Cable Curl",
    "single arm cable curl": "Single-Arm Cable Curl",
}

PAREN_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<note>[^)]*)\)\s*$")


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    base_name: str
    note_suffix: str | None
    rep_range: str
    group_index: int
    first_block: str
    second_block: str


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


def parse_csv(path: Path) -> list[ExerciseRow]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
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
                    first_block=(raw.get("2/16") or "").strip(),
                    second_block=(raw.get("Week 2") or "").strip(),
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


def parse_rep_range(text: str) -> int:
    compact = unicodedata.normalize("NFKC", text).lower().replace("–", "-").strip()
    match = re.search(r"(\d+)", compact)
    return int(match.group(1)) if match else 1


def parse_cell(raw_value: str, *, default_reps: int, note_suffix: str | None) -> list[tuple[int | None, float | None, int | None, str | None]]:
    value = unicodedata.normalize("NFKC", raw_value).strip()
    lower = value.lower().replace("lb", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return [(default_reps, float(value), None, note_suffix)] * 3
    if re.fullmatch(r"\d+@\d+s", lower):
        reps, hold = re.fullmatch(r"(\d+)@(\d+)s", lower).groups()
        return [(int(reps), 0.0, None, merge_notes(note_suffix, f"hold {hold}s"))] * 3
    if re.fullmatch(r"\d+x\d+s", lower):
        sets, secs = re.fullmatch(r"(\d+)x(\d+)s", lower).groups()
        return [(1, 0.0, int(secs), merge_notes(note_suffix, f"{secs}s"))] * int(sets)
    weighted = re.fullmatch(r"(\d+(?:\.\d+)?)@(\d+(?:,\d+)*)", lower)
    if weighted:
        weight = float(weighted.group(1))
        reps_list = [int(x) for x in weighted.group(2).split(",")]
        return [(reps, weight, None, note_suffix) for reps in reps_list]
    weighted_pairs = re.fullmatch(
        r"(\d+(?:\.\d+)?)@(\d+),(\d+(?:\.\d+)?)@(\d+)",
        lower,
    )
    if weighted_pairs:
        return [
            (int(weighted_pairs.group(2)), float(weighted_pairs.group(1)), None, note_suffix),
            (int(weighted_pairs.group(4)), float(weighted_pairs.group(3)), None, note_suffix),
        ]
    raise ValueError(f"Unsupported cell value: {raw_value!r}")


def build_sessions(rows: list[ExerciseRow], resolved_ids: dict[str, int]) -> dict[dt.date, list[SetRow]]:
    sessions: dict[dt.date, list[SetRow]] = {}
    for _label, first_date, second_date in PHASE_COLUMNS:
        sessions.setdefault(first_date, [])
        sessions.setdefault(second_date, [])
    for row in rows:
        default_reps = parse_rep_range(row.rep_range)
        for block_index, value in enumerate((row.first_block, row.second_block)):
            if not value:
                continue
            first_date, second_date = PHASE_COLUMNS[block_index][1], PHASE_COLUMNS[block_index][2]
            target_date = first_date if row.group_index <= GROUP_SPLIT else second_date
            bucket = sessions[target_date]
            for reps, weight, duration, notes in parse_cell(value, default_reps=default_reps, note_suffix=row.note_suffix):
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
    start = dt.date(2026, 2, 15)
    end = dt.date(2026, 2, 24)
    session_ids = [row[0] for row in conn.execute("SELECT id FROM workout_sessions WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat()))]
    deleted_sets = 0
    for sid in session_ids:
        deleted_sets += conn.execute("DELETE FROM workout_sets WHERE session_id = ?", (sid,)).rowcount
    deleted_sessions = conn.execute("DELETE FROM workout_sessions WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat())).rowcount
    return deleted_sets, deleted_sessions


def insert_sessions(conn: sqlite3.Connection, sessions: dict[dt.date, list[SetRow]]) -> tuple[int, int]:
    inserted_sessions = 0
    inserted_sets = 0
    notes_by_date = {
        dt.date(2026, 2, 15): "Winter Bulk Phase 3 block 1 split A",
        dt.date(2026, 2, 16): "Winter Bulk Phase 3 block 1 split B",
        dt.date(2026, 2, 23): "Winter Bulk Phase 3 block 2 split A",
        dt.date(2026, 2, 24): "Winter Bulk Phase 3 block 2 split B",
    }
    for date_value in sorted(sessions):
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        cursor = conn.execute(
            "INSERT INTO workout_sessions (date, started_at, finished_at, notes, created_at) VALUES (?, NULL, NULL, ?, ?)",
            (date_value.isoformat(), notes_by_date[date_value], created_at),
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
            conn.execute("SAVEPOINT winter_phase3_preview")
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
                conn.execute("ROLLBACK TO winter_phase3_preview")
                conn.execute("RELEASE winter_phase3_preview")
            print("Audit only.")
            return 0

        if unmatched:
            print("Refusing to import while unmatched exercises remain.")
            return 1

        deleted_sets, deleted_sessions = delete_range(conn)
        inserted_sessions, inserted_sets = insert_sessions(conn, sessions)
        conn.commit()
        print(f"Deleted {deleted_sessions} sessions and {deleted_sets} sets in the phase 3 range.")
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
