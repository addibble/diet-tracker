"""Compare V_eff kernels: which definition of accumulated work
predicts subsequent reps best?

Kernels to test (all summed over prior sets in same session, weighted by
exercise-tissue overlap):
  K1: tonnage           = W * reps
  K2: reps              = reps
  K3: set_count         = 1                        (recovers beta-table)
  K4: effective_reps    = reps * max(0, 5 - RIR)   (proximity to failure)
  K5: rtf               = reps + RIR               (target reps to failure)

For each kernel, fit:
  log_M[s] = alpha[e_s] - lambda * V_self[s]
where V_self[s] = sum of kernel applied to prior sets of SAME exercise.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch

from fit_manifold_v2 import build_problem, DB, reps_rmse

ROOT = Path(__file__).parent
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def per_set_v_self(df: pd.DataFrame, kernel_col: str) -> np.ndarray:
    """Cumsum of kernel within (session_id, exercise_id), exclusive."""
    grouped = df.groupby(["session_id", "exercise_id"])[kernel_col]
    return (grouped.cumsum() - df[kernel_col]).values


def fit_alpha_lambda(
    v_self: torch.Tensor,
    e_idx: torch.Tensor,
    log_m: torch.Tensor,
    rtf: torch.Tensor,
    weight: torch.Tensor,
    name: str,
) -> dict:
    """Closed-form alternating: alpha + lambda * v_self."""
    E = int(e_idx.max().item()) + 1
    alpha = torch.zeros(E, device=DEVICE)
    for e in range(E):
        m = e_idx == e
        if m.any():
            alpha[e] = log_m[m].mean()
    lam = torch.tensor(0.0, device=DEVICE)
    for _ in range(50):
        resid = alpha[e_idx] - log_m
        denom = (v_self ** 2).sum().clamp(min=1e-12)
        lam = (v_self * resid).sum() / denom
        target = log_m + lam * v_self
        for e in range(E):
            m = e_idx == e
            if m.any():
                alpha[e] = target[m].mean()
    pred = alpha[e_idx] - lam * v_self
    rmse = reps_rmse(pred, weight, rtf)
    med_v = v_self[v_self > 0].median().item() if (v_self > 0).any() else 0.0
    shift = (lam * torch.tensor(med_v, device=DEVICE)).item()
    return {
        "kernel": name,
        "rmse": rmse,
        "lambda": lam.item(),
        "median_v": med_v,
        "log_m_shift_at_median_v": shift,
        "M_mult_at_median_v": float(np.exp(-shift)),
    }


def main() -> None:
    data = build_problem(ROOT / "prod_sets_drew.parquet", DB)
    df = data.sets.copy()

    # Compute kernel columns.
    df["k_tonnage"] = df["weight"] * df["reps"]
    df["k_reps"] = df["reps"]
    df["k_count"] = 1.0
    df["k_eff_reps"] = df["reps"] * (5.0 - df["rir"]).clip(lower=0.0)
    df["k_rtf"] = df["rtf"]

    # Re-sort to chronological order matching build_problem.
    df = df.sort_values(["date", "session_id", "set_order", "id"]).reset_index(drop=True)

    e_idx = data.e_idx.to(DEVICE)
    log_m = data.log_m_obs.to(DEVICE)
    rtf = data.rtf_obs.to(DEVICE)
    w = data.weight.to(DEVICE)

    # Alpha-only baseline
    alpha = torch.zeros(int(e_idx.max().item()) + 1, device=DEVICE)
    for e in range(alpha.shape[0]):
        m = e_idx == e
        if m.any():
            alpha[e] = log_m[m].mean()
    pred = alpha[e_idx]
    print(f"Alpha-only baseline RMSE = {reps_rmse(pred, w, rtf):.4f}\n")

    results = []
    for name, col in [
        ("tonnage (W*reps)", "k_tonnage"),
        ("reps", "k_reps"),
        ("set_count", "k_count"),
        ("eff_reps (reps*(5-RIR)+)", "k_eff_reps"),
        ("rtf (reps+RIR)", "k_rtf"),
    ]:
        v_self_np = per_set_v_self(df, col)
        v_self = torch.tensor(v_self_np, dtype=torch.float32, device=DEVICE)
        r = fit_alpha_lambda(v_self, e_idx, log_m, rtf, w, name)
        results.append(r)

    print("== Alpha + lambda * V_self_kernel ==")
    out = pd.DataFrame(results)
    print(out.to_string(index=False))
    out.to_csv(ROOT / "kernel_comparison.csv", index=False)
    print("\nSaved kernel_comparison.csv")


if __name__ == "__main__":
    main()
