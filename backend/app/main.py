import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import run_bootstrap
from app.database import _wire_auth_dep
from app.routers.admin import router as admin_router
from app.routers.daily import router as daily_router
from app.routers.dashboard import router as dashboard_router
from app.routers.database import router as database_router
from app.routers.debug import ring_handler
from app.routers.debug import router as debug_router
from app.routers.exercises import router as exercises_router
from app.routers.food_search import router as food_search_router
from app.routers.foods import router as foods_router
from app.routers.macro_targets import router as macro_targets_router
from app.routers.meal_items import router as meal_items_router
from app.routers.meals import router as meals_router
from app.routers.parse import router as parse_router
from app.routers.planner import router as planner_router
from app.routers.recipes import router as recipes_router
from app.routers.tissues import router as tissues_router
from app.routers.webauthn import router as webauthn_router
from app.routers.workout_sessions import router as workout_sessions_router
from app.routers.workout_sets import router as workout_sets_router
from app.routers.workouts import router as workouts_router

# Wire the auth dependency onto get_session now that all modules are loaded.
_wire_auth_dep()

# Configure parse logger to write to file
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_parse_logger = logging.getLogger("parse")
_parse_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_log_dir / "parse.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_parse_logger.addHandler(_fh)


# Attach ring buffer handler to root logger for remote log tailing,
# and a stdout stream handler so app logs show up in `docker compose logs`.
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
)
logging.getLogger().addHandler(_console_handler)
logging.getLogger().addHandler(ring_handler)
logging.getLogger().setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_bootstrap()
    yield


app = FastAPI(title="Diet Tracker", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webauthn_router)
app.include_router(admin_router)
app.include_router(database_router)
app.include_router(food_search_router)
app.include_router(foods_router)
app.include_router(macro_targets_router)
app.include_router(recipes_router)
app.include_router(meals_router)
app.include_router(meal_items_router)
app.include_router(daily_router)
app.include_router(dashboard_router)
app.include_router(parse_router)
app.include_router(debug_router)
app.include_router(workouts_router)
app.include_router(exercises_router)
app.include_router(workout_sessions_router)
app.include_router(workout_sets_router)
app.include_router(tissues_router)
app.include_router(planner_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
