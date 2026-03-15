import datetime
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.exercise_loads import bodyweight_by_date, effective_set_load, effective_weight
from app.models import (
    Exercise,
    ExerciseTissue,
    RecoveryCheckIn,
    Tissue,
    TrainingExclusionWindow,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.training_model import (
    build_exercise_risk_ranking,
    build_exercise_strength,
    build_tissue_history,
    build_training_model_summary,
    list_exclusion_windows,
)

router = APIRouter(prefix="/api/training-model", tags=["training-model"])


class ExclusionWindowCreate(BaseModel):
    start_date: datetime.date
    end_date: datetime.date
    kind: str
    notes: str | None = None
    exclude_from_model: bool = True


@router.get("/summary")
def get_summary(
    as_of: datetime.date | None = Query(default=None),
    include_exercises: bool = Query(default=False),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return build_training_model_summary(session, as_of=as_of, include_exercises=include_exercises)


@router.get("/exercises")
def get_exercise_risk_ranking(
    as_of: datetime.date | None = Query(default=None),
    sort_by: str = Query(default="risk_7d", pattern="^(risk_7d|risk_14d|suitability|normalized_load)$"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    recommendation: str | None = Query(default=None, pattern="^(avoid|caution|good)$"),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    rows = build_exercise_risk_ranking(
        session,
        as_of=as_of,
        sort_by=sort_by,
        descending=direction == "desc",
        limit=None,
    )
    if recommendation:
        rows = [row for row in rows if row["recommendation"] == recommendation]
    return rows[:limit]


@router.get("/tissues/{tissue_id}/history")
def get_tissue_model_history(
    tissue_id: int,
    days: int = Query(default=90, ge=7, le=365),
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    return build_tissue_history(session, tissue_id, as_of=as_of, days=days)


@router.get("/exercises/{exercise_id}/strength")
def get_exercise_strength(
    exercise_id: int,
    days: int = Query(default=90, ge=7, le=365),
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    try:
        return build_exercise_strength(session, exercise_id, as_of=as_of, days=days)
    except KeyError:
        raise HTTPException(status_code=404, detail="Exercise not found")


@router.get("/exclusion-windows")
def get_exclusion_windows(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return [
        {
            "id": row.id,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
            "kind": row.kind,
            "notes": row.notes,
            "exclude_from_model": row.exclude_from_model,
        }
        for row in list_exclusion_windows(session)
    ]


@router.post("/exclusion-windows", status_code=201)
def create_exclusion_window(
    data: ExclusionWindowCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    row = TrainingExclusionWindow(
        start_date=data.start_date,
        end_date=data.end_date,
        kind=data.kind,
        notes=data.notes,
        exclude_from_model=data.exclude_from_model,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "id": row.id,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "kind": row.kind,
        "notes": row.notes,
        "exclude_from_model": row.exclude_from_model,
    }


@router.delete("/exclusion-windows/{window_id}", status_code=204)
def delete_exclusion_window(
    window_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    row = session.get(TrainingExclusionWindow, window_id)
    if not row:
        raise HTTPException(status_code=404, detail="Exclusion window not found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


# ── Recovery Check-In Endpoints ──


class RecoveryCheckInCreate(BaseModel):
    date: datetime.date
    region: str
    soreness_0_10: int = 0
    pain_0_10: int = 0
    stiffness_0_10: int = 0
    readiness_0_10: int = 5
    notes: str | None = None


@router.post("/check-in", status_code=201)
def create_check_in(
    data: RecoveryCheckInCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    # Upsert: update existing check-in for same date+region if one exists
    existing = session.exec(
        select(RecoveryCheckIn)
        .where(RecoveryCheckIn.date == data.date, RecoveryCheckIn.region == data.region)
        .order_by(RecoveryCheckIn.id.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()

    if existing:
        existing.soreness_0_10 = data.soreness_0_10
        existing.pain_0_10 = data.pain_0_10
        existing.stiffness_0_10 = data.stiffness_0_10
        existing.readiness_0_10 = data.readiness_0_10
        existing.notes = data.notes
        row = existing
    else:
        row = RecoveryCheckIn(
            date=data.date,
            region=data.region,
            soreness_0_10=data.soreness_0_10,
            pain_0_10=data.pain_0_10,
            stiffness_0_10=data.stiffness_0_10,
            readiness_0_10=data.readiness_0_10,
            notes=data.notes,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "id": row.id,
        "date": row.date.isoformat(),
        "region": row.region,
        "soreness_0_10": row.soreness_0_10,
        "pain_0_10": row.pain_0_10,
        "stiffness_0_10": row.stiffness_0_10,
        "readiness_0_10": row.readiness_0_10,
        "notes": row.notes,
    }


@router.get("/check-ins")
def get_check_ins(
    date: datetime.date | None = Query(default=None),
    start_date: datetime.date | None = Query(default=None),
    end_date: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(RecoveryCheckIn)
    if date is not None:
        stmt = stmt.where(RecoveryCheckIn.date == date)
    else:
        if start_date is not None:
            stmt = stmt.where(col(RecoveryCheckIn.date) >= start_date)
        if end_date is not None:
            stmt = stmt.where(col(RecoveryCheckIn.date) <= end_date)
        if start_date is None and end_date is None:
            import datetime as dt_mod

            today = dt_mod.date.today()
            stmt = stmt.where(RecoveryCheckIn.date == today)
    stmt = stmt.order_by(col(RecoveryCheckIn.date).desc(), col(RecoveryCheckIn.created_at).desc())
    rows = session.exec(stmt).all()
    return [
        {
            "id": row.id,
            "date": row.date.isoformat(),
            "region": row.region,
            "soreness_0_10": row.soreness_0_10,
            "pain_0_10": row.pain_0_10,
            "stiffness_0_10": row.stiffness_0_10,
            "readiness_0_10": row.readiness_0_10,
            "notes": row.notes,
        }
        for row in rows
    ]


@router.get("/regions")
def get_regions(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissues = session.exec(select(Tissue).order_by(Tissue.name)).all()
    regions: dict[str, list[dict]] = defaultdict(list)
    for tissue in tissues:
        regions[tissue.region].append({
            "id": tissue.id,
            "name": tissue.name,
            "display_name": tissue.display_name,
            "type": tissue.type,
        })
    return [
        {"region": region, "tissues": tissues_list}
        for region, tissues_list in sorted(regions.items())
    ]


@router.get("/volume-by-region")
def get_volume_by_region(
    days: int = Query(default=7, ge=1, le=90),
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    """Per-day volume broken down by muscle region for the last N days."""
    today = as_of or datetime.date.today()
    cutoff = today - datetime.timedelta(days=days - 1)

    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    weight_lookup = bodyweight_by_date(
        list(session.exec(select(WeightLog).order_by(WeightLog.logged_at)).all())
    )
    stmt = (
        select(WorkoutSession.date, WorkoutSet)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(col(WorkoutSession.date) >= cutoff, col(WorkoutSession.date) <= today)
    )
    rows = session.exec(stmt).all()

    region_rows = session.exec(
        select(
            ExerciseTissue.exercise_id,
            Tissue.region,
            ExerciseTissue.routing_factor,
            ExerciseTissue.loading_factor,
        )
        .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
        .where(col(ExerciseTissue.role).in_(["primary", "secondary"]))
    ).all()

    exercise_regions: dict = defaultdict(list)
    for ex_id, region, routing, loading in region_rows:
        exercise_regions[ex_id].append((region, routing or loading or 1.0))

    daily: dict = defaultdict(lambda: defaultdict(float))
    for workout_date, workout_set in rows:
        exercise = exercises.get(workout_set.exercise_id)
        if not exercise:
            continue
        set_weight = effective_weight(exercise, workout_set, weight_lookup, workout_date)
        vol = effective_set_load(exercise, workout_set, set_weight)
        if vol <= 0:
            continue
        for region, routing in exercise_regions.get(workout_set.exercise_id, []):
            daily[str(workout_date)][region] += vol * routing

    date_range = []
    cur = cutoff
    while cur <= today:
        date_range.append(str(cur))
        cur += datetime.timedelta(days=1)

    totals: dict = defaultdict(float)
    for day_data in daily.values():
        for region, vol in day_data.items():
            totals[region] += vol

    all_regions = sorted(totals.keys(), key=lambda r: totals[r], reverse=True)

    return {
        "dates": date_range,
        "regions": all_regions,
        "daily": {d: dict(daily.get(d, {})) for d in date_range},
        "totals": dict(totals),
    }
