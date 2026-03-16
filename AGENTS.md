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
    llm.py         # OpenRouter API client, chat loop, tool dispatch
    usda.py        # USDA FoodData Central API client
    llm_tools/     # Table-driven LLM tool system (22 tools)
      __init__.py  # Tool registries, domain-family selection
      shared.py    # Fuzzy matching, filters, response builders
      nutrition.py # 10 tools: foods, recipes, meal_logs, weight_logs, macro_targets
      workout.py   # 12 tools: exercises, tissues, tissue_conditions, etc.
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

At the end of each cycle, commit locally. The pre-commit hook runs `./tools/run_test_cycle.sh` automatically and blocks on failure.

```bash
git add -A
git commit -m "Describe the completed cycle"
```

### Integration Cycle (Merge to Main, Then Push Main)
When a branch is ready, integrate it into local `main`, then push `main`.

```bash
# 1) Ensure branch is up to date and committed
git fetch origin main
git rebase origin/main
git add -A
git commit -m "Final branch updates"   # if there are unstaged changes; hook validates

# 2) Merge branch into local main
git switch main
git pull --ff-only origin main
git merge --ff-only <feature-branch>

# 3) Push main
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

## Pre-commit Hook

The repository pre-commit hook (`.githooks/pre-commit`) runs `./tools/run_test_cycle.sh` on every `git commit` and blocks on failures. This covers backend lint, backend tests, and frontend build — the same checks as CI.

Enable it once per worktree:

```bash
git config core.hooksPath .githooks
```

Do not use `--no-verify` to skip the hook. Commit locally at the end of each development cycle; avoid direct feature development on `main`.

## LLM Tool System

The backend exposes 22 tools (11 get/set pairs) to the LLM via OpenRouter function calling. All tools follow a table-driven contract inspired by SQL and JSON:API.

### Tool naming

Every tool is `get_<table>` or `set_<table>`, where `<table>` matches the SQLModel table name (e.g., `get_foods`, `set_meal_logs`, `get_workout_sessions`).

### Getter contract

```json
{
  "filters": { "<field>": { "<op>": <value> } },
  "include": ["<relation>"],
  "sort": [{ "field": "<col>", "direction": "asc|desc" }],
  "limit": 25,
  "offset": 0
}
```

Filter operators: `eq`, `in`, `gte`, `lte`, `gt`, `lt`, `contains`, `is_null`, `fuzzy`. Fuzzy matching uses `difflib.SequenceMatcher` post-SQL with a 0.60 threshold.

### Setter contract

```json
{
  "changes": [{
    "operation": "create|update|upsert|delete",
    "match": { "<field>": { "<op>": <value> } },
    "set": { "<field>": <value> },
    "relations": { "<relation>": [...] }
  }],
  "dry_run": false
}
```

### Response envelopes

- **Getter:** `{ "table", "count", "matches", "filters_applied?", "match_info?", "warnings?" }`
- **Setter:** `{ "table", "operation", "matched_count", "changed_count", "created_count", "deleted_count", "matches", "warnings?" }`
- **Error:** `{ "table", "error", "details?" }`

### Domain-family tool selection

`select_tools(messages)` in `llm_tools/__init__.py` inspects the latest user message with regex patterns to route:
- Workout keywords → 12 workout tools only
- Nutrition keywords → 10 nutrition tools only
- Mixed/ambiguous → all 22 tools

### Adding a new tool

1. Define the tool schema dict and handler function in `nutrition.py` or `workout.py`
2. Add to the module's `*_TOOL_DEFINITIONS` list and `*_TOOL_HANDLERS` dict
3. The tool is automatically registered in `ALL_TOOL_DEFINITIONS` and `TOOL_HANDLERS`
4. Update domain regex patterns in `__init__.py` if the new tool covers new vocabulary
5. The tool executor in `parse.py` dispatches automatically via `TOOL_HANDLERS[name]`

### Shared utilities (`llm_tools/shared.py`)

- `fuzzy_score()` / `fuzzy_best()` — SequenceMatcher-based fuzzy matching
- `apply_filters()` — SQL WHERE clause builder with fuzzy spec extraction
- `apply_fuzzy_post_filter()` — post-query fuzzy scoring
- `resolve_match()` — resolve a setter `match` clause to DB records
- `getter_response()` / `setter_response()` / `error_response()` — response envelope builders
- `record_to_dict()` — SQLModel record → plain dict with date serialization

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

### Live Testing with Chrome DevTools MCP

After pushing to `main`, GitHub Actions deploys to the production VPS (~2 minutes). Use Chrome DevTools MCP to verify features on the live app.

**Setup:** Add the Chrome DevTools MCP server to your agent configuration:
```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    }
  }
}
```

**Testing workflow:**

1. Push to `main` and wait ~2 minutes for deploy
2. Navigate to the app URL (from `APP_URL` in `.env`)
3. Confirm the deploy landed by checking the git hash slug in the navbar
4. Install error listeners before interacting with the page:
   ```js
   window.__capturedErrors = [];
   window.addEventListener('error', (e) => {
     window.__capturedErrors.push({
       message: e.message, filename: e.filename,
       lineno: e.lineno, stack: e.error?.stack
     });
   });
   window.addEventListener('unhandledrejection', (e) => {
     window.__capturedErrors.push({
       message: e.reason?.message || String(e.reason),
       stack: e.reason?.stack
     });
   });
   ```
5. Log in using the password from `APP_PASSWORD` in `.env` (single password field, submit button)
6. **Re-install error listeners after login** — the page navigates, clearing listeners
7. Exercise the feature under test (click buttons, fill forms, etc.)
8. Check `window.__capturedErrors` for any JS errors
9. Take screenshots to visually verify UI state

**Tips:**
- React input fields need the native value setter pattern to trigger state updates:
  ```js
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value'
  ).set;
  setter.call(input, 'value');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  ```
- After navigation (login, page switch), error listeners are lost — always reinstall
- Use `document.querySelectorAll('button')` to discover interactive elements
- The minified bundle uses short names; match errors to source by searching for property names (e.g., `.rows`, `.items`) in the component code
