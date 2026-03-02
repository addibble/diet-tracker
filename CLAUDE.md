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

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
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
npm install
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

## Git Workflow

- **Default branch is `main`** — never target or push directly to `master`
- Feature branches must be named `claude/<description>-<session-id>`
- **Always run lint and tests before committing or pushing:**
  ```bash
  # Frontend — must pass with zero errors before any commit
  cd frontend && npm install && npm run lint

  # Backend — must pass before any commit
  cd backend && ruff check app/ tests/ && pytest
  ```
- Merge feature branches into `main` via pull request (direct push to `main` is blocked)

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

- **URL:** https://diettracker.kndyman.com/
- **Deploy:** Push to `main` triggers GitHub Actions CD → builds images → pushes to ghcr.io → deploys to VPS
- **VPS compose:** `docker-compose.prod.yml` is synced to VPS during deploy

### Remote Log Tailing

Tail production backend logs from your local machine:

```bash
# Last 100 lines (default)
curl -u logs:iemeM5ja https://diettracker.kndyman.com/api/debug/logs

# Last 50 lines
curl -u logs:iemeM5ja https://diettracker.kndyman.com/api/debug/logs?lines=50

# Only errors
curl -u logs:iemeM5ja https://diettracker.kndyman.com/api/debug/logs?level=ERROR
```
