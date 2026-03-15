#!/usr/bin/env python3
"""Apply manual schema/data updates and backfills to a SQLite database."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
DEFAULT_DB = ROOT / "diet_tracker.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite database file to update.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["DATABASE_URL"] = f"sqlite:///{args.db.resolve()}"
    sys.path.insert(0, str(BACKEND_ROOT))

    from app.database import apply_db_updates

    apply_db_updates()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
