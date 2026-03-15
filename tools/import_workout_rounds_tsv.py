#!/usr/bin/env python3
"""Audit or import round/group workout TSV data."""

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
    "abduction": "Hip Abduction Machine",
    "abductor machine": "Hip Abduction Machine",
    "adduction": "Hip Adduction Machine",
    "adductor machine": "Hip Adduction Machine",
    "cable curl": "Straight-Bar Cable Curl",
    "cable curl with drop set": "Straight-Bar Cable Curl",
    "calf raises": "Seated Calf Raise",
    "chest flys": "Pec Deck",
    "glute drive": "Glute Drive Machine",
    "leg extension": "Single Leg Extension",
    "overhead cable triceps extension": "Overhead Rope Triceps Extension",
    "rear delt machine": "Pec Deck (Rear Delt)",
    "rope pushdowns": "Triceps Rope Pushdown",
    "seated row": "Seated Cable Row",
    "seated hamstring curl": "Seated Leg Curl",
    "seated overhead db press": "Seated DB Shoulder Press",
    "shrugs": "Dumbbell Shrugs",
    "shrugs db": "Dumbbell Shrugs",
    "wide pull ups": "Wide Pull-ups (Assist)",
}

CREATE_NAME_MAP: dict[str, str] = {
    "arnold press": "Arnold Press",
    "incline hammer curl": "Incline Hammer Curl",
    "preacher curl": "Preacher Curl",
    "rear delt machine": "Pec Deck (Rear Delt)",
}

PAREN_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<note>[^)]*)\)\s*$")


@dataclass(frozen=True)
class ExerciseRow:
    raw_name: str
    base_name: str
    paren_note: str | None
    sets_spec: str
    spec_kind: str
    group_index: int
    round_values: tuple[str, ...]


@dataclass(frozen=True)
class SetRow:
    exercise_id: int
    set_order: int
    reps: int | None
    weight: float | None
    note: str | None


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).lower().strip()
    normalized = normalized.replace("&", "and")
    normalized = normalized.replace("‑", "-").replace("–", "-")
    normalized = re.sub(r"[()]", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def split_paren_name(name: str) -> tuple[str, str | None]:
    match = PAREN_RE.fullmatch(name.strip())
    if not match:
        return name.strip(), None
    return match["name"].strip(), match["note"].strip()


def parse_group(text: str) -> int:
    match = re.search(r"(\d+)", text)
    if not match:
        raise ValueError(f"Unsupported group value: {text!r}")
    return int(match.group(1))


def parse_tsv(path: Path) -> tuple[int, list[ExerciseRow]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("TSV has no header row")
        spec_kind = ""
        if "Sets x Reps" in reader.fieldnames:
            spec_kind = "reps"
            spec_column = "Sets x Reps"
        elif "Sets x Weight" in reader.fieldnames:
            spec_kind = "weight"
            spec_column = "Sets x Weight"
        else:
            raise ValueError("TSV must contain either 'Sets x Reps' or 'Sets x Weight'.")
        round_columns = [name for name in reader.fieldnames if name.startswith("Round ")]
        rows: list[ExerciseRow] = []
        for raw in reader:
            raw_name = (raw.get("Exercise") or "").strip()
            if not raw_name:
                continue
            base_name, paren_note = split_paren_name(raw_name)
            rows.append(
                ExerciseRow(
                    raw_name=raw_name,
                    base_name=base_name,
                    paren_note=paren_note,
                    sets_spec=(raw.get(spec_column) or "").strip(),
                    spec_kind=spec_kind,
                    group_index=parse_group((raw.get("Group") or "").strip()),
                    round_values=tuple((raw.get(col) or "").strip() for col in round_columns),
                )
            )
    return len(round_columns), rows


def resolve_exercise_names(
    conn: sqlite3.Connection,
    rows: list[ExerciseRow],
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

        unmatched.append(key)

    return resolved_ids, resolved_names, unmatched


def create_exercise(conn: sqlite3.Connection, raw_name: str) -> int:
    normalized = normalize_name(raw_name)
    exercise_name = CREATE_NAME_MAP.get(normalized, raw_name)
    existing = conn.execute(
        "SELECT id FROM exercises WHERE name = ?",
        (exercise_name,),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
    cursor = conn.execute(
        """
        INSERT INTO exercises (name, equipment, notes, created_at)
        VALUES (?, NULL, NULL, ?)
        """,
        (exercise_name, created_at),
    )
    return cursor.lastrowid


def parse_sets_reps(text: str) -> tuple[int, int | None]:
    compact = unicodedata.normalize("NFKC", text).lower().strip()
    compact = compact.replace("×", "x").replace("–", "-")
    match = re.search(r"(?P<sets>\d+)\s*x\s*(?P<low>\d+)", compact)
    if not match:
        return 1, None
    return int(match["sets"]), int(match["low"])


def parse_sets_weight(text: str) -> tuple[int, float | None]:
    compact = unicodedata.normalize("NFKC", text).lower().strip()
    compact = compact.replace("×", "x").replace("–", "-")
    match = re.search(r"(?P<sets>\d+)\s*x\s*(?P<weight>\d+(?:\.\d+)?)", compact)
    if not match:
        return 1, None
    return int(match["sets"]), float(match["weight"])


def parse_weight_round_value(
    raw_value: str,
    *,
    default_sets: int,
    default_weight: float | None,
) -> tuple[int, int | None, float | None]:
    compact = unicodedata.normalize("NFKC", raw_value).lower().strip()
    compact = compact.replace("×", "x").replace("–", "-")

    reps_only = re.fullmatch(r"(\d+)", compact)
    if reps_only:
        return default_sets, int(reps_only.group(1)), default_weight

    weight_reps = re.fullmatch(
        r"(?P<weight>\d+(?:\.\d+)?)x(?P<reps>\d+)(?:x(?P<sets>\d+))?",
        compact,
    )
    if weight_reps:
        return (
            int(weight_reps.group("sets") or default_sets),
            int(weight_reps.group("reps")),
            float(weight_reps.group("weight")),
        )

    raise ValueError(f"Unsupported round value {raw_value!r} for Sets x Weight mode.")


def build_session_rows(
    rows: list[ExerciseRow],
    resolved_ids: dict[str, int],
    rounds: int,
) -> dict[tuple[int, int], list[SetRow]]:
    sessions: dict[tuple[int, int], list[SetRow]] = {}
    for round_index in range(1, rounds + 1):
        for group_index in sorted({row.group_index for row in rows}):
            bucket = sessions.setdefault((group_index, round_index), [])
            for row in rows:
                if row.group_index != group_index:
                    continue
                raw_value = row.round_values[round_index - 1]
                if not raw_value:
                    continue
                exercise_id = resolved_ids[row.base_name]
                if row.spec_kind == "reps":
                    set_count, reps = parse_sets_reps(row.sets_spec)
                    weight = float(raw_value)
                else:
                    default_sets, default_weight = parse_sets_weight(row.sets_spec)
                    set_count, reps, weight = parse_weight_round_value(
                        raw_value,
                        default_sets=default_sets,
                        default_weight=default_weight,
                    )
                for _ in range(set_count):
                    bucket.append(
                        SetRow(
                            exercise_id=exercise_id,
                            set_order=len(bucket) + 1,
                            reps=reps,
                            weight=weight,
                            note=row.paren_note,
                        )
                    )
    return {key: value for key, value in sessions.items() if value}


def generate_dates_for_month(
    year: int,
    month: int,
    *,
    on_days: int,
    off_days: int,
) -> list[dt.date]:
    cursor = dt.date(year, month, 1)
    next_month = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    dates: list[dt.date] = []
    streak = 0
    off = 0
    while cursor < next_month:
        if streak < on_days:
            dates.append(cursor)
            streak += 1
            if streak == on_days:
                off = off_days
        else:
            off -= 1
            if off == 0:
                streak = 0
        cursor += dt.timedelta(days=1)
    return dates


def delete_month(conn: sqlite3.Connection, year: int, month: int) -> tuple[int, int]:
    start = dt.date(year, month, 1)
    next_month = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    session_ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM workout_sessions WHERE date >= ? AND date < ?",
            (start.isoformat(), next_month.isoformat()),
        )
    ]
    deleted_sets = 0
    for session_id in session_ids:
        deleted_sets += conn.execute(
            "DELETE FROM workout_sets WHERE session_id = ?",
            (session_id,),
        ).rowcount
    deleted_sessions = conn.execute(
        "DELETE FROM workout_sessions WHERE date >= ? AND date < ?",
        (start.isoformat(), next_month.isoformat()),
    ).rowcount
    return deleted_sets, deleted_sessions


def insert_sessions(
    conn: sqlite3.Connection,
    dates: list[dt.date],
    sessions: dict[tuple[int, int], list[SetRow]],
    *,
    year: int,
    month: int,
) -> tuple[int, int]:
    ordered_keys = sorted(sessions, key=lambda item: (item[1], item[0]))
    inserted_sessions = 0
    inserted_sets = 0
    for date_value, (group_index, round_index) in zip(dates, ordered_keys):
        created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
        note = f"October 2025 Group {group_index} Round {round_index}"
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
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    session_id,
                    row.exercise_id,
                    row.set_order,
                    row.reps,
                    row.weight,
                    row.note,
                    created_at,
                ),
            )
            inserted_sets += 1
    return inserted_sessions, inserted_sets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tsv_path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--month", type=int, default=10)
    parser.add_argument("--on-days", type=int, default=4)
    parser.add_argument("--off-days", type=int, default=2)
    parser.add_argument("--create-missing-exercises", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rounds, rows = parse_tsv(args.tsv_path.resolve())
    with sqlite3.connect(args.db.resolve()) as conn:
        resolved_ids, resolved_names, unmatched = resolve_exercise_names(conn, rows)
        if args.apply and unmatched and args.create_missing_exercises:
            for raw_name in sorted(set(unmatched)):
                exercise_id = create_exercise(conn, raw_name)
                resolved_ids[raw_name] = exercise_id
                resolved_names[raw_name] = CREATE_NAME_MAP.get(normalize_name(raw_name), raw_name)
            unmatched = []

        sessions = build_session_rows(rows, resolved_ids, rounds) if not unmatched else {}
        dates = generate_dates_for_month(
            args.year,
            args.month,
            on_days=args.on_days,
            off_days=args.off_days,
        )

        print(f"Rows: {len(rows)}")
        print(f"Rounds: {rounds}")
        print(f"Resolved exercises: {len(resolved_ids)}")
        print(f"Unmatched exercises: {len(unmatched)}")
        print(f"Sessions with data: {len(sessions)}")
        print(f"Available dates in {args.year:04d}-{args.month:02d}: {len(dates)}")
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

        if len(sessions) > len(dates):
            print(
                f"Schedule overflow: {len(sessions)} sessions do not fit into "
                f"{len(dates)} dates with a {args.on_days}-on/{args.off_days}-off pattern."
            )
            return 1

        if not args.apply:
            print("Audit only.")
            return 0

        if unmatched:
            print("Refusing to import while unmatched exercises remain.")
            return 1

        deleted_sets, deleted_sessions = delete_month(conn, args.year, args.month)
        inserted_sessions, inserted_sets = insert_sessions(
            conn,
            dates,
            sessions,
            year=args.year,
            month=args.month,
        )
        conn.commit()
        print(f"Deleted {deleted_sessions} sessions and {deleted_sets} sets in {args.year:04d}-{args.month:02d}.")
        print(f"Inserted {inserted_sessions} sessions and {inserted_sets} sets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
