import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.database import create_db_and_tables
from app.routers.daily import router as daily_router
from app.routers.dashboard import router as dashboard_router
from app.routers.debug import ring_handler
from app.routers.debug import router as debug_router
from app.routers.exercises import router as exercises_router
from app.routers.foods import router as foods_router
from app.routers.macro_targets import router as macro_targets_router
from app.routers.meals import router as meals_router
from app.routers.parse import router as parse_router
from app.routers.recipes import router as recipes_router
from app.routers.routine import router as routine_router
from app.routers.tissue_readiness import router as tissue_readiness_router
from app.routers.tissues import router as tissues_router
from app.routers.training_model import router as training_model_router
from app.routers.workout_sessions import router as workout_sessions_router
from app.routers.workouts import router as workouts_router

# Configure parse logger to write to file
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_parse_logger = logging.getLogger("parse")
_parse_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_log_dir / "parse.log")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_parse_logger.addHandler(_fh)


# Attach ring buffer handler to root logger for remote log tailing
logging.getLogger().addHandler(ring_handler)
logging.getLogger().setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="Diet Tracker", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(foods_router)
app.include_router(macro_targets_router)
app.include_router(recipes_router)
app.include_router(meals_router)
app.include_router(daily_router)
app.include_router(dashboard_router)
app.include_router(parse_router)
app.include_router(debug_router)
app.include_router(workouts_router)
app.include_router(exercises_router)
app.include_router(workout_sessions_router)
app.include_router(tissues_router)
app.include_router(tissue_readiness_router)
app.include_router(training_model_router)
app.include_router(routine_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
