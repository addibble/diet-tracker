"""Load production SQLite DB and provide query helpers for model exploration."""

import sqlite3
from pathlib import Path
from dataclasses import dataclass

DB_PATH = Path(__file__).parent / "production.db"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@dataclass
class SetRecord:
    """A single workout set with all fields needed for model fitting."""
    set_id: int
    session_id: int
    exercise_id: int
    exercise_name: str
    equipment: str | None
    set_order: int
    reps: int | None
    weight: float | None
    rpe: float | None
    rep_completion: str | None
    performed_side: str | None
    duration_secs: int | None
    session_date: str  # ISO date string
    # Bodyweight context
    load_input_mode: str
    bodyweight_fraction: float
    external_load_multiplier: float


def load_all_sets(conn: sqlite3.Connection) -> list[SetRecord]:
    """Load all workout sets joined with exercise and session info."""
    rows = conn.execute("""
        SELECT
            ws.id as set_id,
            ws.session_id,
            ws.exercise_id,
            e.name as exercise_name,
            e.equipment,
            ws.set_order,
            ws.reps,
            ws.weight,
            ws.rpe,
            ws.rep_completion,
            ws.performed_side,
            ws.duration_secs,
            wk.date as session_date,
            e.load_input_mode,
            e.bodyweight_fraction,
            e.external_load_multiplier
        FROM workout_sets ws
        JOIN exercises e ON ws.exercise_id = e.id
        JOIN workout_sessions wk ON ws.session_id = wk.id
        ORDER BY wk.date, ws.session_id, ws.set_order
    """).fetchall()
    return [SetRecord(**dict(r)) for r in rows]


def load_bodyweight_history(conn: sqlite3.Connection) -> dict[str, float]:
    """Load weight logs as {date_str: weight_lb}."""
    rows = conn.execute("""
        SELECT date(logged_at) as d, weight_lb
        FROM weight_logs
        ORDER BY logged_at
    """).fetchall()
    return {r["d"]: r["weight_lb"] for r in rows}


def load_exercises(conn: sqlite3.Connection) -> list[dict]:
    """Load all exercises."""
    rows = conn.execute("SELECT * FROM exercises ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def load_exercise_tissues(conn: sqlite3.Connection) -> list[dict]:
    """Load exercise-tissue mappings."""
    rows = conn.execute("""
        SELECT et.*, t.name as tissue_name, t.type as tissue_type, t.region
        FROM exercise_tissues et
        JOIN tissues t ON et.tissue_id = t.id
        ORDER BY et.exercise_id, et.role
    """).fetchall()
    return [dict(r) for r in rows]


def load_tissue_conditions(conn: sqlite3.Connection) -> list[dict]:
    """Load tissue condition history."""
    rows = conn.execute("""
        SELECT tc.*, t.name as tissue_name
        FROM tissue_conditions tc
        JOIN tissues t ON tc.tissue_id = t.id
        ORDER BY tc.updated_at
    """).fetchall()
    return [dict(r) for r in rows]


def effective_weight(
    set_rec: SetRecord,
    bodyweight: float | None,
) -> float | None:
    """Compute effective load for a set, accounting for bodyweight exercises."""
    mode = set_rec.load_input_mode or "external_weight"
    ext = (set_rec.weight or 0.0) * set_rec.external_load_multiplier
    bw_component = (bodyweight or 0.0) * set_rec.bodyweight_fraction

    if mode == "external_weight":
        return ext if ext > 0 else None
    elif mode == "bodyweight":
        return bw_component if bw_component > 0 else None
    elif mode == "mixed":
        total = ext + bw_component
        return total if total > 0 else None
    elif mode == "assisted_bodyweight":
        total = max(0, bw_component - ext)
        return total if total > 0 else None
    elif mode == "carry":
        return ext if ext > 0 else None
    else:
        return ext if ext > 0 else None


def nearest_bodyweight(bw_history: dict[str, float], date_str: str) -> float | None:
    """Find the closest bodyweight measurement on or before the given date."""
    if not bw_history:
        return None
    candidates = [(d, w) for d, w in bw_history.items() if d <= date_str]
    if not candidates:
        # Fall back to earliest measurement
        earliest = min(bw_history.keys())
        return bw_history[earliest]
    return max(candidates, key=lambda x: x[0])[1]
