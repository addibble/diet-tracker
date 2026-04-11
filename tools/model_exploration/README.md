# Model Exploration — Unified Fatigue-Strength Model

Standalone exploration of the three-layer model from `\share\weightliftingmodel.tex`
against production workout data.

## Quick Start

```bash
cd tools/model_exploration
python run_exploration.py          # run all phases
python run_exploration.py --phase 2  # start from phase 2
python data_audit.py               # phase 1 only
python strength_curve.py           # phase 2 only
python session_fatigue.py          # phase 3 only
python strength_evolution.py       # phase 4 only
```

## Requirements

- Python 3.12+
- numpy, scipy, matplotlib
- Production database: `production.db` (in this directory)

## Files

| File | Purpose |
|---|---|
| `data_loader.py` | SQLite connection, query helpers, effective weight calculation |
| `data_audit.py` | Phase 1: exercise inventory, RPE coverage, tier assignments |
| `strength_curve.py` | Phase 2: fit `r_fresh(W) = k(M/W−1)^γ` per exercise |
| `session_fatigue.py` | Phase 3: dose calc, scalar fatigue, session replay |
| `strength_evolution.py` | Phase 4: M(t) time series, recovery%, drop detection |
| `full_validation.py` | Phase 5: end-to-end validation |
| `visualize.py` | Plotting utilities |
| `run_exploration.py` | Main runner (all phases) |

## Data Summary

- **70 exercises**, 3,959 total sets, 521 with RPE, 44 bodyweight logs
- **Tier 1** (full fit): 32 exercises — ≥8 RPE sets AND ≥2 distinct weights
- **Tier 2** (partial): 33 exercises — ≥5 RPE or ≥15 total sets
- **Tier 3** (priors): 3 exercises — insufficient data
- RPE distribution peaks at 7-8 (training sweet spot)
- Completion labels: 1,799 full / 1,510 partial / 606 failed

## Plots

Generated in `plots/`:
- `curve_gallery.png` — top 20 exercises by data volume
- `curve_<exercise>.png` — individual strength curves with observations
