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
    RehabPlan,
    Tissue,
    TissueCondition,
    TissueRelationship,
    TrackedTissue,
    TrainingExclusionWindow,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.recovery_check_ins import (
    recovery_checkin_has_symptoms,
    recovery_checkin_target_key,
)
from app.rehab_protocols import get_rehab_protocol
from app.tracked_tissues import (
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    list_tracked_tissues,
)
from app.training_model import (
    build_exercise_risk_ranking,
    build_exercise_strength,
    build_tissue_history,
    build_training_model_summary,
    list_exclusion_windows,
)

router = APIRouter(prefix="/api/training-model", tags=["training-model"])

_CHECK_IN_REASON_LABELS = {
    "active_rehab": "Active rehab",
    "active_condition": "Active condition",
    "symptomatic_yesterday": "Symptomatic yesterday",
    "worked_last_workout": "Worked last workout",
    "checked_in_today": "Added today",
}

_CHECK_IN_REASON_PRIORITY = {
    "active_rehab": 0,
    "active_condition": 1,
    "symptomatic_yesterday": 2,
    "worked_last_workout": 3,
    "checked_in_today": 4,
}


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
    region: str | None = None
    tracked_tissue_id: int | None = None
    soreness_0_10: int = 0
    pain_0_10: int = 0
    stiffness_0_10: int = 0
    readiness_0_10: int = 5
    notes: str | None = None


def _region_label(region: str) -> str:
    return region.replace("_", " ").title()


def _tracked_tissue_payload_map(
    session: Session,
    *,
    include_inactive: bool,
) -> dict[int, dict]:
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    result: dict[int, dict] = {}
    for tracked in list_tracked_tissues(session, include_inactive=include_inactive):
        tissue = tissues.get(tracked.tissue_id)
        if tissue is None:
            continue
        result[tracked.id] = {
            "id": tracked.id,
            "tissue_id": tracked.tissue_id,
            "tissue_name": tissue.name,
            "tissue_display_name": tissue.display_name,
            "tissue_type": tissue.type,
            "region": tissue.region,
            "side": tracked.side,
            "display_name": tracked.display_name,
            "tracking_mode": tissue.tracking_mode,
            "active": tracked.active,
        }
    return result


def _serialize_check_in(
    row: RecoveryCheckIn,
    tracked_payloads: dict[int, dict],
) -> dict:
    tracked = (
        tracked_payloads.get(row.tracked_tissue_id)
        if row.tracked_tissue_id is not None
        else None
    )
    return {
        "id": row.id,
        "date": row.date.isoformat(),
        "region": row.region,
        "tracked_tissue_id": row.tracked_tissue_id,
        "target_kind": "tracked_tissue" if row.tracked_tissue_id is not None else "region",
        "target_key": recovery_checkin_target_key(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
        ),
        "target_label": tracked["display_name"] if tracked else _region_label(row.region),
        "tracked_tissue": tracked,
        "soreness_0_10": row.soreness_0_10,
        "pain_0_10": row.pain_0_10,
        "stiffness_0_10": row.stiffness_0_10,
        "readiness_0_10": row.readiness_0_10,
        "notes": row.notes,
    }


def _resolve_check_in_target(
    *,
    session: Session,
    region: str | None,
    tracked_tissue_id: int | None,
) -> tuple[str, int | None]:
    cleaned_region = region.strip() if region else None
    if tracked_tissue_id is None:
        if not cleaned_region:
            raise HTTPException(status_code=400, detail="Region or tracked tissue is required")
        return cleaned_region, None

    tracked = session.get(TrackedTissue, tracked_tissue_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    tissue = session.get(Tissue, tracked.tissue_id)
    if tissue is None:
        raise HTTPException(status_code=404, detail="Tracked tissue tissue not found")
    if cleaned_region and cleaned_region != tissue.region:
        raise HTTPException(status_code=400, detail="Tracked tissue region does not match request region")
    return tissue.region, tracked.id


def _last_workout_major_regions(session: Session) -> list[str]:
    sessions = session.exec(
        select(WorkoutSession).order_by(
            col(WorkoutSession.date).desc(),
            col(WorkoutSession.finished_at).desc(),
            col(WorkoutSession.started_at).desc(),
            col(WorkoutSession.created_at).desc(),
        )
    ).all()
    for workout_session in sessions:
        workout_sets = session.exec(
            select(WorkoutSet).where(WorkoutSet.session_id == workout_session.id)
        ).all()
        if not workout_sets:
            continue
        exercise_ids = sorted({row.exercise_id for row in workout_sets})
        if not exercise_ids:
            continue
        region_scores: dict[str, float] = defaultdict(float)
        mappings = session.exec(
            select(
                ExerciseTissue.exercise_id,
                Tissue.region,
                ExerciseTissue.role,
                ExerciseTissue.routing_factor,
                ExerciseTissue.loading_factor,
            )
            .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
            .where(col(ExerciseTissue.exercise_id).in_(exercise_ids))
        ).all()
        mappings_by_exercise: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
        for exercise_id, region, role, routing, loading in mappings:
            mappings_by_exercise[exercise_id].append(
                (
                    region,
                    role,
                    routing or loading or 1.0,
                )
            )
        for workout_set in workout_sets:
            for region, role, routing in mappings_by_exercise.get(workout_set.exercise_id, []):
                if role == "primary":
                    region_scores[region] += routing
                elif role == "secondary" and routing >= 0.5:
                    region_scores[region] += routing
        if not region_scores:
            continue
        peak = max(region_scores.values())
        threshold = max(0.75, peak * 0.35)
        return [
            region
            for region, score in sorted(
                region_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )
            if score >= threshold
        ]
    return []


def _target_reason(code: str) -> dict[str, str]:
    return {"code": code, "label": _CHECK_IN_REASON_LABELS[code]}


def _condition_requires_recovery_checkin(condition: TissueCondition) -> bool:
    return condition.status in {"tender", "injured"}


def _rehab_plan_requires_recovery_checkin(plan: RehabPlan) -> bool:
    try:
        protocol = get_rehab_protocol(plan.protocol_id)
    except KeyError:
        return True
    return protocol.get("category") == "tendon"


def _companion_tracked_tissue_ids(
    *,
    session: Session,
    tracked_payloads: dict[int, dict],
) -> dict[int, set[int]]:
    relationships = session.exec(
        select(TissueRelationship).where(TissueRelationship.required_for_mapping_warning.is_(True))
    ).all()
    tracked_by_tissue_side: dict[tuple[int, str], int] = {}
    for tracked_id, payload in tracked_payloads.items():
        tracked_by_tissue_side[(payload["tissue_id"], payload["side"])] = tracked_id

    result: dict[int, set[int]] = defaultdict(set)
    for tracked_id, payload in tracked_payloads.items():
        for row in relationships:
            if row.source_tissue_id != payload["tissue_id"]:
                continue
            companion_id = tracked_by_tissue_side.get((row.target_tissue_id, payload["side"]))
            if companion_id is not None:
                result[tracked_id].add(companion_id)
    return result


def _build_check_in_targets(
    *,
    session: Session,
    target_date: datetime.date,
) -> dict:
    tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
    active_tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=False)
    companion_ids = _companion_tracked_tissue_ids(
        session=session,
        tracked_payloads=tracked_payloads,
    )
    today_rows = session.exec(
        select(RecoveryCheckIn)
        .where(RecoveryCheckIn.date == target_date)
        .order_by(col(RecoveryCheckIn.created_at).desc())
    ).all()
    yesterday_rows = session.exec(
        select(RecoveryCheckIn)
        .where(RecoveryCheckIn.date == (target_date - datetime.timedelta(days=1)))
        .order_by(col(RecoveryCheckIn.created_at).desc())
    ).all()
    today_by_key: dict[str, dict] = {}
    for row in today_rows:
        serialized = _serialize_check_in(row, tracked_payloads)
        today_by_key.setdefault(serialized["target_key"], serialized)

    targets: dict[str, dict] = {}

    def add_target(
        *,
        region: str,
        tracked_tissue_id: int | None,
        reason_code: str,
    ) -> None:
        target_key = recovery_checkin_target_key(
            region=region,
            tracked_tissue_id=tracked_tissue_id,
        )
        tracked = (
            tracked_payloads.get(tracked_tissue_id)
            if tracked_tissue_id is not None
            else None
        )
        target = targets.get(target_key)
        if target is None:
            target = {
                "target_key": target_key,
                "target_kind": "tracked_tissue" if tracked_tissue_id is not None else "region",
                "region": region,
                "tracked_tissue_id": tracked_tissue_id,
                "target_label": tracked["display_name"] if tracked else _region_label(region),
                "tracked_tissue": tracked,
                "reasons": [],
                "existing_check_in": today_by_key.get(target_key),
            }
            targets[target_key] = target
        if all(reason["code"] != reason_code for reason in target["reasons"]):
            target["reasons"].append(_target_reason(reason_code))
            target["reasons"].sort(
                key=lambda reason: _CHECK_IN_REASON_PRIORITY.get(reason["code"], 99)
            )
        if target["existing_check_in"] is None:
            target["existing_check_in"] = today_by_key.get(target_key)

    tracked_conditions: dict[int, TissueCondition] = get_all_current_tracked_conditions(session)
    for tracked_id, condition in tracked_conditions.items():
        if not _condition_requires_recovery_checkin(condition):
            continue
        tracked = tracked_payloads.get(tracked_id)
        if tracked is None:
            continue
        add_target(
            region=tracked["region"],
            tracked_tissue_id=tracked_id,
            reason_code="active_condition",
        )
        for companion_id in companion_ids.get(tracked_id, set()):
            companion = tracked_payloads.get(companion_id)
            if companion is None:
                continue
            add_target(
                region=companion["region"],
                tracked_tissue_id=companion_id,
                reason_code="active_condition",
            )

    active_plans: dict[int, RehabPlan] = get_active_rehab_plans_by_tracked_tissue(session)
    for tracked_id, plan in active_plans.items():
        if not _rehab_plan_requires_recovery_checkin(plan):
            continue
        tracked = tracked_payloads.get(tracked_id)
        if tracked is None:
            continue
        add_target(
            region=tracked["region"],
            tracked_tissue_id=tracked_id,
            reason_code="active_rehab",
        )
        for companion_id in companion_ids.get(tracked_id, set()):
            companion = tracked_payloads.get(companion_id)
            if companion is None:
                continue
            add_target(
                region=companion["region"],
                tracked_tissue_id=companion_id,
                reason_code="active_rehab",
            )

    for region in _last_workout_major_regions(session):
        add_target(
            region=region,
            tracked_tissue_id=None,
            reason_code="worked_last_workout",
        )

    for row in yesterday_rows:
        if not recovery_checkin_has_symptoms(row):
            continue
        add_target(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
            reason_code="symptomatic_yesterday",
        )

    for row in today_rows:
        target_key = recovery_checkin_target_key(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
        )
        if target_key in targets:
            continue
        add_target(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
            reason_code="checked_in_today",
        )

    target_keys = set(targets.keys())
    region_options = [
        {
            "target_key": recovery_checkin_target_key(region=region, tracked_tissue_id=None),
            "target_kind": "region",
            "region": region,
            "tracked_tissue_id": None,
            "target_label": _region_label(region),
            "tracked_tissue": None,
        }
        for region in sorted({tissue.region for tissue in session.exec(select(Tissue)).all()})
        if recovery_checkin_target_key(region=region, tracked_tissue_id=None) not in target_keys
    ]
    tracked_options = [
        {
            "target_key": recovery_checkin_target_key(
                region=tracked["region"],
                tracked_tissue_id=tracked["id"],
            ),
            "target_kind": "tracked_tissue",
            "region": tracked["region"],
            "tracked_tissue_id": tracked["id"],
            "target_label": tracked["display_name"],
            "tracked_tissue": tracked,
        }
        for tracked in sorted(
            active_tracked_payloads.values(),
            key=lambda item: item["display_name"],
        )
        if recovery_checkin_target_key(
            region=tracked["region"],
            tracked_tissue_id=tracked["id"],
        ) not in target_keys
    ]

    sorted_targets = sorted(
        targets.values(),
        key=lambda target: (
            min(
                _CHECK_IN_REASON_PRIORITY.get(reason["code"], 99)
                for reason in target["reasons"]
            ),
            0 if target["existing_check_in"] is None else 1,
            0 if target["target_kind"] == "tracked_tissue" else 1,
            target["target_label"].lower(),
        ),
    )
    return {
        "date": target_date.isoformat(),
        "targets": sorted_targets,
        "today_check_ins": list(today_by_key.values()),
        "other_options": {
            "regions": region_options,
            "tracked_tissues": tracked_options,
        },
    }


@router.get("/check-in-targets")
def get_check_in_targets(
    date: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    target_date = date or datetime.date.today()
    return _build_check_in_targets(session=session, target_date=target_date)


@router.post("/check-in", status_code=201)
def create_check_in(
    data: RecoveryCheckInCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    region, tracked_tissue_id = _resolve_check_in_target(
        session=session,
        region=data.region,
        tracked_tissue_id=data.tracked_tissue_id,
    )
    stmt = select(RecoveryCheckIn).where(
        RecoveryCheckIn.date == data.date,
        RecoveryCheckIn.region == region,
    )
    if tracked_tissue_id is None:
        stmt = stmt.where(RecoveryCheckIn.tracked_tissue_id.is_(None))
    else:
        stmt = stmt.where(RecoveryCheckIn.tracked_tissue_id == tracked_tissue_id)
    existing = session.exec(
        stmt.order_by(RecoveryCheckIn.id.desc())  # type: ignore[union-attr]
        .limit(1)
    ).first()

    if existing:
        existing.region = region
        existing.tracked_tissue_id = tracked_tissue_id
        existing.soreness_0_10 = data.soreness_0_10
        existing.pain_0_10 = data.pain_0_10
        existing.stiffness_0_10 = data.stiffness_0_10
        existing.readiness_0_10 = data.readiness_0_10
        existing.notes = data.notes
        row = existing
    else:
        row = RecoveryCheckIn(
            date=data.date,
            region=region,
            tracked_tissue_id=tracked_tissue_id,
            soreness_0_10=data.soreness_0_10,
            pain_0_10=data.pain_0_10,
            stiffness_0_10=data.stiffness_0_10,
            readiness_0_10=data.readiness_0_10,
            notes=data.notes,
        )
    session.add(row)
    session.commit()
    session.refresh(row)
    tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
    return _serialize_check_in(row, tracked_payloads)


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
    tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
    return [_serialize_check_in(row, tracked_payloads) for row in rows]


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
