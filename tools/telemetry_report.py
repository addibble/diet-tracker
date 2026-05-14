#!/usr/bin/env python3
"""Print a human-readable digest of recent telemetry data.

Usage:
    python tools/telemetry_report.py [--hours 24] [--db data/telemetry.db]

Runs read-only against ``data/telemetry.db`` and prints:

* Top endpoints by total time (n, avg, p95, max, avg DB queries, avg DB ms)
* Top slow SQL queries grouped by normalized text (with avg rowcount —
  high rowcount + slow + frequent is a textbook index-opportunity signal)
* Top frontend events by total time

This is the script the *post-workout review agent* should run before
proposing optimizations.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--db", type=str, default=None, help="Path to telemetry.db")
    args = ap.parse_args()

    if args.db:
        os.environ["TELEMETRY_DB_PATH"] = args.db

    # Defer imports so --help works without backend deps.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app import telemetry

    data = telemetry.summary(hours=args.hours)

    print(f"\n=== Telemetry summary (last {args.hours}h) ===\n")

    print("─── Top endpoints by total time ───")
    if not data["endpoints"]:
        print("  (no requests recorded)")
    for row in data["endpoints"]:
        path = row["path"][:60]
        print(
            f"  n={row['n']:>5}  avg={row['avg_ms']:>7.1f}ms  "
            f"p95={(row.get('p95_ms') or 0):>7.1f}ms  max={row['max_ms']:>7.1f}ms  "
            f"db={row['avg_db_count']:>4.1f}q / {row['avg_db_ms']:>6.1f}ms  "
            f"err={row['errors']:<3}  {path}"
        )

    print("\n─── Top slow SQL queries (candidates for indexing) ───")
    if not data["slow_queries"]:
        print("  (no slow queries above threshold)")
    for row in data["slow_queries"]:
        sql = row["sql"][:100].replace("\n", " ")
        print(
            f"  n={row['n']:>5}  avg={row['avg_ms']:>6.1f}ms  max={row['max_ms']:>6.1f}ms  "
            f"rows avg={row['avg_rows']:>6.1f} max={row['max_rows']:>6}  {sql}"
        )

    print("\n─── Top frontend events ───")
    if not data["frontend_events"]:
        print("  (no frontend events recorded)")
    for row in data["frontend_events"]:
        name = row["name"][:60]
        print(
            f"  n={row['n']:>5}  avg={row['avg_ms']:>7.1f}ms  max={row['max_ms']:>7.1f}ms  {name}"
        )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
