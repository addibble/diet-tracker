#!/usr/bin/env python3
"""Rebuild workout_sets for the imported spreadsheet block.

This repairs sessions 20-70 by regenerating `workout_sets` from the spreadsheet
pattern while preserving the current `session_id` and `exercise_id` usage in the
database. Some exercises were merged onto the same exercise_id during import; this
script intentionally keeps those IDs and rebuilds the set blocks in-place.

By default this is a dry run. Pass `--apply` to delete and recreate sets for the
selected sessions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parents[1] / "diet_tracker.db"
ROUND_REPS = {1: 13, 2: 12, 3: 11, 4: 10, 5: 9, 6: 8, 7: 7}
NOTE_RE = re.compile(r"Group\s+(?P<group>\d+)\s+Round\s+(?P<round>\d+)$")


@dataclass(frozen=True)
class BlockSpec:
    exercise_name: str
    set_count: int
    weights: tuple[float | None, ...]
    reps: tuple[int | None, ...] | None = None
    durations: tuple[int | None, ...] | None = None
    notes: tuple[str | None, ...] | None = None
    set_durations: tuple[tuple[int | None, ...] | None, ...] | None = None
    set_notes: tuple[tuple[str | None, ...] | None, ...] | None = None


def repeated(value: float | None, size: int = 7) -> tuple[float | None, ...]:
    return tuple(value for _ in range(size))


def round_reps(
    *,
    r1: int = 13,
    r2: int = 12,
    r3: int = 11,
    r4: int = 10,
    r5: int = 9,
    r6: int = 8,
    r7: int = 7,
) -> tuple[int, ...]:
    return (r1, r2, r3, r4, r5, r6, r7)


GROUP_SPECS: dict[int, list[BlockSpec]] = {
    0: [
        BlockSpec("Hammer Curl", 3, (25, 30, 30, 30, 30, 35, 35)),
        BlockSpec("Seated Hamstring Curl", 3, (150, 150, 160, 160, 170, 170, 180)),
        BlockSpec("Rear Delt Machine", 3, (90, 80, 90, 90, 100, 110, 110)),
        BlockSpec("Lateral Raise", 3, (15, 15, 17.5, 20, 20, 25, 25)),
        BlockSpec("Preacher Curl", 3, (65, 75, 75, 75, 75, 75, 75)),
        BlockSpec("Hip Adduction Machine", 3, (160, 180, 190, 190, 190, 190, 200)),
        BlockSpec("Hip Abduction Machine", 3, (150, 160, 170, 170, 170, 130, 130)),
        BlockSpec("Barbell Curl", 3, (50, 50, 60, 60, 70, 80, 80)),
        BlockSpec("Cable Curl", 3, (42.5, 47.5, 47.5, 47.5, 52.5, 52.5, 55)),
    ],
    1: [
        BlockSpec("Bench Press", 3, (95, 115, 125, 135, 145, 155, 145)),
        BlockSpec("Pec Deck", 3, (90, 100, 110, 110, 120, 130, 140)),
        BlockSpec("Incline Dumbbell Press", 3, repeated(50)),
        BlockSpec("Cable Fly", 3, (20, 20, 20, 20, 20, 25, 27.5)),
        BlockSpec("Seated DB Shoulder Press", 3, (17.5, 20, 25, 25, 30, 35, 35)),
        BlockSpec("Single-Arm Cable Lateral Raise (Left Only)", 3, (2.5, 2.5, 7.5, 10, 12.5, 15, 12.5)),
        BlockSpec("Triceps Rope Pushdown", 3, (42.5, 47.5, 52.5, 55, 57.5, 70, 70)),
        BlockSpec("Cable Woodchoppers (Abs)", 3, (32.5, 37.5, 40, 42.5, 47.5, 47.5, 52.5)),
    ],
    2: [
        BlockSpec("Lat Pulldown", 3, (135, 140, 150, 150, 150, 160, 152.5)),
        BlockSpec("Seated Row", 3, (135, 110, 120, 135, 150, 155, 152.5)),
        BlockSpec("Assisted Pull-Ups", 3, (98, 98, 91.5, 85, 78.5, 72, 70)),
        BlockSpec("Chest-Supported Row", 3, (55, 55, 55, 60, 60, 60, 65)),
        BlockSpec("Face Pulls", 3, (52.5, 55, 57.5, 62.5, 65, 62.5, 62.5)),
        BlockSpec("Single-Arm Cable Lateral Raise (Left Only)", 5, (2.5, 5, 7.5, 10, 12.5, 5, 5)),
        BlockSpec("Rear Delt Machine", 3, (70, 80, 90, 100, 110, 110, 110)),
        BlockSpec("Back Extension Machine", 3, (None, None, 150, 160, 170, 200, 200)),
    ],
    3: [
        BlockSpec("Seated Hamstring Curl", 3, (140, 150, 160, 160, 170, 170, 180)),
        BlockSpec(
            "Seated Tib DB Lift",
            3,
            (20, 25, 30, 30, 30, 30, 35),
            notes=(None, None, None, "hold 1s", "hold 2s", "hold 2s", None),
        ),
        BlockSpec("Seated Calf Raise", 3, (135, 145, 150, 160, 170, 180, 180)),
        BlockSpec("Hip Adduction Machine", 3, (170, 180, 190, 190, 190, 190, 200)),
        BlockSpec("Hip Abduction Machine", 3, (150, 160, 160, 170, 170, 130, 140)),
        BlockSpec("Pec Deck", 3, (80, 90, 100, 120, 130, 130, 140)),
        BlockSpec("Cable Fly", 3, (17.5, 17.5, 17.5, 20, 22.5, 25, 27.5)),
        BlockSpec("Leg Press", 3, (330, 350, 350, 380, 400, 415, 430)),
    ],
    4: [
        BlockSpec("Barbell Curl", 3, (50, 50, 60, 60, 70, 80, 85)),
        BlockSpec("Hammer Curl", 3, (25, 25, 25, 25, 30, 30, 35)),
        BlockSpec("Rope Cable Curl", 3, (42.5, 47.5, 45, 45, 47.5, 47.5, 47.5)),
        BlockSpec("Assisted Pull-Ups", 3, (91.5, 91.5, 85, 78.5, 72, 65.5, 59)),
        BlockSpec("Lat Pulldown", 3, (135, 140, 150, 150, 155, 155, 165)),
        BlockSpec("Landmine Press", 3, (80, 75, 80, 85, 85, 90, 75)),
        BlockSpec("Landmine Row", 3, (100, 100, 105, 110, 110, 115, 100)),
        BlockSpec(
            "Cable bar curl burnout 50-60%",
            1,
            (None, None, None, None, None, 0, 0),
            reps=(None, None, None, None, None, 23, 25),
        ),
    ],
    5: [
        BlockSpec("Seated DB Shoulder Press", 3, (20, 25, 25, 25, 30, 35, 35)),
        BlockSpec("Arnold Press", 3, (15, 17.5, 17.5, 17.5, 20, 20, 20)),
        BlockSpec("Machine Shoulder Press", 3, (70, 80, 70, 70, 80, 80, 80)),
        BlockSpec("Single-Arm Cable Lateral Raise (Left Only)", 5, (5, 5, 7.5, 10, 10, 10, 2.5)),
        BlockSpec("Machine Chest Press", 3, (130, 130, 140, 140, 140, None, 160)),
        BlockSpec("Lateral Raise", 4, (15, 15, 15, 20, 20, 20, None)),
        BlockSpec("Cable Fly", 3, (20, 20, 20, 25, 25, 25, 25)),
        BlockSpec("Pec Deck", 3, (80, 100, 110, 120, 120, 130, 130)),
    ],
    6: [
        BlockSpec(
            "Weighted Decline Crunch",
            3,
            (10, 25, 10, 25, 25, 35, 35),
            reps=round_reps(r7=15),
        ),
        BlockSpec("Cable Crunch", 3, (None, None, None, None, None, 62.5, 62.5)),
        BlockSpec(
            "Hanging Leg Raises",
            3,
            (0, 0, 0, 0, 0, 0, 0),
            notes=(
                "BW",
                "BW",
                "BW hold 1s",
                "BW 2s extended legs",
                "2s extended",
                "2s extended",
                "1s extended",
            ),
        ),
        BlockSpec(
            "Weighted Plank",
            2,
            (None, None, None, None, None, 45, 45),
            reps=(None, None, None, None, None, 1, 1),
            set_durations=(None, None, None, None, None, (30, 30), (60, 35)),
            set_notes=(None, None, None, None, None, ("30s", "30s"), ("60s", "35s")),
        ),
        BlockSpec("Ab Crunch Machine", 3, (120, 120, 120, 130, 140, 140, 140)),
        BlockSpec(
            "Seated Tib DB Lift",
            3,
            (None, None, None, None, None, 35, 35),
            notes=(None, None, None, None, None, "slow eccentric", "slow eccentric"),
        ),
        BlockSpec(
            "Seated Calf Raise",
            3,
            (None, None, None, None, None, 180, 180),
            notes=(None, None, None, None, None, "slow eccentric", "slow eccentric"),
        ),
    ],
}

EXERCISE_CLONES: dict[str, str] = {
    "Seated Hamstring Curl": "Seated Leg Curl",
    "Rear Delt Machine": "Pec Deck (Rear Delt)",
    "Preacher Curl": "Barbell Curl",
    "Cable Curl": "Straight-Bar Cable Curl",
    "Bench Press": "Flat Dumbbell Press",
    "Landmine Row": "Landmine Row (Opposite Stance)",
    "Arnold Press": "Seated DB Shoulder Press",
    "Machine Shoulder Press": "Overhead Press Machine",
    "Machine Chest Press": "Flat Dumbbell Press",
    "Hanging Leg Raises": "Hanging Knee Raises",
}


def parse_group_round(notes: str) -> tuple[int, int]:
    match = NOTE_RE.fullmatch(notes.strip())
    if not match:
        raise ValueError(f"Unsupported session notes format: {notes!r}")
    return int(match["group"]), int(match["round"])


def ensure_exercise(conn: sqlite3.Connection, name: str, *, create: bool) -> int:
    row = conn.execute(
        "SELECT id FROM exercises WHERE name = ?",
        (name,),
    ).fetchone()
    if row:
        return row[0]

    if not create:
        raise ValueError(f"Exercise {name!r} does not exist")

    source_name = EXERCISE_CLONES.get(name)
    equipment = None
    notes = None
    source_id = None
    if source_name:
        source = conn.execute(
            "SELECT id, equipment, notes FROM exercises WHERE name = ?",
            (source_name,),
        ).fetchone()
        if source:
            source_id = source[0]
            equipment = source[1]
            notes = source[2]

    created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")
    cursor = conn.execute(
        """
        INSERT INTO exercises (name, equipment, notes, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, equipment, notes, created_at),
    )
    exercise_id = cursor.lastrowid

    if source_id is not None:
        tissues = conn.execute(
            """
            SELECT tissue_id, role, loading_factor, updated_at
            FROM exercise_tissues
            WHERE exercise_id = ?
            """,
            (source_id,),
        ).fetchall()
        for tissue_id, role, loading_factor, updated_at in tissues:
            conn.execute(
                """
                INSERT INTO exercise_tissues (
                    exercise_id, tissue_id, role, loading_factor, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    exercise_id,
                    tissue_id,
                    role,
                    loading_factor,
                    updated_at,
                ),
            )

    return exercise_id


def build_rows_for_session(
    conn: sqlite3.Connection,
    session_id: int,
    session_notes: str,
    *,
    create_missing_exercises: bool,
) -> list[tuple]:
    group_index, round_index = parse_group_round(session_notes)
    if group_index not in GROUP_SPECS:
        raise ValueError(f"No group spec configured for group {group_index}")

    rows: list[tuple] = []
    set_order = 1
    default_reps = ROUND_REPS[round_index]
    created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(sep=" ")

    for block in GROUP_SPECS[group_index]:
        round_slot = round_index - 1
        exercise_id = ensure_exercise(
            conn,
            block.exercise_name,
            create=create_missing_exercises,
        )
        weight = block.weights[round_slot]
        if weight is None:
            continue

        reps = default_reps if block.reps is None else block.reps[round_slot]
        duration = None if block.durations is None else block.durations[round_slot]
        note = None if block.notes is None else block.notes[round_slot]

        per_set_durations = (
            None if block.set_durations is None else block.set_durations[round_slot]
        )
        per_set_notes = None if block.set_notes is None else block.set_notes[round_slot]

        for idx in range(block.set_count):
            row_duration = duration
            if per_set_durations is not None:
                row_duration = per_set_durations[idx]

            row_note = note
            if per_set_notes is not None:
                row_note = per_set_notes[idx]

            rows.append(
                (
                    session_id,
                    exercise_id,
                    set_order,
                    reps,
                    weight,
                    row_duration,
                    None,
                    None,
                    None,
                    row_note,
                    created_at,
                )
            )
            set_order += 1
    return rows


def load_sessions(conn: sqlite3.Connection, min_id: int, max_id: int) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT id, notes
        FROM workout_sessions
        WHERE id BETWEEN ? AND ?
        ORDER BY id
        """,
        (min_id, max_id),
    ).fetchall()


def preview(
    conn: sqlite3.Connection,
    min_id: int,
    max_id: int,
    delete_session_ids: set[int],
) -> list[tuple[int, int, int, str, str]]:
    conn.execute("SAVEPOINT workout_set_preview")
    sessions = load_sessions(conn, min_id, max_id)
    summary: list[tuple[int, int, int, str, str]] = []
    try:
        for session in sessions:
            old_count = conn.execute(
                "SELECT COUNT(*) FROM workout_sets WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0]
            if session["id"] in delete_session_ids:
                summary.append((session["id"], old_count, 0, session["notes"], "DELETE"))
                continue
            new_count = len(
                build_rows_for_session(
                    conn,
                    session["id"],
                    session["notes"],
                    create_missing_exercises=True,
                )
            )
            action = "UNCHANGED" if old_count == new_count else "REBUILD"
            summary.append((session["id"], old_count, new_count, session["notes"], action))
        return summary
    finally:
        conn.execute("ROLLBACK TO workout_set_preview")
        conn.execute("RELEASE workout_set_preview")


def apply(
    conn: sqlite3.Connection,
    min_id: int,
    max_id: int,
    delete_session_ids: set[int],
) -> tuple[int, int, int]:
    sessions = load_sessions(conn, min_id, max_id)
    deleted = 0
    inserted = 0
    deleted_sessions = 0

    for session in sessions:
        if session["id"] in delete_session_ids:
            deleted += conn.execute(
                "DELETE FROM workout_sets WHERE session_id = ?",
                (session["id"],),
            ).rowcount
            deleted_sessions += conn.execute(
                "DELETE FROM workout_sessions WHERE id = ?",
                (session["id"],),
            ).rowcount
            continue

        deleted += conn.execute(
            "DELETE FROM workout_sets WHERE session_id = ?",
            (session["id"],),
        ).rowcount

        rows = build_rows_for_session(
            conn,
            session["id"],
            session["notes"],
            create_missing_exercises=True,
        )
        conn.executemany(
            """
            INSERT INTO workout_sets (
                session_id,
                exercise_id,
                set_order,
                reps,
                weight,
                duration_secs,
                distance_steps,
                rpe,
                rep_completion,
                notes,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted += len(rows)

    conn.commit()
    return deleted, inserted, deleted_sessions


def parse_id_set(raw: str) -> set[int]:
    if not raw.strip():
        return set()
    return {int(part.strip()) for part in raw.split(",") if part.strip()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite DB path.")
    parser.add_argument("--min-id", type=int, default=20)
    parser.add_argument("--max-id", type=int, default=70)
    parser.add_argument(
        "--delete-session-ids",
        type=parse_id_set,
        default=set(),
        help="Comma-separated session IDs to delete before rebuilding, e.g. 20,21.",
    )
    parser.add_argument("--apply", action="store_true", help="Commit the rebuild.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()
    if not db_path.exists():
        print(f"Database file does not exist: {db_path}", file=sys.stderr)
        return 1

    with sqlite3.connect(db_path) as conn:
        summary = preview(conn, args.min_id, args.max_id, args.delete_session_ids)
        total_old = 0
        total_new = 0
        deleted_sessions = 0
        for session_id, old_count, new_count, notes, action in summary:
            total_old += old_count
            total_new += new_count
            if action == "DELETE":
                deleted_sessions += 1
            print(
                f"{action:9} session={session_id:>3}  sets {old_count:>2} -> {new_count:>2}  {notes}"
            )

        print()
        print(f"Sessions scanned: {len(summary)}")
        print(f"Sessions deleted: {deleted_sessions}")
        print(f"Existing sets: {total_old}")
        print(f"Planned sets:  {total_new}")

        if not args.apply:
            print("Dry run only. Re-run with --apply to rebuild workout_sets.")
            return 0

        deleted, inserted, deleted_sessions = apply(
            conn,
            args.min_id,
            args.max_id,
            args.delete_session_ids,
        )
        print(
            f"Deleted {deleted} sets, inserted {inserted} sets, and removed "
            f"{deleted_sessions} sessions in {db_path}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
