"""Sanity check: can we recover the well-known within-exercise decay
with a single global scalar?

Model: log_M[s] = alpha[e_s] - kappa * (prior tonnage of e_s in s's session)

If this DOESN'T improve on alpha-only, the problem is data, not the manifold model.
If it DOES, the previous v2 fit failed for parameterization reasons, not signal absence.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch

from fit_manifold_v2 import build_problem, DB, reps_rmse

ROOT = Path(__file__).parent
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def main() -> None:
    data = build_problem(ROOT / "prod_sets_drew.parquet", DB)
    U = data.U.to(DEVICE)
    e_idx = data.e_idx.to(DEVICE)
    log_m = data.log_m_obs.to(DEVICE)
    rtf = data.rtf_obs.to(DEVICE)
    w = data.weight.to(DEVICE)

    E = U.shape[1]

    # Within-exercise prior tonnage: u_self[s] = U[s, e_s].
    u_self = U.gather(1, e_idx.unsqueeze(1)).squeeze(1)   # (S,)
    print(f"u_self stats: mean={u_self.mean():.1f}  median={u_self.median():.1f}  "
          f"max={u_self.max():.1f}  frac_zero={(u_self == 0).float().mean():.2%}")

    # --- Baseline: alpha-only (per-exercise mean) ---
    alpha_only = torch.zeros(E, device=DEVICE)
    for e in range(E):
        m = e_idx == e
        if m.any():
            alpha_only[e] = log_m[m].mean()
    pred = alpha_only[e_idx]
    rmse_alpha = reps_rmse(pred, w, rtf)
    print(f"\nAlpha-only baseline:    reps RMSE = {rmse_alpha:.4f}")

    # --- Within-exercise scalar fit (closed-form OLS for kappa given alpha) ---
    # Joint OLS: minimize sum_s (alpha[e_s] - kappa * u_self[s] - log_m[s])^2
    # We solve by alternating: alpha[e] = mean(log_m + kappa*u_self) over e_s == e,
    # then kappa = sum(u_self * (alpha[e_s] - log_m)) / sum(u_self^2)
    # Iterate to convergence.
    alpha = alpha_only.clone()
    kappa = torch.tensor(0.0, device=DEVICE)
    for it in range(50):
        # Update kappa
        resid = alpha[e_idx] - log_m
        if (u_self ** 2).sum() > 0:
            kappa = (u_self * resid).sum() / (u_self ** 2).sum()
        # Update alpha
        target = log_m + kappa * u_self
        for e in range(E):
            m = e_idx == e
            if m.any():
                alpha[e] = target[m].mean()
    pred = alpha[e_idx] - kappa * u_self
    rmse_within = reps_rmse(pred, w, rtf)
    print(f"Alpha + kappa*u_self:   reps RMSE = {rmse_within:.4f}")
    print(f"  kappa = {kappa.item():.6e}")
    print(f"  kappa * median u_self = {(kappa * u_self.median()).item():.4f}  "
          f"(log-M shift)")
    print(f"  -> implied M multiplier at median prior tonnage: "
          f"{torch.exp(-kappa * u_self.median()).item():.4f}")

    # --- Per-exercise kappa ---
    alpha2 = alpha_only.clone()
    kappa_e = torch.zeros(E, device=DEVICE)
    for it in range(100):
        resid = alpha2[e_idx] - log_m
        for e in range(E):
            m = e_idx == e
            if m.any():
                num = (u_self[m] * resid[m]).sum()
                den = (u_self[m] ** 2).sum()
                kappa_e[e] = num / den.clamp(min=1e-9)
        target = log_m + kappa_e[e_idx] * u_self
        for e in range(E):
            m = e_idx == e
            if m.any():
                alpha2[e] = target[m].mean()
    pred = alpha2[e_idx] - kappa_e[e_idx] * u_self
    rmse_per_ex = reps_rmse(pred, w, rtf)
    print(f"Alpha + kappa[e]*u_self: reps RMSE = {rmse_per_ex:.4f}")
    print(f"  kappa[e] stats: mean={kappa_e.mean().item():+.3e}  "
          f"median={kappa_e.median().item():+.3e}  "
          f"frac_positive={(kappa_e > 0).float().mean().item():.2%}")
    # Show top-10 by abs kappa
    abs_k = kappa_e.abs().cpu().numpy()
    order = np.argsort(-abs_k)[:10]
    print("  Top-10 |kappa[e]|:")
    for i in order:
        n = (e_idx == i).sum().item()
        print(f"    {data.exercise_names[i][:32]:32s} n={n:3d}  "
              f"kappa={kappa_e[i].item():+.3e}  "
              f"med_u_self={u_self[e_idx == i].median().item():.0f}")


if __name__ == "__main__":
    main()
