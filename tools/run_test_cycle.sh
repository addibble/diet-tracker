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
