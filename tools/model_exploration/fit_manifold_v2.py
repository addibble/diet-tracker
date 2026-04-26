"""Stage 3 — Learn L (exercise × tissue loading matrix) from data.

Bilinear model:
    log M_obs(set s, exercise i) = alpha[i] - sum_t L[i,t] * lambda[t] * V_t(s)
    V_t(s) = sum_{prior p in session} L[e_p, t] * W_p * reps_p

Unknowns:
    L in R^(E x T)_{>=0}
    lambda in R^T_{>=0}
    alpha in R^E

Four variants:
    A: L fixed at seed (tunes alpha + lambda only)
    B: L free + strong ridge to seed (learn corrections to anatomy)
    C: L free + weak regularization
    D: L init random (does seed carry real info?)

Evaluation: in-sample reps-RMSE and leave-one-session-out (LOSO) CV RMSE.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

ROOT = Path(__file__).parent
DB = ROOT / "prod_dbs_2026-04-19" / \
    "3d91e0e958b64d8eae86fdde4ff72783" / \
    "3d91e0e958b64d8eae86fdde4ff72783_2026-04-19_224613.db"

K_CURVE = 20.0
GAMMA = 0.9
RTF_CAP = 30.0

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ----------------------------------------------------------------------------
# Data prep
# ----------------------------------------------------------------------------

@dataclass
class ProblemData:
    sets: pd.DataFrame            # the per-set dataframe
    U: torch.Tensor               # (S, E) prior-exercise tonnage before each set
    e_idx: torch.Tensor           # (S,) long, exercise index of each set
    log_m_obs: torch.Tensor       # (S,)
    rtf_obs: torch.Tensor         # (S,)
    weight: torch.Tensor          # (S,)
    session_id: np.ndarray        # (S,) session id per set
    exercise_names: list[str]     # len E
    exercise_ids: list[int]       # len E (ordered)
    tissue_ids: list[int]         # len T
    tissue_names: list[str]
    L_seed: torch.Tensor          # (E, T) seed loading_factor matrix


def load_seed_L(
    db_path: Path, exercise_ids: list[int],
) -> tuple[torch.Tensor, list[int], list[str]]:
    con = sqlite3.connect(db_path)
    try:
        et = pd.read_sql_query(
            "SELECT exercise_id, tissue_id, loading_factor "
            "FROM exercise_tissues WHERE loading_factor > 0",
            con,
        )
        tis = pd.read_sql_query("SELECT id, name FROM tissues", con)
    finally:
        con.close()

    # Restrict tissues to those loaded by at least one of our E exercises.
    et = et[et["exercise_id"].isin(exercise_ids)].copy()
    tissue_ids = sorted(et["tissue_id"].unique().tolist())
    t_index = {tid: i for i, tid in enumerate(tissue_ids)}
    e_index = {eid: i for i, eid in enumerate(exercise_ids)}

    E, T = len(exercise_ids), len(tissue_ids)
    L = np.zeros((E, T), dtype=np.float32)
    for row in et.itertuples(index=False):
        L[e_index[int(row.exercise_id)], t_index[int(row.tissue_id)]] = \
            float(row.loading_factor)

    tname = {int(r.id): str(r.name) for r in tis.itertuples(index=False)}
    tissue_names = [tname.get(tid, f"t{tid}") for tid in tissue_ids]
    return torch.from_numpy(L), tissue_ids, tissue_names


def build_problem(parquet: Path, db_path: Path) -> ProblemData:
    df = pd.read_parquet(parquet).reset_index(drop=True)

    # Stable exercise ordering.
    exercise_ids = sorted(df["exercise_id"].unique().tolist())
    e_index = {eid: i for i, eid in enumerate(exercise_ids)}
    ex_names_map = df.groupby("exercise_id")["name"].first().to_dict()
    exercise_names = [ex_names_map[eid] for eid in exercise_ids]

    E = len(exercise_ids)
    S = len(df)

    # Build U[s, e] = sum of tonnage of exercise e among sets prior to s in s's session.
    U = np.zeros((S, E), dtype=np.float32)
    # Walk chronologically per session.
    df = df.sort_values(["date", "session_id", "set_order", "id"]).reset_index(drop=True)
    prev_session = None
    running: np.ndarray | None = None
    for idx, row in df.iterrows():
        sess = int(row["session_id"])
        if sess != prev_session:
            running = np.zeros(E, dtype=np.float32)
            prev_session = sess
        U[idx] = running  # snapshot BEFORE this set
        running[e_index[int(row["exercise_id"])]] += float(row["tonnage"])

    e_idx = torch.tensor(
        [e_index[int(eid)] for eid in df["exercise_id"].values], dtype=torch.long,
    )
    log_m_obs = torch.tensor(np.log(df["m_obs"].values), dtype=torch.float32)
    rtf_obs = torch.tensor(df["rtf"].values, dtype=torch.float32)
    weight = torch.tensor(df["weight"].values, dtype=torch.float32)

    L_seed, tissue_ids, tissue_names = load_seed_L(db_path, exercise_ids)

    return ProblemData(
        sets=df,
        U=torch.from_numpy(U),
        e_idx=e_idx,
        log_m_obs=log_m_obs,
        rtf_obs=rtf_obs,
        weight=weight,
        session_id=df["session_id"].values,
        exercise_names=exercise_names,
        exercise_ids=exercise_ids,
        tissue_ids=tissue_ids,
        tissue_names=tissue_names,
        L_seed=L_seed,
    )


# ----------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------

class ManifoldModel(torch.nn.Module):
    """
    L:     (E, T)  non-negative via softplus
    lam:   (T,)    non-negative via softplus
    alpha: (E,)    free

    Pred: log_M[s] = alpha[e_s] - L[e_s,:] @ diag(lam) @ L.T @ U[s,:]
        = alpha[e_s] - (L[e_s,:] * lam) . (U[s,:] @ L)
    """

    def __init__(
        self,
        L_seed: torch.Tensor,
        learn_L: bool = True,
        init_random: bool = False,
        lam_init: float = 1e-5,
    ) -> None:
        super().__init__()
        E, T = L_seed.shape

        # Parameterize L via softplus(raw_L). Init raw_L so softplus(raw_L) == L_seed.
        if init_random:
            # Random non-negative L roughly in the seed magnitude range.
            rng = torch.rand_like(L_seed) * L_seed.mean() * 2
            raw_init = _inv_softplus(rng + 1e-3)
        else:
            raw_init = _inv_softplus(L_seed + 1e-4)
        self.raw_L = torch.nn.Parameter(raw_init, requires_grad=learn_L)

        # lambda init: small positive.
        self.raw_lambda = torch.nn.Parameter(
            torch.full((T,), _inv_softplus_scalar(lam_init), dtype=torch.float32)
        )
        # alpha init: zeros (learn per-exercise intercept).
        self.alpha = torch.nn.Parameter(torch.zeros(E, dtype=torch.float32))

        self.register_buffer("L_seed", L_seed.clone())

    @property
    def L(self) -> torch.Tensor:
        return F.softplus(self.raw_L)

    @property
    def lam(self) -> torch.Tensor:
        return F.softplus(self.raw_lambda)

    def forward(self, U: torch.Tensor, e_idx: torch.Tensor) -> torch.Tensor:
        L = self.L                     # (E, T)
        lam = self.lam                 # (T,)
        V = U @ L                      # (S, T)
        L_dest = L[e_idx]              # (S, T)
        fatigue = (L_dest * lam * V).sum(dim=1)   # (S,)
        return self.alpha[e_idx] - fatigue


def _inv_softplus(x: torch.Tensor) -> torch.Tensor:
    # Inverse of softplus: y = log(exp(x) - 1), numerically stable for x > 0.
    return torch.where(x > 20, x, torch.log(torch.expm1(x.clamp(min=1e-6))))


def _inv_softplus_scalar(x: float) -> float:
    return float(_inv_softplus(torch.tensor([x])).item())


# ----------------------------------------------------------------------------
# Training
# ----------------------------------------------------------------------------

def train_model(
    model: ManifoldModel,
    U: torch.Tensor,
    e_idx: torch.Tensor,
    log_m_obs: torch.Tensor,
    mask: torch.Tensor | None = None,
    n_steps: int = 3000,
    lr: float = 0.05,
    ridge_L: float = 0.0,
    ridge_lambda: float = 1e-3,
    verbose: bool = False,
) -> list[float]:
    """Train and return the loss history."""
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    hist: list[float] = []
    if mask is None:
        mask = torch.ones_like(log_m_obs, dtype=torch.bool)

    for step in range(n_steps):
        opt.zero_grad()
        pred = model(U, e_idx)
        residual = (pred[mask] - log_m_obs[mask])
        mse = (residual ** 2).mean()

        reg_L = ridge_L * ((model.L - model.L_seed) ** 2).sum()
        reg_lam = ridge_lambda * (model.lam ** 2).sum()

        loss = mse + reg_L + reg_lam
        loss.backward()
        opt.step()

        if verbose and (step % 500 == 0 or step == n_steps - 1):
            print(f"  step {step:4d}  mse={mse.item():.5f}  "
                  f"reg_L={reg_L.item():.5f}  reg_lam={reg_lam.item():.5f}")
        hist.append(float(mse.item()))
    return hist


def reps_rmse(
    pred_log_m: torch.Tensor,
    weight: torch.Tensor,
    rtf_obs: torch.Tensor,
) -> float:
    """Convert log-M prediction back to reps and compute reps RMSE."""
    M_pred = torch.exp(pred_log_m).clamp(min=weight * 1.001)   # M > W
    # rtf = K * ((M/W) - 1)^gamma
    rtf_pred = K_CURVE * (M_pred / weight - 1.0).clamp(min=0.0) ** GAMMA
    err = rtf_pred - rtf_obs
    return float(torch.sqrt((err ** 2).mean()).item())


# ----------------------------------------------------------------------------
# Runners
# ----------------------------------------------------------------------------

@dataclass
class RunResult:
    name: str
    in_sample_rmse: float
    cv_rmse_median: float
    cv_rmse_mean: float
    L_drift: float         # ||L_learned - L_seed||_F / ||L_seed||_F
    lam_median: float
    lam_active: int        # count of lambda > 1e-6


def build_model(
    data: ProblemData,
    variant: str,
) -> ManifoldModel:
    if variant == "A":
        return ManifoldModel(data.L_seed, learn_L=False)
    if variant == "B":
        return ManifoldModel(data.L_seed, learn_L=True)
    if variant == "C":
        return ManifoldModel(data.L_seed, learn_L=True)
    if variant == "D":
        return ManifoldModel(data.L_seed, learn_L=True, init_random=True)
    raise ValueError(variant)


RIDGE_BY_VARIANT = {
    "A": 0.0,           # irrelevant, L frozen
    "B": 10.0,          # strong pull toward seed
    "C": 0.01,          # weak
    "D": 0.01,          # weak; seed was scrambled anyway
}


def run_variant(
    data: ProblemData,
    variant: str,
    n_steps: int = 3000,
    verbose: bool = False,
) -> RunResult:
    U = data.U.to(DEVICE)
    e_idx = data.e_idx.to(DEVICE)
    log_m = data.log_m_obs.to(DEVICE)
    rtf = data.rtf_obs.to(DEVICE)
    w = data.weight.to(DEVICE)

    # --- In-sample fit ---
    torch.manual_seed(0)
    model = build_model(data, variant).to(DEVICE)
    train_model(
        model, U, e_idx, log_m,
        n_steps=n_steps,
        ridge_L=RIDGE_BY_VARIANT[variant],
        verbose=verbose,
    )
    with torch.no_grad():
        pred = model(U, e_idx)
        in_rmse = reps_rmse(pred, w, rtf)
        L_drift = float(torch.norm(model.L - model.L_seed) /
                        torch.norm(model.L_seed).clamp(min=1e-6))
        lam = model.lam.detach().cpu().numpy()
        lam_median = float(np.median(lam))
        lam_active = int((lam > 1e-6).sum())

    # --- LOSO-CV ---
    sessions = np.array(sorted(set(int(s) for s in data.session_id)))
    session_arr = torch.tensor(
        [int(s) for s in data.session_id], dtype=torch.long, device=DEVICE,
    )
    cv_rmses: list[float] = []
    for held in sessions:
        train_mask = session_arr != int(held)
        test_mask = ~train_mask
        if test_mask.sum() == 0:
            continue
        torch.manual_seed(int(held) % 10_000)
        m = build_model(data, variant).to(DEVICE)
        train_model(
            m, U, e_idx, log_m,
            mask=train_mask,
            n_steps=max(1500, n_steps // 2),
            ridge_L=RIDGE_BY_VARIANT[variant],
            verbose=False,
        )
        with torch.no_grad():
            pred = m(U, e_idx)
            cv_rmses.append(
                reps_rmse(pred[test_mask], w[test_mask], rtf[test_mask])
            )

    return RunResult(
        name=f"variant_{variant}",
        in_sample_rmse=in_rmse,
        cv_rmse_median=float(np.median(cv_rmses)),
        cv_rmse_mean=float(np.mean(cv_rmses)),
        L_drift=L_drift,
        lam_median=lam_median,
        lam_active=lam_active,
    )


# ----------------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------------

def diagnose_L_changes(
    data: ProblemData, variant: str = "B", n_steps: int = 5000,
) -> pd.DataFrame:
    """Report largest L[e,t] edges that changed from seed to learned value."""
    torch.manual_seed(0)
    model = build_model(data, variant).to(DEVICE)
    train_model(
        model,
        data.U.to(DEVICE), data.e_idx.to(DEVICE), data.log_m_obs.to(DEVICE),
        n_steps=n_steps, ridge_L=RIDGE_BY_VARIANT[variant], verbose=False,
    )
    with torch.no_grad():
        L_learned = model.L.cpu().numpy()
        L_seed = data.L_seed.cpu().numpy()
        lam = model.lam.cpu().numpy()

    # Report changes weighted by lam (so we focus on tissues that matter).
    delta = L_learned - L_seed
    E, T = delta.shape
    rows = []
    for e in range(E):
        for t in range(T):
            weighted = abs(delta[e, t]) * lam[t]
            rows.append({
                "exercise": data.exercise_names[e],
                "tissue": data.tissue_names[t],
                "seed_L": float(L_seed[e, t]),
                "learned_L": float(L_learned[e, t]),
                "delta": float(delta[e, t]),
                "lam_t": float(lam[t]),
                "weighted_change": float(weighted),
            })
    out = pd.DataFrame(rows).sort_values(
        "weighted_change", ascending=False,
    ).reset_index(drop=True)
    return out


def main() -> None:
    print(f"Device: {DEVICE}")
    parquet = ROOT / "prod_sets_drew.parquet"
    data = build_problem(parquet, DB)
    print(f"Sets: {len(data.sets)}  Exercises: {len(data.exercise_ids)}  "
          f"Tissues: {len(data.tissue_ids)}")
    print(f"L_seed nonzero entries: {int((data.L_seed > 0).sum())} of "
          f"{data.L_seed.numel()}  "
          f"seed mean (nonzero): "
          f"{data.L_seed[data.L_seed > 0].mean().item():.3f}")

    print("\n== Fitting variants ==")
    results = []
    for variant in ("A", "B", "C", "D"):
        print(f"\n-- variant {variant} --")
        r = run_variant(data, variant, n_steps=2500, verbose=(variant == "A"))
        print(
            f"  {r.name:14s} in-sample={r.in_sample_rmse:.3f} "
            f"cv_median={r.cv_rmse_median:.3f} cv_mean={r.cv_rmse_mean:.3f} "
            f"L_drift={r.L_drift:.3f} lam_med={r.lam_median:.3e} "
            f"lam_active={r.lam_active}"
        )
        results.append(r)

    print("\n== Summary ==")
    summary = pd.DataFrame([r.__dict__ for r in results])
    print(summary.to_string(index=False))
    summary.to_csv(ROOT / "manifold_v2_summary.csv", index=False)

    # Detailed L change report for variant B (the physiologically principled one).
    print("\n== Top weighted L changes (variant B, seed-regularized) ==")
    changes = diagnose_L_changes(data, variant="B", n_steps=5000)
    print(changes.head(25).to_string(index=False))
    changes.to_csv(ROOT / "manifold_v2_L_changes.csv", index=False)
    print(f"\nSaved manifold_v2_summary.csv and manifold_v2_L_changes.csv")


if __name__ == "__main__":
    main()
