#!/usr/bin/env python3
"""Repair incorrectly ordered workout session dates.

This script rewrites `workout_sessions.date` for a bounded ID range by using the
session notes (`Group X Round Y`) as the canonical workout ordering. The default
repair matches the import issue described for IDs 20-70:

- round-major ordering: Round 1 / Group 0, Round 1 / Group 1, ...
- start on 2025-11-29
- insert a break after Round 3 / Group 2
- resume on 2025-12-29

By default the script is a dry run and only prints the proposed changes. Pass
`--apply` to commit the updates.

Examples:
    python tools/repair_workout_session_dates.py
    python tools/repair_workout_session_dates.py --apply
    python tools/repair_workout_session_dates.py --db ./diet_tracker.db --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


NOTE_RE = re.compile(r"Group\s+(?P<group>\d+)\s+Round\s+(?P<round>\d+)$")
DEFAULT_DB = Path(__file__).resolve().parents[1] / "diet_tracker.db"


@dataclass(frozen=True)
class SessionRow:
    id: int
    old_date: str
    notes: str
    group_index: int
    round_index: int


@dataclass(frozen=True)
class PlannedUpdate:
    id: int
    old_date: str
    new_date: str
    notes: str
    group_index: int
    round_index: int


def parse_group_round(notes: str) -> tuple[int, int]:
    match = NOTE_RE.fullmatch(notes.strip())
    if not match:
        raise ValueError(f"Unsupported notes format: {notes!r}")
    return int(match["group"]), int(match["round"])


def load_rows(db_path: Path, min_id: int, max_id: int) -> list[SessionRow]:
    query = """
        SELECT id, date, COALESCE(notes, '')
        FROM workout_sessions
        WHERE id BETWEEN ? AND ?
        ORDER BY id
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, (min_id, max_id)).fetchall()

    result: list[SessionRow] = []
    for row_id, old_date, notes in rows:
        group_index, round_index = parse_group_round(notes)
        result.append(
            SessionRow(
                id=row_id,
                old_date=old_date,
                notes=notes,
                group_index=group_index,
                round_index=round_index,
            )
        )
    return result


def advance_date(
    current: dt.date,
    key: tuple[int, int],
    break_after: tuple[int, int],
    resume_date: dt.date,
    skip_dates: set[dt.date],
) -> dt.date:
    next_date = resume_date if key == break_after else current + dt.timedelta(days=1)
    while next_date in skip_dates:
        next_date += dt.timedelta(days=1)
    return next_date


def build_shared_date_plan(
    rows: list[SessionRow],
    start_date: dt.date,
    break_after: tuple[int, int],
    resume_date: dt.date,
    skip_dates: set[dt.date],
) -> list[PlannedUpdate]:
    ordered_keys = sorted({(row.round_index, row.group_index) for row in rows})
    if break_after not in ordered_keys:
        raise ValueError(
            f"Break marker Round {break_after[0]} / Group {break_after[1]} "
            "does not exist in the selected rows."
        )

    assigned_dates: dict[tuple[int, int], dt.date] = {}
    current_date = start_date
    for key in ordered_keys:
        assigned_dates[key] = current_date
        current_date = advance_date(
            current_date,
            key,
            break_after,
            resume_date,
            skip_dates,
        )

    return [
        PlannedUpdate(
            id=row.id,
            old_date=row.old_date,
            new_date=assigned_dates[(row.round_index, row.group_index)].isoformat(),
            notes=row.notes,
            group_index=row.group_index,
            round_index=row.round_index,
        )
        for row in sorted(rows, key=lambda row: row.id)
    ]


def build_sequential_plan(
    rows: list[SessionRow],
    start_date: dt.date,
    break_after: tuple[int, int],
    resume_date: dt.date,
    skip_dates: set[dt.date],
) -> list[PlannedUpdate]:
    ordered_rows = sorted(rows, key=lambda row: (row.round_index, row.group_index, row.id))
    if not any(
        row.round_index == break_after[0] and row.group_index == break_after[1]
        for row in ordered_rows
    ):
        raise ValueError(
            f"Break marker Round {break_after[0]} / Group {break_after[1]} "
            "does not exist in the selected rows."
        )

    planned_by_id: dict[int, PlannedUpdate] = {}
    current_date = start_date
    for row in ordered_rows:
        planned_by_id[row.id] = PlannedUpdate(
            id=row.id,
            old_date=row.old_date,
            new_date=current_date.isoformat(),
            notes=row.notes,
            group_index=row.group_index,
            round_index=row.round_index,
        )
        current_date = advance_date(
            current_date,
            (row.round_index, row.group_index),
            break_after,
            resume_date,
            skip_dates,
        )

    return [planned_by_id[row.id] for row in sorted(rows, key=lambda row: row.id)]


def build_plan(
    rows: list[SessionRow],
    start_date: dt.date,
    break_after: tuple[int, int],
    resume_date: dt.date,
    duplicate_mode: str,
    skip_dates: set[dt.date],
) -> list[PlannedUpdate]:
    if duplicate_mode == "shared-date":
        return build_shared_date_plan(
            rows,
            start_date,
            break_after,
            resume_date,
            skip_dates,
        )
    if duplicate_mode == "sequential":
        return build_sequential_plan(
            rows,
            start_date,
            break_after,
            resume_date,
            skip_dates,
        )
    raise ValueError(f"Unsupported duplicate mode: {duplicate_mode}")


def print_plan(plan: list[PlannedUpdate]) -> None:
    changed = 0
    for item in plan:
        marker = "UNCHANGED" if item.old_date == item.new_date else "UPDATE"
        if marker == "UPDATE":
            changed += 1
        print(
            f"{marker:8} id={item.id:>3}  {item.old_date} -> {item.new_date}  "
            f"{item.notes}"
        )
    print()
    print(f"Rows scanned: {len(plan)}")
    print(f"Rows changing: {changed}")


def apply_plan(db_path: Path, plan: list[PlannedUpdate]) -> int:
    updates = [
        (item.new_date, item.id)
        for item in plan
        if item.old_date != item.new_date
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany("UPDATE workout_sessions SET date = ? WHERE id = ?", updates)
        conn.commit()
    return len(updates)


def parse_break_marker(raw: str) -> tuple[int, int]:
    try:
        round_text, group_text = raw.split(":", maxsplit=1)
        return int(round_text), int(group_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Break marker must be in ROUND:GROUP form, for example 3:2."
        ) from exc


def parse_skip_dates(raw: str) -> set[dt.date]:
    if not raw.strip():
        return set()
    try:
        return {dt.date.fromisoformat(part.strip()) for part in raw.split(",") if part.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Skip dates must be a comma-separated list of YYYY-MM-DD values."
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to the SQLite database. Defaults to {DEFAULT_DB}.",
    )
    parser.add_argument("--min-id", type=int, default=20, help="First session ID to repair.")
    parser.add_argument("--max-id", type=int, default=70, help="Last session ID to repair.")
    parser.add_argument(
        "--start-date",
        type=dt.date.fromisoformat,
        default=dt.date(2025, 11, 29),
        help="First assigned workout date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--break-after",
        type=parse_break_marker,
        default=(3, 2),
        help="Pause after this ROUND:GROUP marker. Default: 3:2.",
    )
    parser.add_argument(
        "--resume-date",
        type=dt.date.fromisoformat,
        default=dt.date(2025, 12, 29),
        help="Date to assign immediately after the break in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--duplicate-mode",
        choices=("shared-date", "sequential"),
        default="shared-date",
        help=(
            "How to handle duplicate 'Group X Round Y' rows. "
            "'shared-date' gives duplicates the same repaired date; "
            "'sequential' assigns each row its own date."
        ),
    )
    parser.add_argument(
        "--skip-dates",
        type=parse_skip_dates,
        default=set(),
        help=(
            "Comma-separated YYYY-MM-DD dates to leave unused in the generated "
            "schedule, for example 2026-01-04,2026-01-11."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the updates. Without this flag the script only previews changes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()

    if not db_path.exists():
        print(f"Database file does not exist: {db_path}", file=sys.stderr)
        return 1

    rows = load_rows(db_path, args.min_id, args.max_id)
    if not rows:
        print("No workout_sessions rows found in the selected ID range.", file=sys.stderr)
        return 1

    if args.start_date in args.skip_dates:
        print("--start-date cannot also appear in --skip-dates.", file=sys.stderr)
        return 1

    plan = build_plan(
        rows=rows,
        start_date=args.start_date,
        break_after=args.break_after,
        resume_date=args.resume_date,
        duplicate_mode=args.duplicate_mode,
        skip_dates=args.skip_dates,
    )

    print_plan(plan)

    if not args.apply:
        print("Dry run only. Re-run with --apply to update workout_sessions.date.")
        return 0

    changed = apply_plan(db_path, plan)
    print(f"Applied {changed} date updates to {db_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
