"""Fit global-λ multiplicative M-decay model and compare to baselines.

Model family (shape-invariant universal curve, only M shifts with V_eff):
  rtf_pred = k * (M(V)/W - 1)^gamma
  M(V) = M_0 · exp(-λ · V_eff)

Inverted per-set target:
  log(M_obs) = log(M_0_exercise) - λ · V_eff + ε

We fit per-exercise intercepts (fixed effects) + one global λ by OLS on
log-M space. Compare held-out per-session RMSE in reps space against:
  - Fresh-only (no fatigue, per-exercise M_0)
  - Set-index β-table (current production model)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
K = 20.0
GAMMA = 0.9


def predict_reps(w: np.ndarray, m: np.ndarray, rir: np.ndarray) -> np.ndarray:
    """Predict integer-ish reps at a given weight, M, and target RIR.

    rtf = k * (M/W - 1)^gamma ; reps = rtf - rir. Clamp to >= 0.
    """
    ratio = np.maximum(m / np.maximum(w, 1e-6) - 1.0, 0.0)
    rtf = K * np.power(ratio, GAMMA)
    return np.maximum(rtf - rir, 0.0)


def fit_fresh_only(df: pd.DataFrame) -> dict[int, float]:
    """Per-exercise M_0 = exp(mean(log M_obs))."""
    return (
        df.groupby("exercise_id")["m_obs"]
          .apply(lambda s: float(np.exp(np.mean(np.log(s)))))
          .to_dict()
    )


def fit_global_lambda(
    df: pd.DataFrame,
) -> tuple[dict[int, float], float]:
    """Fit log(M_obs) = alpha_e - λ·V_eff via OLS with per-exercise intercepts.

    Returns (M_0 per exercise, λ).
    """
    # Encode exercise as integer index.
    exercises = sorted(df["exercise_id"].unique())
    ex_idx = {e: i for i, e in enumerate(exercises)}
    n_ex = len(exercises)
    n = len(df)
    # Design matrix: [n_ex one-hot columns for intercepts, 1 column for V_eff].
    x = np.zeros((n, n_ex + 1))
    for i, e in enumerate(df["exercise_id"].to_numpy()):
        x[i, ex_idx[int(e)]] = 1.0
    x[:, -1] = -df["v_eff"].to_numpy()  # coefficient will be λ
    y = np.log(df["m_obs"].to_numpy())
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    m0 = {exercises[i]: float(np.exp(beta[i])) for i in range(n_ex)}
    lam = float(beta[-1])
    return m0, lam


def fit_beta_table(df: pd.DataFrame, m0_fresh: dict[int, float]) -> np.ndarray:
    """Current production approach: β_s = mean residual (rtf_obs - rtf_fresh)
    aggregated over set index within session-and-exercise, global across ex."""
    max_idx = int(df["set_idx_in_session_ex"].max())
    beta = np.zeros(max_idx + 1)
    for s in range(1, max_idx + 1):
        rows = df[df["set_idx_in_session_ex"] == s]
        if len(rows) < 5:
            beta[s] = 0.0
            continue
        m0 = rows["exercise_id"].map(m0_fresh).to_numpy()
        w = rows["weight"].to_numpy()
        ratio = np.maximum(m0 / w - 1.0, 0.0)
        rtf_fresh = K * np.power(ratio, GAMMA)
        beta[s] = float(np.mean(rows["rtf"].to_numpy() - rtf_fresh))
    return beta


def reps_rmse(
    df: pd.DataFrame,
    rtf_pred: np.ndarray,
) -> float:
    """RMSE between predicted reps (rtf - rir) and observed reps."""
    pred_reps = np.maximum(rtf_pred - df["rir"].to_numpy(), 0.0)
    resid = pred_reps - df["reps"].to_numpy()
    return float(np.sqrt(np.mean(resid ** 2)))


def eval_on(
    df: pd.DataFrame,
    m0: dict[int, float],
    lam: float = 0.0,
    beta_per_set: np.ndarray | None = None,
) -> float:
    m0_arr = df["exercise_id"].map(m0).to_numpy()
    # Skip sets for exercises not in training set (cold start).
    mask = np.isfinite(m0_arr.astype(float))
    df = df[mask]
    m0_arr = m0_arr[mask].astype(float)
    w = df["weight"].to_numpy()
    v = df["v_eff"].to_numpy()

    m_eff = m0_arr * np.exp(-lam * v)
    ratio = np.maximum(m_eff / w - 1.0, 0.0)
    rtf = K * np.power(ratio, GAMMA)

    if beta_per_set is not None:
        s_idx = df["set_idx_in_session_ex"].to_numpy()
        s_idx = np.clip(s_idx, 1, len(beta_per_set) - 1)
        rtf = rtf + beta_per_set[s_idx]

    return reps_rmse(df, rtf)


def session_cv(df: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-session-out CV for each of three models."""
    sessions = sorted(df["session_id"].unique())
    rows = []
    for ses in sessions:
        train = df[df["session_id"] != ses]
        test = df[df["session_id"] == ses]
        if len(test) < 3:
            continue

        m0_fresh = fit_fresh_only(train)
        m0_lam, lam = fit_global_lambda(train)
        beta_table = fit_beta_table(train, m0_fresh)

        fresh_rmse = eval_on(test, m0_fresh)
        lam_rmse = eval_on(test, m0_lam, lam=lam)
        beta_rmse = eval_on(test, m0_fresh, beta_per_set=beta_table)

        rows.append({
            "session_id": ses, "n_test": len(test),
            "fresh_rmse": fresh_rmse, "beta_rmse": beta_rmse,
            "lam_rmse": lam_rmse, "lambda": lam,
        })
    return pd.DataFrame(rows)


def main() -> None:
    df = pd.read_parquet(ROOT / "prod_sets_drew.parquet")
    print(f"Loaded {len(df)} sets, {df['exercise_id'].nunique()} exercises")

    print("\n== In-sample fits ==")
    m0_fresh = fit_fresh_only(df)
    m0_lam, lam = fit_global_lambda(df)
    beta = fit_beta_table(df, m0_fresh)

    print(f"  Global λ = {lam:.6e}  (1/tonnage units)")
    print(f"  λ * median_V_eff = {lam * df['v_eff'].median():.3f} nats of M")
    print(f"  → median M multiplier from fatigue: "
          f"{np.exp(-lam * df['v_eff'].median()):.3f}")
    print(f"  β-table: {[round(float(b), 2) for b in beta[1:6]]}")

    in_fresh = eval_on(df, m0_fresh)
    in_beta = eval_on(df, m0_fresh, beta_per_set=beta)
    in_lam = eval_on(df, m0_lam, lam=lam)
    print(f"\n  In-sample reps RMSE:")
    print(f"    fresh-only      : {in_fresh:.3f}")
    print(f"    β-table (prod)  : {in_beta:.3f}")
    print(f"    global-λ mult   : {in_lam:.3f}")

    print("\n== Leave-one-session-out CV ==")
    cv = session_cv(df)
    print(f"  n_sessions evaluated: {len(cv)}")
    for col in ("fresh_rmse", "beta_rmse", "lam_rmse"):
        print(f"  {col:12s} median={cv[col].median():.3f} "
              f"mean={cv[col].mean():.3f} "
              f"p10={cv[col].quantile(0.10):.3f} "
              f"p90={cv[col].quantile(0.90):.3f}")
    print(f"  lambda stability : mean={cv['lambda'].mean():.6e} "
          f"std={cv['lambda'].std():.6e}")

    # Save for plotting.
    cv.to_csv(ROOT / "session_cv_results.csv", index=False)
    pd.DataFrame({
        "exercise_id": list(m0_lam.keys()),
        "m0_lambda": list(m0_lam.values()),
        "m0_fresh": [m0_fresh[e] for e in m0_lam],
    }).to_csv(ROOT / "per_exercise_m0.csv", index=False)
    print(f"\nSaved: session_cv_results.csv, per_exercise_m0.csv")


if __name__ == "__main__":
    main()
