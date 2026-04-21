#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "[test-cycle] backend lint"
(
  cd "$REPO_ROOT/backend"
  if [[ ! -f ".venv/bin/activate" ]]; then
    echo "Missing backend virtualenv at backend/.venv."
    echo "Run: cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
  fi
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
  ruff check app/ tests/
)

echo "[test-cycle] weight/rep-space guard"
(
  cd "$REPO_ROOT"
  # Forbid inline weight/rep-space arithmetic outside the central units
  # modules. Call sites should go through app.units (backend) or
  # src/lib/units.ts (frontend) so space conversions stay auditable.
  #
  # Patterns flagged:
  #   reps + rir / rtf - rir / r_fail - rir / <anything>.reps + rir
  #   10 - rpe / 10.0 - ws.rpe / 10 - target_rpe
  # Allow-listed files: units.py (backend canonical), units.ts (frontend
  # canonical), exercise_loads.py (low-level weight math, wrapped by units).
  py_hits=$(grep -rEn \
      --include='*.py' \
      --exclude='units.py' \
      --exclude='exercise_loads.py' \
      -- \
      '(\breps\s*\+\s*rir\b|\brtf\s*-\s*rir\b|\br_fail\s*-\s*rir\b|\b10(\.0?)?\s*-\s*\w*rpe\b)' \
      backend/app/ || true)
  if [[ -n "$py_hits" ]]; then
    echo "Raw weight/rep-space math outside backend/app/units.py:"
    echo "$py_hits"
    echo "Use app.units helpers (rpe_to_rir, reps_done_to_rtf, rtf_to_reps_done)."
    exit 1
  fi
  ts_hits=$(grep -rEn \
      --include='*.ts' --include='*.tsx' \
      --exclude='units.ts' \
      -- \
      '(\breps\s*\+\s*rir\b|\brtf\s*-\s*rir\b|\b10\s*-\s*\w*[Rr]pe\b|\b10\s*-\s*\w*[Rr]ir\b)' \
      frontend/src/ || true)
  if [[ -n "$ts_hits" ]]; then
    echo "Raw weight/rep-space math outside frontend/src/lib/units.ts:"
    echo "$ts_hits"
    echo "Use units.ts helpers (rpeToRir, repsDoneToRtf, rtfToRepsDone)."
    exit 1
  fi
)

echo "[test-cycle] backend tests"
(
  cd "$REPO_ROOT/backend"
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
  pytest -v
)

echo "[test-cycle] frontend build"
(
  cd "$REPO_ROOT/frontend"
  npm ci
  npm run build
)

echo "[test-cycle] all checks passed"
