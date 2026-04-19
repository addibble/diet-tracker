"""Single entrypoint for the refreshed model exploration.

Runs, in order:
  1. features.py    -- compute per-set freshness and within-session features
  2. residuals.py   -- build leakage-free residuals vs fresh curve
  3. set1_freshness.py -- Set-1 accuracy vs freshness features
  4. fatigue_decomposition.py -- 2-compartment fatigue model (session + local)

Outputs:
  plots/set1_freshness/*.png
  plots/fatigue/*.png
  findings.txt (captured stdout of the analysis)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")


def main():
    t0 = time.time()
    print("=" * 80)
    print("DIET-TRACKER MODEL EXPLORATION -- REFRESH")
    print("=" * 80)
    print()

    from residuals import build_residual_table
    rows = build_residual_table()
    print(f"\nResidual table: {len(rows)} RPE sets\n")

    from set1_freshness import analyze_set1
    print("\n" + "#" * 80)
    print("# SET-1 FRESHNESS ANALYSIS")
    print("#" * 80)
    analyze_set1(rows)

    from fatigue_decomposition import analyze_fatigue
    print("\n" + "#" * 80)
    print("# FATIGUE DECOMPOSITION")
    print("#" * 80)
    analyze_fatigue(rows)

    dt = time.time() - t0
    print(f"\nDone in {dt:.1f}s")


if __name__ == "__main__":
    main()
