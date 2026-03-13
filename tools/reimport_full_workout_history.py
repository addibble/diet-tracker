#!/usr/bin/env python3
"""Run the full scripted workout-history reimport in one pass."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_commands(python: str) -> list[list[str]]:
    return [
        [
            python,
            str(ROOT / "tools/import_workout_csv.py"),
            str(Path.home() / "Downloads/weight lifting - August-September 2025.csv"),
            "--delete-before",
            "2026-03-08",
            "--create-missing-exercises",
        ],
        [
            python,
            str(ROOT / "tools/import_workout_rounds_tsv.py"),
            str(ROOT / "tools/data/october_2025_rounds.tsv"),
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
            "--create-missing-exercises",
        ],
        [
            python,
            str(ROOT / "tools/import_february_injury_tsv.py"),
            str(ROOT / "tools/data/february_2026_shoulder_injury.tsv"),
            "--create-missing-exercises",
        ],
        [
            python,
            str(ROOT / "tools/import_winter_bulk_phase3_csv.py"),
            str(ROOT / "tools/data/winter_bulk_phase3_2026.csv"),
            "--create-missing-exercises",
        ],
        [
            python,
            str(ROOT / "tools/import_late_feb_march_tsv.py"),
            str(ROOT / "tools/data/late_feb_march_2026.tsv"),
            "--create-missing-exercises",
        ],
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reimport. Without this flag each step runs in audit mode.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for the child importers.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands = build_commands(args.python)
    for command in commands:
        if args.apply:
            command.append("--apply")
        print("$", " ".join(command))
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
