"""Generate figure showing how two curves with different gamma
fit the same moderate-load observations but diverge at heavy loads."""

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import minimize

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
})

def fresh(W, M, k, gamma):
    r = np.where(W < M, k * np.power(np.maximum(M / W - 1, 0), gamma), 0.0)
    return r

def fit_Mk(gamma_fixed):
    """Fit (M, k) for a fixed gamma to the two observed points."""
    def loss(p):
        M, k = p
        r1 = k * ((M / 100) - 1) ** gamma_fixed
        r2 = k * ((M / 120) - 1) ** gamma_fixed
        return (r1 - 18) ** 2 + (r2 - 12) ** 2
    best = None
    for M0 in [140, 160, 180, 200, 240, 300]:
        for k0 in [8, 15, 25, 40, 60]:
            try:
                res = minimize(loss, [M0, k0],
                    bounds=[(130, 500), (1, 300)],
                    method="L-BFGS-B")
                if best is None or res.fun < best.fun:
                    best = res
            except Exception:
                continue
    return best.x[0], best.x[1], gamma_fixed

# Fix gamma values that tell the story:
#   gamma=1.5 => no inflection, ceiling drifts high
#   gamma=0.65 => inflects at heavy loads, ceiling is pinned
gA = 1.5
gB = 0.65
gC = 1.0  # Brzycki baseline
MA, kA, gA = fit_Mk(gA)
MB, kB, gB = fit_Mk(gB)
MC, kC, gC = fit_Mk(gC)

print(f"Curve A (γ=1.5): M={MA:.0f}, k={kA:.1f}, γ={gA:.2f}")
print(f"Curve C (γ=1.0): M={MC:.0f}, k={kC:.1f}, γ={gC:.2f}  [Brzycki]")
print(f"Curve B (γ=0.65): M={MB:.0f}, k={kB:.1f}, γ={gB:.2f}")

# Weight ranges — each curve ends cleanly at its ceiling
W_A = np.linspace(60, 250, 500)
rA = fresh(W_A, MA, kA, gA)

W_C = np.linspace(60, MC - 1.0, 450)
rC = fresh(W_C, MC, kC, gC)

W_B = np.linspace(60, MB - 0.3, 400)
rB = fresh(W_B, MB, kB, gB)

# Set 3 prescription weights (r_fail = 6)
W3A = MA / (1 + (6 / kA) ** (1 / gA))
W3B = MB / (1 + (6 / kB) ** (1 / gB))
W3C = MC / (1 + (6 / kC) ** (1 / gC))

fig, ax = plt.subplots(figsize=(9, 5.5))

# Shade regions first (behind everything)
ax.axvspan(90, 130, alpha=0.08, color="green", zorder=0)
ax.axvspan(130, 250, alpha=0.06, color="orange", zorder=0)
ax.text(110, 33, "Observed\nregion", ha="center", fontsize=8, color="green",
        alpha=0.6, fontstyle="italic")
ax.text(190, 33, "Extrapolation zone", ha="center", fontsize=8, color="darkorange",
        alpha=0.6, fontstyle="italic")

# Curves — three gammas
ax.plot(W_A, rA, "-", color="#d62728", linewidth=2.5,
        label=f"γ = {gA:.1f}   →   M = {MA:.0f} lb   (ceiling unconstrained)")
ax.plot(W_C, rC, "--", color="#2ca02c", linewidth=2.0,
        label=f"γ = {gC:.1f}   →   M = {MC:.0f} lb   (Brzycki baseline)")
ax.plot(W_B, rB, "-", color="#1f77b4", linewidth=2.5,
        label=f"γ = {gB:.2f}  →   M = {MB:.0f} lb   (ceiling pinned by inflection)")

# Observed points (the two moderate-load sets)
ax.scatter([100, 120], [18, 12], s=130, color="black", zorder=5,
           edgecolors="white", linewidths=1.5)
ax.annotate("Set 1\n100 lb × 15 reps\nRPE 7  (r_fail ≈ 18)",
            (100, 18), textcoords="offset points", xytext=(15, 12),
            fontsize=8.5, ha="left", fontweight="bold",
            arrowprops=dict(arrowstyle="-", color="gray", lw=0.8))
ax.annotate("Set 2\n120 lb × 10 reps\nRPE 8  (r_fail ≈ 12)",
            (120, 12), textcoords="offset points", xytext=(15, 10),
            fontsize=8.5, ha="left", fontweight="bold",
            arrowprops=dict(arrowstyle="-", color="gray", lw=0.8))

# Set 3 prescription markers
r3A = fresh(np.array([W3A]), MA, kA, gA)[0]
r3B = fresh(np.array([W3B]), MB, kB, gB)[0]
r3C = fresh(np.array([W3C]), MC, kC, gC)[0]
ax.scatter([W3A], [r3A], s=110, color="#d62728", marker="D", zorder=5,
           edgecolors="white", linewidths=1.2)
ax.scatter([W3C], [r3C], s=110, color="#2ca02c", marker="D", zorder=5,
           edgecolors="white", linewidths=1.2)
ax.scatter([W3B], [r3B], s=110, color="#1f77b4", marker="D", zorder=5,
           edgecolors="white", linewidths=1.2)

# Set 3 labels — staggered vertically to avoid overlap
ax.annotate(f"Set 3: {W3B:.0f} lb",
            (W3B, r3B), textcoords="offset points", xytext=(-75, -15),
            fontsize=8.5, color="#1f77b4", fontweight="bold", ha="left",
            arrowprops=dict(arrowstyle="-", color="#1f77b4", lw=0.8),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                      edgecolor="#1f77b4", alpha=0.9))
ax.annotate(f"Set 3: {W3C:.0f} lb",
            (W3C, r3C), textcoords="offset points", xytext=(10, 18),
            fontsize=8.5, color="#2ca02c", fontweight="bold", ha="left",
            arrowprops=dict(arrowstyle="-", color="#2ca02c", lw=0.8),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                      edgecolor="#2ca02c", alpha=0.9))
ax.annotate(f"Set 3: {W3A:.0f} lb",
            (W3A, r3A), textcoords="offset points", xytext=(15, -18),
            fontsize=8.5, color="#d62728", fontweight="bold", ha="left",
            arrowprops=dict(arrowstyle="-", color="#d62728", lw=0.8),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                      edgecolor="#d62728", alpha=0.9))

# Blue ceiling
ax.axvline(MB, color="#1f77b4", linestyle=":", alpha=0.4, linewidth=1.2)
ax.text(MB + 3, 24, f"M = {MB:.0f}", fontsize=8.5, color="#1f77b4",
        fontweight="bold", ha="left",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#1f77b4", alpha=0.85))

# Green (Brzycki) ceiling
ax.axvline(MC, color="#2ca02c", linestyle=":", alpha=0.4, linewidth=1.2)
ax.text(MC - 3, 26, f"M = {MC:.0f}", fontsize=8.5, color="#2ca02c",
        fontweight="bold", ha="right",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#2ca02c", alpha=0.85))

# Red ceiling is off-screen
ax.text(249, 2.5, f"M = {MA:.0f} lb  →", fontsize=9,
        color="#d62728", ha="right", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                  edgecolor="#d62728", alpha=0.9))

# Big callout: the 1RM ambiguity
ax.text(0.98, 0.48,
        f"1RM ambiguity from\nsame two observations:\n"
        f"{MB:.0f} lb  (γ={gB})  vs  {MA:.0f} lb  (γ={gA})\n"
        f"Brzycki (γ=1): {MC:.0f} lb",
        transform=ax.transAxes, fontsize=10, fontweight="bold",
        ha="right", va="center",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="lightyellow",
                  edgecolor="gray", alpha=0.9))

ax.set_xlabel("Weight (lb)", fontsize=11)
ax.set_ylabel("Reps to failure", fontsize=11)
ax.set_title("Gamma degeneracy: both curves fit the same moderate-load observations\n"
             "but imply wildly different 1RM ceilings",
             fontsize=12, fontweight="bold")
ax.set_xlim(60, 255)
ax.set_ylim(-1, 37)
ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
ax.grid(True, alpha=0.2)

fig.tight_layout()
fig.savefig(r"C:\share\gamma_degeneracy.pdf", dpi=300, bbox_inches="tight")
fig.savefig(r"C:\share\gamma_degeneracy.png", dpi=180, bbox_inches="tight")
print(f"\nSaved to C:\\share\\gamma_degeneracy.pdf and .png")
print(f"Set 3 divergence: {abs(W3A - W3B):.0f} lb")
print(f"1RM divergence: {abs(MA - MB):.0f} lb")
