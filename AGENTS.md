# Diet Tracker

A database-backed diet tracking web app with LLM-powered meal parsing.

## Stack

- **Backend:** Python 3.12+ / FastAPI / SQLModel / SQLite
- **Frontend:** React / Vite / Tailwind CSS v4 / TypeScript
- **LLM:** OpenRouter API (Claude Haiku) for meal parsing + USDA FoodData Central for lookups
- **Deploy:** Docker + docker-compose / nginx / GitHub Actions / ghcr.io

## Project Structure

```
backend/           # FastAPI application
  app/
    main.py        # App entry point
    models.py      # SQLModel table definitions
    database.py    # Engine and session management
    config.py      # Pydantic settings
    auth.py        # Cookie-based auth
    macros.py      # Shared macro field definitions and helpers
    llm.py         # OpenRouter API client for meal parsing
    usda.py        # USDA FoodData Central API client
    routers/       # API route handlers (foods, recipes, meals, daily, parse)
  tests/           # pytest tests
  requirements.txt
  pyproject.toml
frontend/          # React + Vite application
  src/
    pages/         # Page components
    components/    # Shared components
    api.ts         # API client
docker-compose.yml
```

## Development Commands

### Development Cycle (Worktree-First)
Run this sequence for every development cycle.

```bash
# 0) One-time per worktree: enable versioned git hooks
git config core.hooksPath .githooks

# 1) Work on a branch (never develop directly on main)
# Example:
# git switch -c <feature-branch>

# 2) Start clean and up to date
git fetch origin main
git rebase origin/main
# Resolve any rebase conflicts before writing code.

# 3) Fresh worktree setup (required when not on main, and recommended always)
# Backend: per-worktree virtualenv
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..

# Frontend: install tools/deps for this worktree
cd frontend
npm ci
cd ..
```

At the end of each cycle, run validation, then commit locally.

```bash
# Validation before check-in (same checks used in CI)
./tools/run_test_cycle.sh

# Local check-in
git add -A
git commit -m "Describe the completed cycle"
# pre-commit hook runs ./tools/run_test_cycle.sh automatically
```

### Integration Cycle (Merge to Main, Then Push Main)
When a branch is ready, integrate it into local `main`, then push `main`.

```bash
# 1) Ensure branch is up to date and committed
git fetch origin main
git rebase origin/main
./tools/run_test_cycle.sh
git add -A
git commit -m "Final branch updates"   # if there are unstaged changes

# 2) Merge branch into local main
git switch main
git pull --ff-only origin main
git merge --ff-only <feature-branch>

# 3) Validate on main and push main
./tools/run_test_cycle.sh
git push origin main
```

If `git merge --ff-only` fails, rebase the branch on latest `origin/main`, resolve conflicts, re-run the test cycle, and retry.

### Backend
```bash
cd backend
# Create/refresh venv in this worktree
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload              # Run dev server (port 8000)
pytest                                      # Run tests
pytest -x -v                               # Run tests, stop on first failure
ruff check app/ tests/                     # Lint
ruff format app/ tests/                    # Format
```

### Frontend
```bash
cd frontend
# Prefer npm ci for reproducible installs in fresh worktrees
npm ci
npm run dev                                # Dev server (port 5173, proxies /api to 8000)
npm run build                              # Production build
npm run lint                               # Lint
```

### Docker
```bash
docker compose up --build                  # Build and run everything
docker compose down                        # Stop
docker compose logs -f backend             # Tail backend logs
```

## Pre-commit Requirements

**Before staging, committing, or pushing**, always run these checks and confirm they pass.
If working on a branch/worktree, first rebase on latest `origin/main` and resolve conflicts:

```bash
git fetch origin main
git rebase origin/main
```

Then run:

```bash
./tools/run_test_cycle.sh
```

All tests must pass and there must be no lint or build errors before committing. Do not skip this step.

Commit locally at the end of each development cycle. Integration should happen by merging into local `main` and pushing `main`; avoid direct feature development on `main`.

### Hook Setup

Use versioned hooks in each worktree:

```bash
git config core.hooksPath .githooks
```

The repository pre-commit hook runs `./tools/run_test_cycle.sh` on every `git commit` and blocks the commit on failures.

## Agent Validation Checklist

For any new feature, bug fix, or refactor, agents must run the same checks as `.github/workflows/ci.yml` before handoff:

```bash
cd backend && source .venv/bin/activate && ruff check app/ tests/
cd backend && source .venv/bin/activate && pytest -v
cd frontend && npm ci && npm run build
```

## Lessons Learned

### Python 3.14 + Pydantic: field name shadowing type imports
If a Pydantic model field has the same name as its type import (e.g., `date: date | None = None`), Python 3.14's annotation evaluation resolves the field name to its default value (`None`) instead of the type. Fix by qualifying the type: `date: datetime.date | None = None` (with `import datetime`).

### JSX conditional rendering must use parentheses for multi-line blocks
`{condition && <div>...</div>}` fails to parse when the JSX spans multiple lines. Always wrap in parentheses: `{condition && (<div>...</div>)}`.

### Tool definition dicts: watch line length
Inline JSON-style tool definitions (OpenRouter/OpenAI function calling format) easily exceed the 100-char ruff limit. Break long description strings into parenthesized multi-line strings, and split property dicts across lines.

## Conventions

- Python: type hints everywhere, ruff for linting/formatting (line-length 100)
- API routes prefixed with `/api`
- Auth: all `/api` endpoints require auth except `/api/health`, `/api/auth/login`, and `/api/debug/logs` (HTTP Basic Auth)
- 8 macros tracked: calories, fat, saturated_fat, cholesterol, sodium, carbs, fiber, protein
- Macros stored **per serving** with `serving_size_grams` on each food; scale for actual amounts
- Macro field list defined in `backend/app/macros.py` (MACRO_FIELDS) and `frontend/src/api.ts` (MACRO_KEYS)
- `POST /api/meals/parse` — LLM parses meal description → matches DB foods → USDA lookup for unknowns
- Frontend uses fetch with credentials: "include" for cookie auth
- SQLite DB persisted via Docker volume at `/app/data/diet_tracker.db`

## Production

- **Deploy:** Push to `main` triggers GitHub Actions CD → builds images → pushes to ghcr.io → deploys to VPS
- **VPS compose:** `docker-compose.prod.yml` is synced to VPS during deploy

### Remote Log Tailing

Tail production backend logs from your local machine:

Find the password as APP_PASSWORD in .env
Find the URL as APP_URL in .env

```bash
# Last 100 lines (default)
curl -u logs:$APP_PASSWORD $APP_URL/api/debug/logs

# Last 50 lines
curl -u logs:$APP_PASSWORD $APP_URL/api/debug/logs?lines=50

# Only errors
curl -u logs:$APP_PASSWORD $APP_URL/api/debug/logs?level=ERROR
```
