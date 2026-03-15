#!/usr/bin/env python3
"""Run the full scripted workout-history reimport plus post-import fixups."""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
DEFAULT_DB = ROOT / "diet_tracker.db"
DEFAULT_AUG_SEP_CSV = Path.home() / "Downloads/weight lifting - August-September 2025.csv"

sys.path.insert(0, str(BACKEND_ROOT))

from app.reference_exercises import (  # noqa: E402
    REFERENCE_EXERCISE_FIXUPS,
    TISSUE_RECOVERY_HOURS_FIXUPS,
)


def build_commands(
    python: str,
    db_path: Path,
    aug_sep_csv: Path | None,
) -> tuple[list[list[str]], list[str]]:
    commands: list[list[str]] = []
    notes: list[str] = []

    if aug_sep_csv and aug_sep_csv.exists():
        commands.append(
            [
                python,
                str(ROOT / "tools/import_workout_csv.py"),
                str(aug_sep_csv),
                "--db",
                str(db_path),
                "--delete-before",
                "2026-03-08",
                "--create-missing-exercises",
            ]
        )
    else:
        notes.append(
            "Skipping August-September CSV; pass --aug-sep-csv to include it."
        )

    commands.extend(
        [
            [
                python,
                str(ROOT / "tools/import_workout_rounds_tsv.py"),
                str(ROOT / "tools/data/october_2025_rounds.tsv"),
                "--db",
                str(db_path),
                "--month",
                "10",
                "--off-days",
                "1",
                "--create-missing-exercises",
            ],
            [
                python,
                str(ROOT / "tools/import_workout_rounds_tsv.py"),
                str(ROOT / "tools/data/november_2025_rounds.tsv"),
                "--db",
                str(db_path),
                "--month",
                "11",
                "--off-days",
                "1",
                "--create-missing-exercises",
            ],
            [
                python,
                str(ROOT / "tools/import_winter_bulk_csv.py"),
                str(ROOT / "tools/data/winter_bulk_2025_2026.csv"),
                "--db",
                str(db_path),
                "--create-missing-exercises",
            ],
            [
                python,
                str(ROOT / "tools/import_february_injury_tsv.py"),
                str(ROOT / "tools/data/february_2026_shoulder_injury.tsv"),
                "--db",
                str(db_path),
                "--create-missing-exercises",
            ],
            [
                python,
                str(ROOT / "tools/import_winter_bulk_phase3_csv.py"),
                str(ROOT / "tools/data/winter_bulk_phase3_2026.csv"),
                "--db",
                str(db_path),
                "--create-missing-exercises",
            ],
            [
                python,
                str(ROOT / "tools/import_late_feb_march_tsv.py"),
                str(ROOT / "tools/data/late_feb_march_2026.tsv"),
                "--db",
                str(db_path),
                "--create-missing-exercises",
            ],
        ]
    )
    return commands, notes


def audit_post_import_fixups(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        has_tissue_recovery_logs = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'tissue_recovery_logs'"
        ).fetchone()
        matched_reference_exercises = conn.execute(
            "SELECT COUNT(*) "
            "FROM exercises "
            f"WHERE name IN ({','.join('?' for _ in REFERENCE_EXERCISE_FIXUPS)})",
            tuple(REFERENCE_EXERCISE_FIXUPS.keys()),
        ).fetchone()[0]
        matched_tissues = conn.execute(
            "SELECT COUNT(*) "
            "FROM tissues "
            f"WHERE name IN ({','.join('?' for _ in TISSUE_RECOVERY_HOURS_FIXUPS)})",
            tuple(TISSUE_RECOVERY_HOURS_FIXUPS.keys()),
        ).fetchone()[0]
        farmers_rows_missing_steps = conn.execute(
            "SELECT COUNT(*) "
            "FROM workout_sets "
            "WHERE distance_steps IS NULL "
            "  AND reps IS NOT NULL "
            "  AND exercise_id IN (SELECT id FROM exercises WHERE name = 'Farmers Carry')"
        ).fetchone()[0]
        earliest_bodyweight_training = conn.execute(
            "SELECT MIN(wses.date) "
            "FROM workout_sets ws "
            "JOIN workout_sessions wses ON wses.id = ws.session_id "
            "JOIN exercises e ON e.id = ws.exercise_id "
            "WHERE e.load_input_mode IN ('bodyweight', 'mixed', 'assisted_bodyweight') "
            "  AND COALESCE(e.bodyweight_fraction, 0) > 0"
        ).fetchone()[0]
        historical_weight_anchor_missing = 0
        if earliest_bodyweight_training is not None:
            historical_weight_anchor_missing = int(
                conn.execute(
                    "SELECT NOT EXISTS ("
                    "    SELECT 1 FROM weight_logs "
                    "    WHERE DATE(logged_at) <= ?"
                    ")",
                    (earliest_bodyweight_training,),
                ).fetchone()[0]
            )
        incomplete_rep_completion = conn.execute(
            "SELECT COUNT(*) "
            "FROM workout_sets ws "
            "JOIN exercises e ON e.id = ws.exercise_id "
            "WHERE ws.rep_completion IS NULL "
            "  AND ("
            "        ws.reps IS NOT NULL "
            "        OR ws.duration_secs IS NOT NULL "
            "        OR ws.distance_steps IS NOT NULL"
            "      ) "
            "  AND e.load_input_mode IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()

    print("Post-import audit:")
    print(f"  reference exercise fixes matched: {matched_reference_exercises}")
    print(f"  tissue recovery-hour fixes matched: {matched_tissues}")
    print(f"  Farmers Carry rows missing steps: {farmers_rows_missing_steps}")
    print(f"  historical bodyweight anchor missing: {historical_weight_anchor_missing}")
    print(f"  sets still missing rep_completion: {incomplete_rep_completion}")
    print(f"  tissue_recovery_logs table present: {bool(has_tissue_recovery_logs)}")


def run_post_import_fixups(python: str, db_path: Path) -> int:
    bootstrap = (
        "import sys; "
        f"sys.path.insert(0, {str(BACKEND_ROOT)!r}); "
        "from app.database import apply_db_updates; "
        "apply_db_updates()"
    )
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path.resolve()}"
    command = [python, "-c", bootstrap]
    print("$", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, env=env)
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reimport. Without this flag each step runs in audit mode.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite DB to import into and post-process.",
    )
    parser.add_argument(
        "--aug-sep-csv",
        type=Path,
        default=DEFAULT_AUG_SEP_CSV,
        help=(
            "Optional August-September CSV source. If the file is missing, the "
            "script skips that source."
        ),
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for the child importers.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands, notes = build_commands(args.python, args.db, args.aug_sep_csv)
    for note in notes:
        print(note)
    for command in commands:
        if args.apply:
            command.append("--apply")
        print("$", " ".join(command))
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            return result.returncode

    if args.apply:
        fixup_result = run_post_import_fixups(args.python, args.db)
        if fixup_result != 0:
            return fixup_result

    audit_post_import_fixups(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
