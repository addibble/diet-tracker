"""Refit kernels with per-(session, exercise) intercepts.

If alpha[e] alone misses across-session baseline drift, lambda has to fight it.
With alpha[s,e] (per session+exercise), lambda isolates pure within-session decay.

This is also closer to what the beta-table effectively does: it's a within-session
within-exercise correction relative to set 1.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch

from fit_manifold_v2 import build_problem, DB, reps_rmse

ROOT = Path(__file__).parent
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def fit_with_grouped_alpha(
    v_self: torch.Tensor,
    group_idx: torch.Tensor,    # (S,) group id per set (e.g., session*1e6 + exercise)
    log_m: torch.Tensor,
    rtf: torch.Tensor,
    weight: torch.Tensor,
    name: str,
    require_group_size: int = 1,
) -> dict:
    """Closed-form alternating: alpha[group] + lambda * v_self."""
    G = int(group_idx.max().item()) + 1
    alpha = torch.zeros(G, device=DEVICE)

    # Optionally restrict to groups with >= require_group_size sets
    sizes = torch.bincount(group_idx, minlength=G)
    keep = sizes[group_idx] >= require_group_size
    if not keep.any():
        return {"kernel": name, "rmse": float("nan"), "lambda": 0.0}

    g = group_idx[keep]
    v = v_self[keep]
    lm = log_m[keep]
    r = rtf[keep]
    w = weight[keep]

    lam = torch.tensor(0.0, device=DEVICE)
    for _ in range(50):
        resid = alpha[g] - lm
        denom = (v ** 2).sum().clamp(min=1e-12)
        lam = (v * resid).sum() / denom
        target = lm + lam * v
        # alpha[group] = mean(target) over members
        alpha = torch.zeros(G, device=DEVICE).scatter_add_(
            0, g, target,
        ) / sizes.clamp(min=1).float()
    pred = alpha[g] - lam * v
    rmse = reps_rmse(pred, w, r)
    return {
        "kernel": name,
        "n_sets": int(keep.sum().item()),
        "n_groups": int((sizes >= require_group_size).sum().item()),
        "rmse": rmse,
        "lambda": lam.item(),
    }


def main() -> None:
    data = build_problem(ROOT / "prod_sets_drew.parquet", DB)
    df = data.sets.copy()
    df["k_tonnage"] = df["weight"] * df["reps"]
    df["k_reps"] = df["reps"]
    df["k_count"] = 1.0
    df["k_eff_reps"] = df["reps"] * (5.0 - df["rir"]).clip(lower=0.0)
    df["k_rtf"] = df["rtf"]

    df = df.sort_values(["date", "session_id", "set_order", "id"]).reset_index(drop=True)

    e_idx = data.e_idx.to(DEVICE)
    log_m = data.log_m_obs.to(DEVICE)
    rtf = data.rtf_obs.to(DEVICE)
    w = data.weight.to(DEVICE)

    # Build group ids
    sess_ids = pd.factorize(df["session_id"])[0]
    se_pair = sess_ids * 1000 + df["exercise_id"].map(
        {eid: i for i, eid in enumerate(data.exercise_ids)}
    ).values
    se_pair = pd.factorize(se_pair)[0]
    group_se = torch.tensor(se_pair, dtype=torch.long, device=DEVICE)

    sess_grp = torch.tensor(sess_ids, dtype=torch.long, device=DEVICE)

    print("== Per-(session, exercise) intercept (only groups with >= 2 sets) ==")
    print("(This isolates pure within-session decay — apples-to-apples with beta-table)\n")
    for name, col in [
        ("tonnage", "k_tonnage"),
        ("reps", "k_reps"),
        ("set_count", "k_count"),
        ("eff_reps", "k_eff_reps"),
        ("rtf", "k_rtf"),
    ]:
        v_np = (
            df.groupby(["session_id", "exercise_id"])[col].cumsum() - df[col]
        ).values
        v = torch.tensor(v_np, dtype=torch.float32, device=DEVICE)
        r = fit_with_grouped_alpha(
            v, group_se, log_m, rtf, w,
            name=name, require_group_size=2,
        )
        # Report shift at median nonzero v
        med_v = float(np.median(v_np[v_np > 0])) if (v_np > 0).any() else 0.0
        shift = r["lambda"] * med_v
        r["median_v"] = med_v
        r["log_m_shift"] = shift
        r["M_mult"] = float(np.exp(-shift))
        print(f"  {r['kernel']:10s}  n_sets={r['n_sets']:3d} n_groups={r['n_groups']:3d}  "
              f"rmse={r['rmse']:.4f}  lambda={r['lambda']:+.5e}  "
              f"shift_at_med_v={shift:+.4f}  M_mult={r['M_mult']:.3f}")

    print("\n== Per-session-only intercept (controls for global session feel) ==")
    for name, col in [
        ("tonnage", "k_tonnage"),
        ("reps", "k_reps"),
        ("set_count", "k_count"),
        ("eff_reps", "k_eff_reps"),
        ("rtf", "k_rtf"),
    ]:
        v_np = (
            df.groupby(["session_id", "exercise_id"])[col].cumsum() - df[col]
        ).values
        v = torch.tensor(v_np, dtype=torch.float32, device=DEVICE)
        r = fit_with_grouped_alpha(
            v, sess_grp, log_m, rtf, w,
            name=name, require_group_size=1,
        )
        med_v = float(np.median(v_np[v_np > 0])) if (v_np > 0).any() else 0.0
        shift = r["lambda"] * med_v
        r["median_v"] = med_v
        r["log_m_shift"] = shift
        r["M_mult"] = float(np.exp(-shift))
        print(f"  {r['kernel']:10s}  rmse={r['rmse']:.4f}  "
              f"lambda={r['lambda']:+.5e}  shift_at_med_v={shift:+.4f}  "
              f"M_mult={r['M_mult']:.3f}")


if __name__ == "__main__":
    main()
