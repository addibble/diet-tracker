"""One-shot correction for the rtf-vs-reps_done logging bug.

Background
----------
After the rep/weight-space refactor, the frontend briefly stored
reps-to-failure (reps_done + per-set scheme RIR) instead of reps_done for
curve-mode exercises. Bootstrap/Tier-3 (flat-dashed-line) exercises were
unaffected.

For each affected WorkoutSet on a target date, the correction is:

    reps_corrected = reps_stored - scheme_rir_for_within_exercise_index

where scheme_rir_for_within_exercise_index is 3/2/1 for the 1st/2nd/3rd
set of the exercise in that session. Extra sets beyond 3 (set_order 4+
within the exercise) are left alone — the bug only shifted the three
scheme-driven sets.

Note that `workout_sets.set_order` is the GLOBAL order within a whole
workout session (not per-exercise), so we have to compute the per-
exercise index ourselves, grouping by (session_id, exercise_id) and
sorting by set_order ascending.

"Curve-mode" detection matches the backend's strength model:
  - exercise.load_input_mode is NOT in {bodyweight, assisted_bodyweight}
  - >= MIN_SETS_TIER2 (=3) observations exist with weight+reps+
    (rpe OR rep_completion) across >= 2 distinct prior sessions, counting
    sets from sessions strictly before the target date.

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
from collections import defaultdict
from pathlib import Path

SCHEME_RIR_BY_INDEX = {1: 3, 2: 2, 3: 1}
MIN_SETS_TIER2 = 3  # matches backend/app/strength_model.py
MIN_SESSIONS_TIER2 = 2
BODYWEIGHT_MODES = {"bodyweight", "assisted_bodyweight"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db", type=Path, help="path to user's diet_tracker.db")
    p.add_argument("--date", default=None, help="target date YYYY-MM-DD (default: today)")
    p.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    return p.parse_args()


def has_curve_eligible_history(conn: sqlite3.Connection, exercise_id: int, before_date: str) -> bool:
    """Mirror backend/app/strength_model.py: curve fit requires >= 3 obs
    from >= 2 distinct sessions, with weight+reps+(rpe OR rep_completion)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n_obs, COUNT(DISTINCT s.id) AS n_sess
        FROM workout_sets ws
        JOIN workout_sessions s ON s.id = ws.session_id
        WHERE ws.exercise_id = ?
          AND s.date < ?
          AND ws.reps IS NOT NULL
          AND ws.weight IS NOT NULL
          AND (ws.rpe IS NOT NULL OR ws.rep_completion IS NOT NULL)
        """,
        (exercise_id, before_date),
    ).fetchone()
    return row["n_obs"] >= MIN_SETS_TIER2 and row["n_sess"] >= MIN_SESSIONS_TIER2


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        print(f"ERROR: {args.db} does not exist", file=sys.stderr)
        return 2
    target_date = args.date or dt.date.today().isoformat()

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    # All reps-bearing sets on target date, ordered so we can assign a
    # within-exercise index per (session, exercise).
    rows = conn.execute(
        """
        SELECT ws.id AS set_id,
               ws.session_id,
               ws.exercise_id,
               ws.set_order,
               ws.reps,
               ws.rpe,
               ws.rep_completion,
               e.name AS exercise_name,
               e.load_input_mode AS load_mode
        FROM workout_sets ws
        JOIN workout_sessions s ON s.id = ws.session_id
        JOIN exercises e ON e.id = ws.exercise_id
        WHERE s.date = ?
          AND ws.reps IS NOT NULL
        ORDER BY ws.session_id, ws.exercise_id, ws.set_order
        """,
        (target_date,),
    ).fetchall()

    if not rows:
        print(f"No sets found on {target_date}.")
        return 0

    # Assign per-exercise index and classify curve/bootstrap.
    groups: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        groups[(r["session_id"], r["exercise_id"])].append(r)

    corrections: list[tuple[int, int, int, str, int]] = []
    skipped_bootstrap: list[str] = []
    skipped_extra_set: list[str] = []

    for (sess_id, ex_id), group in groups.items():
        ex_name = group[0]["exercise_name"]
        load_mode = group[0]["load_mode"] or "external_weight"
        is_bw = load_mode in BODYWEIGHT_MODES
        curve_eligible = (not is_bw) and has_curve_eligible_history(conn, ex_id, target_date)

        for within_idx, r in enumerate(group, start=1):
            if not curve_eligible:
                skipped_bootstrap.append(
                    f"  skip bootstrap: set={r['set_id']} ex={ex_name!r} pos={within_idx}"
                )
                continue
            if within_idx not in SCHEME_RIR_BY_INDEX:
                skipped_extra_set.append(
                    f"  skip extra:     set={r['set_id']} ex={ex_name!r} pos={within_idx}"
                )
                continue
            delta = SCHEME_RIR_BY_INDEX[within_idx]
            old_reps = int(r["reps"])
            new_reps = max(1, old_reps - delta)
            if old_reps - delta < 1:
                print(
                    f"  WARN: set={r['set_id']} {ex_name!r} pos={within_idx} "
                    f"reps={old_reps} → {old_reps - delta} clamped to 1",
                    file=sys.stderr,
                )
            corrections.append((r["set_id"], old_reps, new_reps, ex_name, within_idx))

    print(f"Target date: {target_date}")
    print(f"Candidate sets: {len(rows)}")
    print(f"Exercise-groups: {len(groups)}")
    print(f"Bootstrap/Tier-3 skipped: {len(skipped_bootstrap)}")
    for line in skipped_bootstrap:
        print(line)
    print(f"Extra sets (position > 3) skipped: {len(skipped_extra_set)}")
    for line in skipped_extra_set:
        print(line)
    print(f"Curve-mode corrections: {len(corrections)}")
    for set_id, old, new, name, pos in corrections:
        print(f"  set={set_id:<6} pos={pos} {name[:40]!r:<42} reps {old} → {new}")

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
        for set_id, _old, new_reps, _name, _pos in corrections:
            cur.execute("UPDATE workout_sets SET reps = ? WHERE id = ?", (new_reps, set_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"Applied {len(corrections)} updates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
