"""Per-set feature engineering for set-1 freshness and set-2/3 fatigue studies.

Features computed per (session, exercise, set):
  - days_since_any_session (wall days since last logged session, before this one)
  - days_since_same_exercise
  - days_since_same_primary_tissue (min over primary tissues of this exercise)
  - days_since_same_group (Push/Pull/Legs/Shoulders/Core via tissue-region heuristic)
  - acute_load_7d: total effective volume (weight*reps, RPE agnostic) in the
    rolling 7-day window ENDING the day BEFORE this session, restricted to
    exercises that load ≥1 primary tissue of this exercise.
  - prior_session_volume: total effective_set_load from earlier exercises in
    THIS session (not including the current exercise's earlier sets).
  - prior_exercise_volume: effective_set_load from earlier sets of THIS exercise
    within this session.
  - prior_dose: AR(1) accumulated dose from earlier sets (shared within session).
  - isolation_flag: 1 if exercise has exactly 1 primary tissue (heuristic).

Also produces Set-1 residuals and per-set residuals w.r.t. a
LEAKAGE-FREE fresh curve (fit only from sets strictly before the session's
date).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import date as dtdate, timedelta
from pathlib import Path

import numpy as np

from data_loader import (
    DB_PATH,
    SetRecord,
    effective_weight,
    get_connection,
    load_all_sets,
    load_bodyweight_history,
    nearest_bodyweight,
)


# Map tissue.region -> coarse group. Matches backend/app/exercise_groups.py
# centroids approximately; used only as a feature, not as ground truth.
REGION_TO_GROUP = {
    "chest": "Push",
    "shoulders": "Push",
    "triceps": "Push",
    "neck": "Push",
    "upper_back": "Pull",
    "lower_back": "Pull",
    "biceps": "Pull",
    "forearms": "Pull",
    "quads": "Legs",
    "hamstrings": "Legs",
    "glutes": "Legs",
    "calves": "Legs",
    "inner_leg_adductor": "Legs",
    "outer_leg_abductor": "Legs",
    "shins": "Legs",
    "core": "Core",
}


@dataclass
class ExerciseTissueMap:
    """Primary and secondary tissue info for one exercise."""
    exercise_id: int
    primary_tissue_ids: set[int]
    secondary_tissue_ids: set[int]
    loading_factors: dict[int, float]  # tissue_id -> factor
    primary_group: str | None
    n_primary: int


def load_exercise_tissue_map(conn: sqlite3.Connection) -> dict[int, ExerciseTissueMap]:
    """Build per-exercise tissue and group info."""
    tissue_region = {
        r["id"]: r["region"]
        for r in conn.execute("SELECT id, region FROM tissues").fetchall()
    }
    rows = conn.execute(
        "SELECT exercise_id, tissue_id, role, loading_factor FROM exercise_tissues"
    ).fetchall()
    primary: dict[int, set[int]] = defaultdict(set)
    secondary: dict[int, set[int]] = defaultdict(set)
    lf: dict[int, dict[int, float]] = defaultdict(dict)
    for r in rows:
        ex_id = r["exercise_id"]
        tid = r["tissue_id"]
        lf[ex_id][tid] = r["loading_factor"]
        if r["role"] == "primary":
            primary[ex_id].add(tid)
        elif r["role"] == "secondary":
            secondary[ex_id].add(tid)
    result: dict[int, ExerciseTissueMap] = {}
    all_ex = set(primary) | set(secondary) | set(lf)
    for ex_id in all_ex:
        # Group: majority vote over primary tissues' regions
        groups = [REGION_TO_GROUP.get(tissue_region.get(t)) for t in primary.get(ex_id, set())]
        groups = [g for g in groups if g is not None]
        if groups:
            primary_group = max(set(groups), key=groups.count)
        else:
            primary_group = None
        result[ex_id] = ExerciseTissueMap(
            exercise_id=ex_id,
            primary_tissue_ids=primary.get(ex_id, set()),
            secondary_tissue_ids=secondary.get(ex_id, set()),
            loading_factors=lf.get(ex_id, {}),
            primary_group=primary_group,
            n_primary=len(primary.get(ex_id, set())),
        )
    return result


@dataclass
class SetFeatures:
    """Features for one set (primarily RPE-tagged sets, but emitted for all)."""
    session_id: int
    session_date: str
    exercise_id: int
    exercise_name: str
    set_order: int
    reps: int
    effective_weight: float
    rpe: float | None
    rtf_actual: float | None  # reps + (10 - rpe) if rpe else None

    # Freshness features (computed at session level, identical for all sets
    # within the same (session, exercise)):
    days_since_any_session: float | None
    days_since_same_exercise: float | None
    days_since_same_primary_tissue: float | None
    days_since_same_group: float | None
    acute_load_7d_same_tissue: float  # effective volume on same primary tissues in 7d before session

    # Within-session features (vary across sets / exercises within a session):
    prior_session_volume: float   # effective volume from OTHER exercises earlier this session
    prior_exercise_volume: float  # effective volume from earlier sets of THIS exercise
    prior_dose_ar1: float         # AR(1) accumulated dose from earlier sets (this exercise only)
    exercise_order_in_session: int  # 1-indexed order this exercise appears in session
    set_index_in_exercise: int  # 1-indexed order of this set within (session, exercise)

    # Exercise properties
    n_primary_tissues: int
    isolation_flag: int
    primary_group: str | None


def _iso_to_date(s: str) -> dtdate:
    return dtdate.fromisoformat(s[:10])


def _days_between(a: str, b: str) -> int:
    """Days from a to b (positive if b is later)."""
    return (_iso_to_date(b) - _iso_to_date(a)).days


def compute_features_for_rpe_sets(
    db_path: Path | None = None,
    alpha: float = 1.2,
    beta: float = 1.0,
    lambda_rir: float = 0.2,
    eta: float = 0.85,
    c_dose: float = 1e-4,
) -> list[SetFeatures]:
    """Compute features for every RPE-tagged set.

    Does a single in-memory pass over all sets (sorted by date+order), then
    replays history to compute freshness and within-session accumulators.
    """
    conn = get_connection(db_path)
    all_sets = load_all_sets(conn)
    bw_history = load_bodyweight_history(conn)
    tmap = load_exercise_tissue_map(conn)
    conn.close()

    # Pre-compute effective weights
    set_ew: dict[int, float | None] = {}
    for s in all_sets:
        bw = nearest_bodyweight(bw_history, s.session_date)
        set_ew[s.set_id] = effective_weight(s, bw)

    # Group sets by session, in date+session_order
    sessions_by_date: dict[str, list[int]] = defaultdict(list)
    sets_by_session: dict[int, list[SetRecord]] = defaultdict(list)
    for s in all_sets:
        sets_by_session[s.session_id].append(s)
    for sid, slist in sets_by_session.items():
        slist.sort(key=lambda x: x.set_order)
        sessions_by_date[slist[0].session_date].append(sid)
    sorted_dates = sorted(sessions_by_date.keys())

    # Tracking history (updated as we iterate through sessions chronologically):
    last_session_date: str | None = None  # date of last ANY session
    last_ex_date: dict[int, str] = {}       # exercise_id -> last date
    last_tissue_date: dict[int, str] = {}    # tissue_id -> last date
    last_group_date: dict[str, str] = {}     # group -> last date
    tissue_volume_log: dict[int, list[tuple[str, float]]] = defaultdict(list)
    # ^ tissue_id -> list of (date, effective_volume) entries; used for 7d acute.

    out: list[SetFeatures] = []

    for day in sorted_dates:
        # One day may have multiple sessions; process each independently
        for sid in sessions_by_date[day]:
            sets = sets_by_session[sid]

            # Freshness at the START of this session (i.e., BEFORE we log anything)
            days_any = _days_between(last_session_date, day) if last_session_date else None

            # Order exercises by first set_order seen in this session
            ex_first_order: dict[int, int] = {}
            for s in sets:
                if s.exercise_id not in ex_first_order:
                    ex_first_order[s.exercise_id] = s.set_order
            ex_order_sorted = sorted(ex_first_order, key=lambda x: ex_first_order[x])
            ex_order_idx = {e: i + 1 for i, e in enumerate(ex_order_sorted)}

            # Per-exercise session trackers (built as we iterate)
            session_cum_volume_excl: dict[int, float] = {}  # cumulative volume from OTHER exercises, as of first appearance of THIS ex
            cum_session_volume = 0.0
            per_ex_cum_volume: dict[int, float] = defaultdict(float)
            per_ex_cum_dose: dict[int, float] = defaultdict(float)

            # For each exercise in order, snapshot volume of OTHER exercises before it first
            # appears. (Best-effort: approximate via exercise ordering.)
            # We iterate sets in set_order; track the "first-appearance volume" for each ex.
            for s in sets:
                ex_id = s.exercise_id
                ew = set_ew.get(s.set_id)
                if ew is None:
                    continue
                if ex_id not in session_cum_volume_excl:
                    session_cum_volume_excl[ex_id] = cum_session_volume

                reps = s.reps or 0
                set_vol = ew * reps
                cum_session_volume += set_vol

            # Second pass to emit features for RPE sets in stable order
            per_ex_cum_volume.clear()
            per_ex_cum_dose.clear()
            per_ex_set_counter: dict[int, int] = defaultdict(int)
            for s in sets:
                ex_id = s.exercise_id
                ew = set_ew.get(s.set_id)
                if ew is None or s.reps is None or s.reps <= 0:
                    continue
                reps = s.reps
                set_vol = ew * reps

                # RPE check
                has_rpe = s.rpe is not None and 5.0 <= s.rpe <= 10.0

                emap = tmap.get(ex_id)
                primary_ids = emap.primary_tissue_ids if emap else set()
                group = emap.primary_group if emap else None
                n_primary = emap.n_primary if emap else 0

                # Freshness features (snapshots at session start for session-level features,
                # but per-exercise for "days_since_same_exercise")
                d_same_ex = _days_between(last_ex_date[ex_id], day) if ex_id in last_ex_date else None
                d_same_tissue = None
                for tid in primary_ids:
                    if tid in last_tissue_date:
                        dd = _days_between(last_tissue_date[tid], day)
                        d_same_tissue = dd if d_same_tissue is None else min(d_same_tissue, dd)
                d_same_group = _days_between(last_group_date[group], day) if group and group in last_group_date else None

                # 7-day acute load on same primary tissues (strictly BEFORE `day`)
                cutoff_lo = (_iso_to_date(day) - timedelta(days=7)).isoformat()
                acute = 0.0
                for tid in primary_ids:
                    for (ddate, dvol) in tissue_volume_log.get(tid, []):
                        if cutoff_lo <= ddate < day:
                            acute += dvol

                if has_rpe:
                    rir = 10.0 - s.rpe
                    rtf = s.reps + rir
                    dose = (ew ** alpha) * (s.reps ** beta) * math.exp(-lambda_rir * rir)
                else:
                    rtf = None
                    # Default-RIR dose for fatigue carry-over from non-RPE sets
                    dose = (ew ** alpha) * (s.reps ** beta) * math.exp(-lambda_rir * 2.0)

                prior_sess_vol = session_cum_volume_excl.get(ex_id, 0.0)
                prior_ex_vol = per_ex_cum_volume[ex_id]
                prior_ex_dose = per_ex_cum_dose[ex_id]
                per_ex_set_counter[ex_id] += 1
                set_idx = per_ex_set_counter[ex_id]

                if has_rpe:
                    out.append(SetFeatures(
                        session_id=sid,
                        session_date=day,
                        exercise_id=ex_id,
                        exercise_name=s.exercise_name,
                        set_order=s.set_order,
                        reps=s.reps,
                        effective_weight=float(ew),
                        rpe=s.rpe,
                        rtf_actual=float(rtf) if rtf is not None else None,
                        days_since_any_session=float(days_any) if days_any is not None else None,
                        days_since_same_exercise=float(d_same_ex) if d_same_ex is not None else None,
                        days_since_same_primary_tissue=float(d_same_tissue) if d_same_tissue is not None else None,
                        days_since_same_group=float(d_same_group) if d_same_group is not None else None,
                        acute_load_7d_same_tissue=float(acute),
                        prior_session_volume=float(prior_sess_vol),
                        prior_exercise_volume=float(prior_ex_vol),
                        prior_dose_ar1=float(prior_ex_dose),
                        exercise_order_in_session=ex_order_idx.get(ex_id, 0),
                        set_index_in_exercise=set_idx,
                        n_primary_tissues=n_primary,
                        isolation_flag=1 if n_primary == 1 else 0,
                        primary_group=group,
                    ))

                # Update within-exercise running state
                per_ex_cum_volume[ex_id] += set_vol
                per_ex_cum_dose[ex_id] = eta * per_ex_cum_dose[ex_id] + c_dose * dose

            # After processing the session, update global history
            last_session_date = day
            for ex_id in ex_first_order:
                last_ex_date[ex_id] = day
                emap = tmap.get(ex_id)
                if emap:
                    for tid in emap.primary_tissue_ids:
                        last_tissue_date[tid] = day
                    if emap.primary_group:
                        last_group_date[emap.primary_group] = day
            # Log tissue volume for 7d acute lookups (keyed by tissue per exercise primary set)
            for s in sets:
                ex_id = s.exercise_id
                ew = set_ew.get(s.set_id)
                if ew is None or not s.reps:
                    continue
                emap = tmap.get(ex_id)
                if not emap:
                    continue
                vol = ew * s.reps
                for tid in emap.primary_tissue_ids:
                    tissue_volume_log[tid].append((day, vol))

    return out


if __name__ == "__main__":
    feats = compute_features_for_rpe_sets()
    print(f"Emitted {len(feats)} RPE-set feature rows")
    if feats:
        f = feats[0]
        print(f"First: {f.session_date} {f.exercise_name} set#{f.set_order} rtf={f.rtf_actual}")
        print(f"       fresh: any={f.days_since_any_session} ex={f.days_since_same_exercise} "
              f"tissue={f.days_since_same_primary_tissue} group={f.days_since_same_group}")
        # Quick summary
        set1 = [f for f in feats if f.set_index_in_exercise == 1]
        print(f"First-set-per-exercise count: {len(set1)}")
        have_any_freshness = sum(1 for f in set1 if f.days_since_any_session is not None)
        print(f"First-set with prior-session history: {have_any_freshness}")
        by_idx = defaultdict(int)
        for f in feats:
            by_idx[f.set_index_in_exercise] += 1
        print("Distribution by set_index_in_exercise:")
        for idx in sorted(by_idx):
            print(f"  set #{idx}: {by_idx[idx]}")
