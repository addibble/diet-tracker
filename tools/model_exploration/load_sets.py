"""Build a per-set DataFrame for 3D-manifold exploration.

Filters to external-weight exercises with ample RPE data. Produces per-set
accumulated tissue-volume features plus the inverted M_obs from the universal
fresh curve.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DB = ROOT / "prod_dbs_2026-04-19" / \
    "3d91e0e958b64d8eae86fdde4ff72783" / \
    "3d91e0e958b64d8eae86fdde4ff72783_2026-04-19_224613.db"

# Universal curve constants (same as production bootstrap).
K = 20.0
GAMMA = 0.9
RTF_CAP = 30.0

# RPE quality floor — only use sets with at least this RPE (0-10 RIR scale).
MIN_RPE = 6.0

# External-weight only. Bodyweight / partial-bodyweight modes excluded per
# user instruction (suspect data quality).
EXTERNAL_LOAD_MODES = {"external_weight", "external_load"}


def load_raw(db_path: Path) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    try:
        tables = {
            name: pd.read_sql_query(f"SELECT * FROM {name}", con)
            for name in (
                "workout_sets", "workout_sessions", "exercises",
                "exercise_tissues", "tissues",
            )
        }
    finally:
        con.close()
    return tables


def build_sets_df(db_path: Path) -> pd.DataFrame:
    t = load_raw(db_path)
    ws = t["workout_sets"]
    ses = t["workout_sessions"][["id", "date"]].rename(
        columns={"id": "session_id"},
    )
    ex = t["exercises"][[
        "id", "name", "load_input_mode", "bodyweight_fraction",
        "allow_heavy_loading",
    ]].rename(columns={"id": "exercise_id"})

    df = ws.merge(ses, on="session_id").merge(ex, on="exercise_id")

    # Quality filters.
    df = df[
        df["rpe"].notna()
        & (df["reps"] > 0)
        & df["weight"].notna()
        & (df["weight"] > 0)
        & (df["rpe"] >= MIN_RPE)
        & (df["rpe"] <= 10.0)
        & df["load_input_mode"].isin(EXTERNAL_LOAD_MODES)
    ].copy()

    df["rir"] = 10.0 - df["rpe"]
    df["rtf"] = df["reps"] + df["rir"]
    df["rtf"] = df["rtf"].clip(upper=RTF_CAP)
    df["tonnage"] = df["weight"] * df["reps"]

    # Observed M from the universal curve: rtf = k·((M/W)−1)^γ  ⇒
    #   M = W · (1 + (rtf/k)^(1/γ))
    df["m_obs"] = df["weight"] * (1.0 + (df["rtf"] / K) ** (1.0 / GAMMA))

    # Set-order timestamp: use completed_at if present, else synthesize from
    # session date + set_order so accumulation order is deterministic.
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "session_id", "set_order", "id"])
    return df.reset_index(drop=True)


def build_tissue_weights(db_path: Path) -> pd.DataFrame:
    """Per (exercise_id, tissue_id) volume-weight used for V accumulation.

    The production "volume by region" calculation uses ``loading_factor`` for
    volume attribution (fatigue_factor is for fatigue-recovery tissue modeling,
    not volume). We use loading_factor here for the tissue-dose kernel.
    """
    t = load_raw(db_path)
    et = t["exercise_tissues"][[
        "exercise_id", "tissue_id", "role", "loading_factor",
    ]].copy()
    et["loading_factor"] = et["loading_factor"].fillna(0.0)
    # Exclude zero-weight mappings.
    et = et[et["loading_factor"] > 0.0]
    return et


def accumulate_session_v(
    sets_df: pd.DataFrame, tissue_weights: pd.DataFrame,
) -> pd.DataFrame:
    """Compute V_eff at the start of each set.

    V_t(session, just before set s) = Σ_{prior sets p in same session}
        tissue_weight[p.exercise, t] · W_p · reps_p

    V_eff(s) = Σ_t tissue_weight[s.exercise, t] · V_t
    """
    # Build lookup: exercise_id → {tissue_id → weight}.
    by_ex: dict[int, dict[int, float]] = {}
    for ex_id, grp in tissue_weights.groupby("exercise_id"):
        by_ex[int(ex_id)] = dict(
            zip(grp["tissue_id"].astype(int), grp["loading_factor"].astype(float),
                strict=True)
        )

    v_eff = np.zeros(len(sets_df))
    # tonnage per-tissue contribution for THIS set (used for the next set's V)
    per_set_tissue_ton: list[dict[int, float]] = [dict() for _ in range(len(sets_df))]

    # Walk sets in chronological order, grouped by session.
    tissue_state: dict[int, float] = {}
    prev_session: int | None = None
    for idx, row in sets_df.iterrows():
        session = int(row["session_id"])
        if session != prev_session:
            tissue_state = {}
            prev_session = session

        ex_id = int(row["exercise_id"])
        ex_weights = by_ex.get(ex_id, {})

        # V_eff for THIS set = Σ_t ex_weights[t] · tissue_state[t]
        v = 0.0
        for tid, w in ex_weights.items():
            v += w * tissue_state.get(tid, 0.0)
        v_eff[idx] = v

        # Then add this set's tonnage to tissue_state for future sets.
        ton = float(row["tonnage"])
        for tid, w in ex_weights.items():
            tissue_state[tid] = tissue_state.get(tid, 0.0) + w * ton
            per_set_tissue_ton[idx][tid] = w * ton

    sets_df = sets_df.copy()
    sets_df["v_eff"] = v_eff
    # Also carry set-index within exercise within session for β-table baseline.
    sets_df["set_idx_in_session_ex"] = (
        sets_df.groupby(["session_id", "exercise_id"]).cumcount() + 1
    )
    return sets_df


@dataclass
class ExerciseSummary:
    exercise_id: int
    name: str
    n_sets: int
    n_sessions: int
    median_rtf: float
    median_v_eff: float


def summarize_exercises(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    g = df.groupby(["exercise_id", "name"], as_index=False).agg(
        n_sets=("id", "count"),
        n_sessions=("session_id", "nunique"),
        median_rtf=("rtf", "median"),
        median_w=("weight", "median"),
        median_v_eff=("v_eff", "median"),
        frac_first_set=(
            "set_idx_in_session_ex",
            lambda s: (s == 1).mean(),
        ),
    )
    g = g.sort_values("n_sets", ascending=False).head(top_n)
    return g.reset_index(drop=True)


def main() -> None:
    print(f"Loading {DB}")
    df = build_sets_df(DB)
    weights = build_tissue_weights(DB)
    df = accumulate_session_v(df, weights)

    print(f"\n== Filtered dataset ==")
    print(f"  sets: {len(df)}")
    print(f"  exercises: {df['exercise_id'].nunique()}")
    print(f"  sessions: {df['session_id'].nunique()}")
    print(f"  date range: {df['date'].min().date()} -> {df['date'].max().date()}")

    print(f"\n== Top exercises by RPE sets ==")
    summary = summarize_exercises(df, top_n=20)
    pd.set_option("display.width", 140)
    pd.set_option("display.max_columns", 20)
    print(summary.to_string(index=False))

    print(f"\n== V_eff distribution ==")
    print(df["v_eff"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]))
    zero_frac = (df["v_eff"] == 0).mean()
    print(f"  fraction of sets with V_eff == 0 (first-in-session): {zero_frac:.2%}")

    print(f"\n== M_obs distribution ==")
    print(df["m_obs"].describe(percentiles=[0.1, 0.5, 0.9]))

    # Persist for downstream scripts.
    out = ROOT / "prod_sets_drew.parquet"
    try:
        df.to_parquet(out)
        print(f"\nSaved: {out}")
    except Exception as e:  # noqa: BLE001
        # Fallback if pyarrow not installed: use pickle.
        alt = ROOT / "prod_sets_drew.pkl"
        df.to_pickle(alt)
        print(f"\nparquet unavailable ({e!r}); saved pickle: {alt}")

    # Exit non-zero if the dataset is suspiciously thin so CI-style flows notice.
    if len(df) < 100:
        print("WARNING: fewer than 100 sets after filtering", file=sys.stderr)


if __name__ == "__main__":
    main()
