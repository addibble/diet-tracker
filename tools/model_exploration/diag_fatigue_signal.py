"""Diagnostics: is there a fatigue signal at all in Drew's data?

1. Within-(session, exercise) set-to-set rtf changes — do later sets drop?
2. Within-session cross-exercise V_eff vs M_obs residual (after per-exercise mean)
3. Per-exercise RTF variance as a sanity check for RPE noise floor.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
K = 20.0
GAMMA = 0.9


def main() -> None:
    df = pd.read_parquet(ROOT / "prod_sets_drew.parquet")

    # 1) Within (session, exercise) rtf decay.
    print("== Within-(session, exercise) rtf by set index ==")
    # Only groups with >= 2 sets.
    grouped = df.groupby(["session_id", "exercise_id"])
    multi = df[grouped["id"].transform("count") >= 2].copy()
    agg = multi.groupby("set_idx_in_session_ex").agg(
        n=("rtf", "size"),
        rtf_mean=("rtf", "mean"),
        rtf_std=("rtf", "std"),
        reps_mean=("reps", "mean"),
        w_mean=("weight", "mean"),
    )
    print(agg.to_string())

    # Subtract session-exercise mean so we isolate within-unit decay.
    multi["rtf_demeaned"] = multi["rtf"] - multi.groupby(
        ["session_id", "exercise_id"],
    )["rtf"].transform("mean")
    print("\n== Within-(session, exercise) demeaned rtf by set index ==")
    demean = multi.groupby("set_idx_in_session_ex")["rtf_demeaned"].agg(
        ["count", "mean", "std"],
    )
    print(demean.to_string())

    # 2) Cross-exercise tissue-volume signal: use sessions where Drew did
    #    multiple DIFFERENT exercises hitting overlapping tissues, and check
    #    whether later exercises show higher M_obs residuals.
    print("\n== Log-M residual (after per-exercise mean) vs V_eff ==")
    df["log_m"] = np.log(df["m_obs"])
    df["log_m_resid"] = df["log_m"] - df.groupby("exercise_id")["log_m"].transform(
        "mean",
    )
    bins = pd.qcut(df["v_eff"], q=6, duplicates="drop")
    bin_stats = df.groupby(bins, observed=True).agg(
        n=("log_m_resid", "size"),
        resid_mean=("log_m_resid", "mean"),
        resid_std=("log_m_resid", "std"),
        v_mean=("v_eff", "mean"),
    )
    print(bin_stats.to_string())

    # 3) Correlation of V_eff with demeaned log-M, overall and within-exercise.
    overall_r = df[["v_eff", "log_m_resid"]].corr().iloc[0, 1]
    print(f"\n  Overall Pearson r(V_eff, log_M_resid) = {overall_r:.4f}")
    # Per-exercise correlations for the top-15 exercises.
    top = df["exercise_id"].value_counts().head(15).index
    print("  Per-exercise Pearson r(V_eff, log_M_resid):")
    for e in top:
        sub = df[df["exercise_id"] == e]
        if len(sub) < 8:
            continue
        r = sub[["v_eff", "log_m_resid"]].corr().iloc[0, 1]
        name = sub["name"].iloc[0][:30]
        print(f"    {e:4d} {name:30s} n={len(sub):3d}  r={r:+.3f}")

    # 4) Noise floor: within (session, exercise, weight) rep variance —
    #    when Drew does two sets at SAME weight in one session, how much
    #    does rtf vary? That's the RPE noise floor.
    same_w = df.copy()
    same_w["w_rounded"] = same_w["weight"].round(0)
    g = same_w.groupby(["session_id", "exercise_id", "w_rounded"])
    pairs = g.filter(lambda x: len(x) >= 2)
    pairs["rtf_demeaned"] = pairs["rtf"] - pairs.groupby(
        ["session_id", "exercise_id", "w_rounded"],
    )["rtf"].transform("mean")
    noise_floor = float(pairs["rtf_demeaned"].std())
    print(f"\n  Noise floor (σ of rtf within same session/exercise/weight)"
          f" = {noise_floor:.3f} reps  n={len(pairs)}")


if __name__ == "__main__":
    main()
