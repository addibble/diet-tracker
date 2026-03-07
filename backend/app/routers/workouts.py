"""Workout import and retrieval endpoints."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.config import settings
from app.database import get_session
from app.models import Workout

router = APIRouter(prefix="/api/workouts", tags=["workouts"])


def _require_auth(request: Request, session: Session = Depends(get_session)) -> str:
    """Accept either a Bearer API token or the normal session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if settings.api_token and token == settings.api_token:
            return "api"
        raise HTTPException(status_code=401, detail="Invalid API token")
    # Fall back to cookie-based auth
    return get_current_user(request)


class WorkoutIn(BaseModel):
    sync_key: str
    date: str           # YYYY-MM-DD
    workout_type: str
    duration_minutes: float
    active_calories: float
    total_calories: float | None = None
    distance_km: float | None = None
    source: str | None = None


class WorkoutOut(BaseModel):
    id: int
    sync_key: str
    date: str
    workout_type: str
    duration_minutes: float
    active_calories: float
    total_calories: float | None
    distance_km: float | None
    source: str | None


def _to_out(w: Workout) -> dict:
    return {
        "id": w.id,
        "sync_key": w.sync_key,
        "date": str(w.date),
        "workout_type": w.workout_type,
        "duration_minutes": round(w.duration_minutes, 1),
        "active_calories": round(w.active_calories, 1),
        "total_calories": round(w.total_calories, 1) if w.total_calories else None,
        "distance_km": round(w.distance_km, 2) if w.distance_km else None,
        "source": w.source,
    }


@router.post("", status_code=200)
def upsert_workouts(
    workouts: list[WorkoutIn],
    session: Session = Depends(get_session),
    _user: str = Depends(_require_auth),
):
    """Upsert a batch of workouts. Idempotent — safe to re-run the Shortcut."""
    created = 0
    updated = 0
    for w in workouts:
        try:
            workout_date = date.fromisoformat(w.date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid date: {w.date!r}"
            )
        existing = session.exec(
            select(Workout).where(Workout.sync_key == w.sync_key)
        ).first()
        if existing:
            existing.workout_type = w.workout_type
            existing.duration_minutes = w.duration_minutes
            existing.active_calories = w.active_calories
            existing.total_calories = w.total_calories
            existing.distance_km = w.distance_km
            existing.source = w.source
            session.add(existing)
            updated += 1
        else:
            session.add(Workout(
                sync_key=w.sync_key,
                date=workout_date,
                workout_type=w.workout_type,
                duration_minutes=w.duration_minutes,
                active_calories=w.active_calories,
                total_calories=w.total_calories,
                distance_km=w.distance_km,
                source=w.source,
            ))
            created += 1
    session.commit()
    return {"created": created, "updated": updated}


@router.get("")
def list_workouts(
    date: date | None = None,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """List workouts, optionally filtered by date."""
    stmt = select(Workout).order_by(Workout.date.desc())  # type: ignore[union-attr]
    if date:
        stmt = stmt.where(Workout.date == date)
    workouts = session.exec(stmt).all()
    return [_to_out(w) for w in workouts]
