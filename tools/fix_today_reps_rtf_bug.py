"""One-shot correction for the rtf-vs-reps_done logging bug.

Background
----------
After the rep/weight-space refactor, the frontend briefly stored
reps-to-failure (reps_done + per-set scheme RIR) instead of reps_done for
curve-mode exercises. Bootstrap/Tier-3 (flat-dashed-line) exercises were
unaffected.

For each affected WorkoutSet on a target date, the correction is:

    reps_corrected = reps_stored - scheme_rir_for_set_order

where scheme_rir_for_set_order is 3 for set_order=1, 2 for set_order=2,
1 for set_order=3. Sets with set_order >= 4 are left alone (they do not
match the three-set scheme pattern).

"Curve-mode" detection (conservative):
  - exercise.load_input_mode is NOT in {bodyweight, assisted_bodyweight}
  - exercise has >= 6 sets with an RPE recorded **before** the target date

Usage
-----
Dry-run (default) on the user DB:

    python tools/fix_today_reps_rtf_bug.py /path/to/user.db

Apply:

    python tools/fix_today_reps_rtf_bug.py /path/to/user.db --apply

Target a different date (default: today, local server TZ):

    python tools/fix_today_reps_rtf_bug.py /path/to/user.db --date 2026-04-22

A timestamped `.bak` of the DB is written before --apply writes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

SCHEME_RIR_BY_SET = {1: 3, 2: 2, 3: 1}
MIN_SETS_CURVE = 6
BODYWEIGHT_MODES = {"bodyweight", "assisted_bodyweight"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("db", type=Path, help="path to user's diet_tracker.db")
    p.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: today)")
    p.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"ERROR: {args.db} does not exist", file=sys.stderr)
        return 2
    target_date = args.date or dt.date.today().isoformat()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    # 1. Today's sets joined to session date and exercise metadata.
    rows = conn.execute(
        """
        SELECT ws.id AS set_id,
               ws.session_id,
               ws.exercise_id,
               ws.set_order,
               ws.reps,
               ws.rpe,
               e.name AS exercise_name,
               e.load_input_mode AS load_mode,
               s.date AS session_date
        FROM workout_sets ws
        JOIN workout_sessions s ON s.id = ws.session_id
        JOIN exercises e ON e.id = ws.exercise_id
        WHERE s.date = ?
          AND ws.reps IS NOT NULL
          AND ws.set_order IN (1, 2, 3)
        ORDER BY ws.session_id, ws.exercise_id, ws.set_order
        """,
        (target_date,),
    ).fetchall()

    if not rows:
        print(f"No candidate sets on {target_date}.")
        return 0

    # 2. Pre-date RPE-count per exercise (to decide curve vs bootstrap).
    rpe_counts: dict[int, int] = {}
    ex_ids = {r["exercise_id"] for r in rows}
    for ex_id in ex_ids:
        n = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM workout_sets ws
            JOIN workout_sessions s ON s.id = ws.session_id
            WHERE ws.exercise_id = ?
              AND ws.rpe IS NOT NULL
              AND s.date < ?
            """,
            (ex_id, target_date),
        ).fetchone()
        rpe_counts[ex_id] = int(n["n"])

    # 3. Classify and plan corrections.
    corrections: list[tuple[int, int, int, str, int]] = []
    skipped_bootstrap: list[str] = []
    for r in rows:
        is_bw = (r["load_mode"] or "external_weight") in BODYWEIGHT_MODES
        has_curve = (not is_bw) and rpe_counts[r["exercise_id"]] >= MIN_SETS_CURVE
        if not has_curve:
            skipped_bootstrap.append(f"  skip bootstrap: set={r['set_id']} ex={r['exercise_name']!r} order={r['set_order']}")
            continue
        delta = SCHEME_RIR_BY_SET[r["set_order"]]
        new_reps = int(r["reps"]) - delta
        if new_reps < 1:
            print(
                f"  WARN: set={r['set_id']} {r['exercise_name']!r} order={r['set_order']} "
                f"reps={r['reps']} → {new_reps} clamped to 1",
                file=sys.stderr,
            )
            new_reps = 1
        corrections.append(
            (r["set_id"], int(r["reps"]), new_reps, r["exercise_name"], r["set_order"])
        )

    print(f"Target date: {target_date}")
    print(f"Candidate sets: {len(rows)} (set_order 1-3 on that date)")
    print(f"Bootstrap/Tier-3 skipped: {len(skipped_bootstrap)}")
    if skipped_bootstrap:
        for line in skipped_bootstrap:
            print(line)
    print(f"Curve-mode corrections: {len(corrections)}")
    for set_id, old, new, name, order in corrections:
        print(f"  set={set_id:<6} order={order} {name!r:<45} reps {old} → {new}")

    if not corrections:
        return 0

    if not args.apply:
        print("\nDry-run; re-run with --apply to commit.")
        return 0

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.db.with_suffix(args.db.suffix + f".bak.{ts}")
    shutil.copy2(args.db, backup)
    print(f"Backup written to: {backup}")

    cur = conn.cursor()
    try:
        for set_id, _old, new_reps, _name, _order in corrections:
            cur.execute("UPDATE workout_sets SET reps = ? WHERE id = ?", (new_reps, set_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"Applied {len(corrections)} updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
