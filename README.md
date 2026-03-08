# Diet Tracker

A self-hosted diet and workout tracking app with an LLM-powered chat interface. Log meals by describing them in natural language, scan nutrition labels, track macros against daily targets, monitor body weight trends, and plan strength training with tissue-level recovery tracking.

## What It Does

**Diet tracking** -- Tell the chat what you ate ("two eggs, toast with butter, coffee with cream") and the LLM parses it into structured food items, matches against your database, and fills in nutrition data from USDA. Eight macros tracked per food: calories, fat, saturated fat, cholesterol, sodium, carbs, fiber, protein.

**Nutrition label scanning** -- Photograph a nutrition facts label; the LLM extracts all macro data via OCR and creates a food entry after you confirm.

**Macro targets and trends** -- Set daily macro goals via chat. The dashboard shows 7-day macro intake vs. targets with stacked bar charts, calorie breakdowns, and color-coded over/under indicators.

**Weight tracking** -- Log weight via chat or Apple Health sync. The dashboard shows a regression trend line over configurable time windows.

**Workout tracking** -- Log strength training sessions via chat. The system tracks exercises, sets, reps, weight, and maps each exercise to the specific muscles, tendons, and joints it loads. A tissue readiness dashboard shows recovery status based on per-tissue recovery times, and suggests which exercises are ready to train. An injury state machine tracks tissue conditions from healthy through injured, rehabbing, and back to healthy, with loading restrictions at each stage.

**Progressive overload** -- Rep completion tracking (full/partial/failed) drives weight increase suggestions. Two consecutive "full" sessions trigger a progression recommendation.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+ / FastAPI / SQLModel / SQLite |
| Frontend | React 19 / Vite / Tailwind CSS v4 / TypeScript |
| LLM | OpenRouter API (Claude Haiku default, switchable) |
| Food data | USDA FoodData Central API |
| Auth | Single-user password via signed cookies (itsdangerous) |
| Deploy | Docker Compose / nginx / GitHub Actions CI/CD / ghcr.io |
| Hosting | Any Linux VPS with Docker |

## Project Structure

```
backend/
  app/
    main.py              # FastAPI app + route registration
    models.py            # SQLModel table definitions (diet + workout)
    database.py          # Engine, session management, migrations, seeding
    config.py            # Pydantic settings (loads .env)
    auth.py              # Cookie-based auth
    macros.py            # 8-macro field definitions and helpers
    llm.py               # OpenRouter client, chat system prompt, tool dispatch
    usda.py              # USDA FoodData Central API client
    workout_tools.py     # 16 LLM chat tools for workout tracking
    workout_queries.py   # Log-table query helpers (tissue, conditions)
    seed_tissues.py      # 125-tissue musculoskeletal seed data
    macro_targets.py     # Macro target windowing logic
    routers/             # API route handlers
  tests/                 # pytest tests
  requirements.txt
  Dockerfile
frontend/
  src/
    pages/               # DashboardPage, MealLogPage, WorkoutPage, etc.
    components/          # Layout, shared UI
    api.ts               # Typed API client
  nginx.conf             # Reverse proxy config
  Dockerfile
tools/
  run_test_cycle.sh      # Lint + test + build validation
  diet-tracker-backup.sh # SQLite backup script for production
docker-compose.yml       # Development compose
docker-compose.prod.yml  # Production compose (pre-built images)
.github/workflows/
  ci.yml                 # PR checks: lint, test, build
  cd.yml                 # Push-to-main: build images, deploy to VPS
```

## Local Development

### Prerequisites

- Python 3.12+
- Node.js 22+
- An [OpenRouter](https://openrouter.ai/) API key

### Setup

```bash
# Clone
git clone https://github.com/addibble/diet-tracker.git
cd diet-tracker

# Configure environment
cp .env.example .env
# Edit .env: set APP_PASSWORD, SECRET_KEY, OPENROUTER_API_KEY

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..

# Frontend
cd frontend
npm ci
cd ..

# Enable pre-commit hooks
git config core.hooksPath .githooks
```

### Run Dev Servers

```bash
# Terminal 1: Backend (port 8000)
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload

# Terminal 2: Frontend (port 5173, proxies /api to 8000)
cd frontend
npm run dev
```

Open http://localhost:5173 and log in with your `APP_PASSWORD`.

### Run with Docker

```bash
docker compose up --build
```

This builds both images locally and starts them. The frontend serves on ports 80/443 with nginx proxying `/api` to the backend.

### Validation

Run the full test cycle before committing:

```bash
./tools/run_test_cycle.sh
```

This runs:
1. `ruff check` -- Python linting (line-length 100)
2. `pytest -v` -- Backend tests
3. `npm run build` -- Frontend TypeScript check + production build

The pre-commit hook runs this automatically and blocks commits on failure.

## Environment Variables

Create a `.env` file in the project root (see `.env.example`):

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_PASSWORD` | Login password for the web UI | `your-secure-password` |
| `SECRET_KEY` | Signing key for session cookies | `random-string-here` |
| `DATABASE_URL` | SQLite database path | `sqlite:///./data/diet_tracker.db` |
| `OPENROUTER_API_KEY` | API key from openrouter.ai | `sk-or-v1-...` |
| `LOGS_USER` | Username for remote log tailing | `logs` |
| `LOGS_PASSWORD` | Password for remote log tailing | `your-logs-password` |
| `APP_URL` | Public URL (for reference only) | `https://yourapp.example.com` |

## Production Deployment

### VPS Setup

Provision any Linux VPS with Docker and Docker Compose installed. Then:

```bash
# On the VPS
mkdir -p ~/diet-tracker/ssl

# Place your SSL certificate and key
cp fullchain.pem ~/diet-tracker/ssl/cert.pem
cp privkey.pem ~/diet-tracker/ssl/key.pem

# Create .env
cat > ~/diet-tracker/.env << 'EOF'
APP_PASSWORD=your-secure-password
SECRET_KEY=your-random-secret-key
DATABASE_URL=sqlite:///./data/diet_tracker.db
OPENROUTER_API_KEY=sk-or-v1-your-key
LOGS_USER=logs
LOGS_PASSWORD=your-logs-password
EOF
```

The SSL certs are mounted into the nginx container at `/etc/nginx/ssl/`. The nginx config redirects HTTP to HTTPS and proxies `/api/*` to the backend.

### GitHub Actions CI/CD

**CI** (`.github/workflows/ci.yml`) -- Runs on every pull request to `main`:
- Python lint with ruff
- Backend tests with pytest
- Frontend TypeScript check and production build

**CD** (`.github/workflows/cd.yml`) -- Runs on every push to `main`:
1. Builds Docker images for backend and frontend
2. Pushes to `ghcr.io/addibble/diet-tracker-backend:latest` and `ghcr.io/addibble/diet-tracker-frontend:latest`
3. SCPs `docker-compose.prod.yml` to the VPS
4. SSHs into the VPS, pulls new images, and restarts containers with zero downtime

**Required GitHub Secrets:**

| Secret | Description |
|--------|-------------|
| `DEPLOY_HOST` | VPS IP address or hostname |
| `DEPLOY_USER` | SSH username on the VPS |
| `DEPLOY_SSH_KEY` | Private SSH key for deployment |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for ghcr.io authentication.

### Manual Deploy

If you prefer to deploy without CI/CD:

```bash
# On the VPS
cd ~/diet-tracker
scp user@dev-machine:path/to/docker-compose.prod.yml docker-compose.yml
docker compose pull
docker compose up -d
```

## Database Backups

The backup script at `tools/diet-tracker-backup.sh` creates safe, non-blocking SQLite backups from the running container using Python's `sqlite3.backup()` API.

### Setup

```bash
# On the VPS
sudo cp tools/diet-tracker-backup.sh /usr/local/bin/diet-tracker-backup
sudo chmod +x /usr/local/bin/diet-tracker-backup

# Create backup directory
mkdir -p ~/backups/diet-tracker
```

### Schedule with Cron

```bash
crontab -e
```

Add a line for your desired schedule:

```cron
# Daily at 3:00 AM UTC
0 3 * * * /usr/local/bin/diet-tracker-backup >> /var/log/diet-tracker-backup.log 2>&1

# Or hourly
0 * * * * /usr/local/bin/diet-tracker-backup >> /var/log/diet-tracker-backup.log 2>&1
```

### What the Script Does

1. Finds the running backend container by Docker Compose labels
2. Runs `sqlite3.backup()` inside the container (non-blocking, no downtime)
3. Copies the backup to `~/backups/diet-tracker/diet_tracker_YYYYMMDDTHHMMSSZ.db.gz`
4. Compresses with gzip
5. Deletes backups older than 30 days
6. Uses a lock file to prevent overlapping runs

### Restore from Backup

```bash
# Stop the app
cd ~/diet-tracker && docker compose down

# Decompress and replace the database
gunzip -k ~/backups/diet-tracker/diet_tracker_20250308T030000Z.db.gz
docker volume inspect diet-tracker_db_data  # find the mount path
cp diet_tracker_20250308T030000Z.db /path/to/volume/diet_tracker.db

# Restart
docker compose up -d
```

## Importing Data

All data import happens through the chat interface. The LLM parses your data, shows a dry-run summary, and commits only after you confirm.

### Importing a Diet Log

Paste meal data into the chat with context about the format. Examples:

```
Here's what I ate yesterday:
Breakfast: 3 eggs scrambled, 2 slices wheat toast with butter, coffee with cream
Lunch: chicken caesar salad, about 300g
Dinner: salmon fillet 200g, rice 150g, steamed broccoli
```

The LLM will:
1. Parse each food item with estimated gram amounts
2. Match against your existing food database
3. Look up unknown foods via USDA
4. Present a structured breakdown for confirmation
5. Save the meal after you approve

For bulk historical import, paste spreadsheet data:

```
I want to import my food log from last week. Here's the data:
Date, Meal, Food, Grams
2025-03-01, breakfast, oatmeal, 250
2025-03-01, breakfast, banana, 120
2025-03-01, lunch, turkey sandwich, 350
...
```

### Importing a Workout Log

The workout system supports four spreadsheet formats for historical import. Paste the data with a note about the time period:

**Dated columns:**
```
This is my August 2025 workout data:
Exercise        SetsxReps    8/24/2025    8/25/2025
Incl-DB Press   3x4-6        35           40
Cable Fly       3x10-12      20           25
```

**Rounds with groups:**
```
This is my October 2025 workout data:
Exercise                    Sets x Reps    Group    Round 1    Round 2
Incline Dumbbell Press      4x6-8          Group 1  45         50
Cable Fly                   3x10-12        Group 1  20         25
Barbell Row                 4x6-8          Group 2  135        145
```

**Progressive rep range:**
```
This is my Dec 2025-Feb 2026 data:
Exercise        Group    12-15 Reps    11-14 Reps    10-13 Reps
Hammer Curl     0        25            30            30
```

For each import, the LLM will:
1. Detect the format
2. Parse exercises, weights, sets, and estimate dates
3. Show a dry-run summary (number of sessions, exercises, sets, new exercises needing tissue mappings)
4. Wait for your confirmation before committing
5. Create exercises, assign tissue mappings, and log all sessions

After import, use the chat to refine tissue mappings or adjust exercise details:

```
Set the tissue mappings for Incline DB Press to:
- pectoralis_major: primary, 1.0
- anterior_deltoid: secondary, 0.5
- triceps: secondary, 0.4
```

## Remote Log Tailing

Tail production backend logs from your local machine:

```bash
# Last 100 lines (default)
curl -u logs:$LOGS_PASSWORD $APP_URL/api/debug/logs

# Last 50 lines
curl -u logs:$LOGS_PASSWORD $APP_URL/api/debug/logs?lines=50

# Only errors
curl -u logs:$LOGS_PASSWORD $APP_URL/api/debug/logs?level=ERROR
```

## Development Practices

### Branching

Work on feature branches, not directly on `main`. Merge with fast-forward only:

```bash
git switch -c my-feature
# ... develop, commit ...
git switch main
git pull --ff-only origin main
git merge --ff-only my-feature
./tools/run_test_cycle.sh
git push origin main
```

### Code Conventions

- **Python**: Type hints everywhere. Ruff for linting and formatting (100-char line limit). No Alembic -- schema changes are applied at startup in `database.py`.
- **TypeScript**: Strict mode. All API types defined in `api.ts`. No external charting libraries -- SVG charts built inline.
- **API routes**: Prefixed with `/api`. All endpoints require auth except `/api/health` and `/api/auth/login`.
- **Macros**: The canonical 8-macro field list is defined in `backend/app/macros.py` (`MACRO_FIELDS`) and `frontend/src/api.ts` (`MACRO_KEYS`).
- **Log tables**: `tissue`, `exercise_tissue`, and `tissue_condition` are append-only. Rows are never updated -- new rows are inserted and queries use the latest per logical key.

### Testing

```bash
# Backend lint
cd backend && source .venv/bin/activate && ruff check app/ tests/

# Backend tests
pytest -v

# Frontend build (includes TypeScript type check)
cd frontend && npm run build
```

### Adding a New LLM Tool

1. Define the tool schema in `backend/app/workout_tools.py` (or a new tools file) following the OpenRouter function calling format
2. Write a handler function that takes `(args: dict, session: Session) -> dict`
3. Add to the `WORKOUT_TOOL_HANDLERS` dispatch map
4. The tool is automatically available to the LLM via `_all_chat_tools()` in `llm.py` and dispatched in `parse.py`

### Adding a New API Route

1. Create `backend/app/routers/your_route.py` following the pattern in existing routers
2. Import and register in `backend/app/main.py`
3. Add TypeScript interfaces and API functions to `frontend/src/api.ts`
