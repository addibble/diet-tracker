"""Visualization utilities for model exploration.

Generates matplotlib plots for strength curves, session fatigue, and M(t) evolution.
Saves to tools/model_exploration/plots/.
"""

import math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

PLOT_DIR = Path(__file__).parent / "plots"
PLOT_DIR.mkdir(exist_ok=True)


def plot_strength_curve(result, save=True, show_obs=True):
    """Plot fitted strength curve r_fresh(W) with observations overlaid."""
    from strength_curve import fresh_curve

    fig, ax = plt.subplots(1, 1, figsize=(10, 6))

    # Clip plot range to observed data + 30%, not all the way to M
    min_obs_w = result.min_observed_weight if result.min_observed_weight > 0 else 1
    max_obs_w = result.max_observed_weight
    plot_right = min(result.M * 0.95, max_obs_w * 1.3)
    plot_left = min_obs_w * 0.7

    W_range = np.linspace(max(1, plot_left), plot_right, 200)
    r_pred = fresh_curve(W_range, result.M, result.k, result.gamma)
    ax.plot(W_range, r_pred, "b-", linewidth=2, label=f"Fitted: k={result.k:.1f}, gamma={result.gamma:.2f}")

    # Overlay observations
    if show_obs and result.observations:
        rpe_obs = [o for o in result.observations if o.observation_type == "rpe"]
        ord_obs = [o for o in result.observations if o.observation_type == "ordinal"]

        if rpe_obs:
            ax.scatter(
                [o.effective_weight for o in rpe_obs],
                [o.reps_to_failure for o in rpe_obs],
                c="red", s=60, alpha=0.7, zorder=5, label=f"RPE obs (n={len(rpe_obs)})",
                edgecolors="darkred",
            )
        if ord_obs:
            ax.scatter(
                [o.effective_weight for o in ord_obs],
                [o.reps_to_failure for o in ord_obs],
                c="orange", s=30, alpha=0.4, zorder=4, label=f"Ordinal obs (n={len(ord_obs)})",
                marker="^",
            )

    # Mark M: in-plot line if visible, otherwise off-plot annotation
    if result.M <= plot_right * 1.05:
        ax.axvline(x=result.M, color="green", linestyle="--", alpha=0.6, label=f"M={result.M:.1f}")
    else:
        # M is off-plot -- annotate with arrow at right edge
        ax.annotate(
            f"M={result.M:.0f} ->",
            xy=(plot_right, 0.5), fontsize=10, color="green", fontweight="bold",
            ha="right", va="bottom",
        )

    # Show Brzycki estimate if available
    brz = getattr(result, "brzycki_M", 0)
    if brz > 0:
        ax.axvline(x=brz, color="purple", linestyle=":", alpha=0.5, label=f"Brzycki 1RM={brz:.0f}")

    ax.set_xlabel("Weight (lb)", fontsize=12)
    ax.set_ylabel("Reps to Failure", fontsize=12)

    ident = getattr(result, "identifiability", -1)
    ident_str = f", ident={ident:.2f}" if ident >= 0 else ""
    ax.set_title(f"{result.exercise_name} -- Fresh-Set Strength Curve\n"
                 f"M={result.M:.1f}, k={result.k:.1f}, gamma={result.gamma:.2f}, "
                 f"RMSE={result.residual_rmse:.2f}{ident_str}",
                 fontsize=13)
    ax.legend(fontsize=10)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save:
        fname = PLOT_DIR / f"curve_{result.exercise_name.replace(' ', '_').replace('/', '_')}.png"
        fig.savefig(fname, dpi=120)
        plt.close(fig)
        return str(fname)
    return fig


def plot_session_fatigue(session_data: dict, save=True):
    """Plot predicted vs actual reps through a session, showing fatigue accumulation.

    session_data: {
        'date': str,
        'exercise_name': str,
        'sets': [{'set_order', 'weight', 'actual_reps', 'predicted_reps', 'fatigue', 'phi'}],
    }
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    sets = session_data["sets"]
    x = list(range(1, len(sets) + 1))
    actual = [s["actual_reps"] for s in sets]
    predicted = [s["predicted_reps"] for s in sets]
    phi = [s["phi"] for s in sets]

    # Top: reps comparison
    ax1.plot(x, actual, "ro-", markersize=8, label="Actual reps", linewidth=2)
    ax1.plot(x, predicted, "bs--", markersize=8, label="Predicted reps", linewidth=2)
    ax1.set_ylabel("Reps", fontsize=12)
    ax1.set_title(f"Session Fatigue Replay -- {session_data['exercise_name']} ({session_data['date']})", fontsize=13)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Bottom: fatigue modifier phi
    ax2.plot(x, phi, "g^-", markersize=8, linewidth=2)
    ax2.set_xlabel("Set Number", fontsize=12)
    ax2.set_ylabel("phi (fatigue modifier)", fontsize=12)
    ax2.set_ylim(0, 1.1)
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=1.0, color="gray", linestyle=":", alpha=0.5)

    plt.tight_layout()
    if save:
        name = session_data["exercise_name"].replace(" ", "_").replace("/", "_")
        fname = PLOT_DIR / f"fatigue_{name}_{session_data['date']}.png"
        fig.savefig(fname, dpi=120)
        plt.close(fig)
        return str(fname)
    return fig


def plot_session_fatigue_from_replay(replay, save=True):
    """Plot session fatigue from a SessionReplay object."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    preds = replay.predictions
    x = list(range(1, len(preds) + 1))
    actual = [p.actual_reps for p in preds]
    predicted_fresh = [p.predicted_reps_fresh for p in preds]
    predicted_fat = [p.predicted_reps_fatigued for p in preds]
    phi = [p.phi for p in preds]

    ax1.plot(x, actual, "ro-", markersize=8, label="Actual reps performed", linewidth=2)
    ax1.plot(x, predicted_fresh, "g^--", markersize=7, label="Predicted (fresh)", linewidth=1.5, alpha=0.6)
    ax1.plot(x, predicted_fat, "bs--", markersize=8, label="Predicted (fatigued)", linewidth=2)

    # Mark RPE-tagged sets
    for i, p in enumerate(preds):
        if p.has_rpe and p.actual_reps_to_failure is not None:
            ax1.plot(i + 1, p.actual_reps_to_failure, "kx", markersize=10, markeredgewidth=2)
    ax1.plot([], [], "kx", markersize=10, markeredgewidth=2, label="Actual r_fail (RPE)")

    ax1.set_ylabel("Reps", fontsize=12)
    ax1.set_title(f"Session Fatigue -- {replay.exercise_name} ({replay.date})\n"
                  f"RMSE: fresh={replay.rmse_fresh:.2f}, fatigued={replay.rmse_fatigued:.2f}",
                  fontsize=13)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    ax2.plot(x, phi, "g^-", markersize=8, linewidth=2)
    ax2.set_xlabel("Set Number", fontsize=12)
    ax2.set_ylabel("phi (fatigue modifier)", fontsize=12)
    ax2.set_ylim(0, 1.1)
    ax2.grid(True, alpha=0.3)
    ax2.axhline(y=1.0, color="gray", linestyle=":", alpha=0.5)

    plt.tight_layout()
    if save:
        name = replay.exercise_name.replace(" ", "_").replace("/", "_")
        fname = PLOT_DIR / f"fatigue_{name}_{replay.date}.png"
        fig.savefig(fname, dpi=120)
        plt.close(fig)
        return str(fname)
    return fig


def plot_strength_evolution(exercise_name: str, dates: list[str], M_values: list[float],
                            recovery_pct: list[float] | None = None,
                            injury_dates: list[str] | None = None, save=True):
    """Plot M(t) time series and optionally recovery%."""
    n_plots = 2 if recovery_pct else 1
    fig, axes = plt.subplots(n_plots, 1, figsize=(12, 4 * n_plots), sharex=True)
    if n_plots == 1:
        axes = [axes]

    from datetime import date as dtdate
    date_objs = [dtdate.fromisoformat(d) for d in dates]

    # M(t)
    axes[0].plot(date_objs, M_values, "b-", linewidth=2, marker=".", markersize=4)
    axes[0].set_ylabel("M(t) -- Strength Ceiling", fontsize=12)
    axes[0].set_title(f"{exercise_name} -- Strength Evolution", fontsize=13)
    axes[0].grid(True, alpha=0.3)

    if injury_dates:
        for d in injury_dates:
            inj_date = dtdate.fromisoformat(d)
            axes[0].axvline(x=inj_date, color="red", linestyle="--", alpha=0.6, label="Injury")

    # Recovery%
    if recovery_pct:
        axes[1].plot(date_objs, recovery_pct, "g-", linewidth=2, marker=".", markersize=4)
        axes[1].set_ylabel("Recovery %", fontsize=12)
        axes[1].axhline(y=100, color="gray", linestyle=":", alpha=0.5)
        axes[1].set_ylim(50, 110)
        axes[1].grid(True, alpha=0.3)

    axes[-1].set_xlabel("Date", fontsize=12)
    fig.autofmt_xdate()

    plt.tight_layout()
    if save:
        fname = PLOT_DIR / f"evolution_{exercise_name.replace(' ', '_').replace('/', '_')}.png"
        fig.savefig(fname, dpi=120)
        plt.close(fig)
        return str(fname)
    return fig


def plot_curve_gallery(results: list, top_n: int = 20, save=True):
    """Plot a gallery of strength curves for top N exercises."""
    from strength_curve import fresh_curve

    successful = [r for r in results if r.success and r.residual_rmse < 50]
    successful.sort(key=lambda x: x.n_observations, reverse=True)
    to_plot = successful[:top_n]

    ncols = 4
    nrows = math.ceil(len(to_plot) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4 * nrows))
    axes = axes.flatten() if nrows > 1 else (axes if ncols > 1 else [axes])

    for i, result in enumerate(to_plot):
        ax = axes[i]
        # Clip to data range + 30%
        max_obs_w = result.max_observed_weight
        min_obs_w = getattr(result, "min_observed_weight", 1) or 1
        plot_right = min(result.M * 0.95, max_obs_w * 1.3)
        W_range = np.linspace(max(1, min_obs_w * 0.7), plot_right, 100)
        r_pred = fresh_curve(W_range, result.M, result.k, result.gamma)
        ax.plot(W_range, r_pred, "b-", linewidth=1.5)

        # Observations
        rpe_obs = [o for o in result.observations if o.observation_type == "rpe"]
        if rpe_obs:
            ax.scatter(
                [o.effective_weight for o in rpe_obs],
                [o.reps_to_failure for o in rpe_obs],
                c="red", s=20, alpha=0.6, zorder=5,
            )

        brz = getattr(result, "brzycki_M", 0)
        brz_str = f" brz={brz:.0f}" if brz > 0 else ""
        ax.set_title(f"{result.exercise_name[:25]}\nM={result.M:.0f}{brz_str} k={result.k:.1f} g={result.gamma:.2f}",
                     fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.2)

    # Hide empty subplots
    for j in range(len(to_plot), len(axes)):
        axes[j].set_visible(False)

    fig.suptitle("Strength Curve Gallery -- Top Exercises by Data Volume", fontsize=14, y=1.01)
    plt.tight_layout()
    if save:
        fname = PLOT_DIR / "curve_gallery.png"
        fig.savefig(fname, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return str(fname)
    return fig
