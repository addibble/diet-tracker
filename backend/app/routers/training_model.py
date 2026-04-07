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
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    RegionSorenessCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TissueRegionLink,
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
from app.tissue_regions import (
    canonical_region_label,
    canonical_region_names,
    canonical_region_sort_key,
    canonicalize_region,
    is_canonical_region,
)
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
    soreness_0_10: int | None = None
    pain_0_10: int | None = None
    notes: str | None = None


def _region_label(region: str) -> str:
    return canonical_region_label(canonicalize_region(region) or region)


def _tissue_region_links(
    session: Session,
    *,
    tissue_ids: set[int] | None = None,
) -> dict[int, list[str]]:
    if tissue_ids is not None and not tissue_ids:
        return {}
    stmt = select(TissueRegionLink).order_by(
        col(TissueRegionLink.tissue_id),
        col(TissueRegionLink.is_primary).desc(),
        col(TissueRegionLink.region),
    )
    if tissue_ids is not None:
        stmt = stmt.where(col(TissueRegionLink.tissue_id).in_(tissue_ids))
    links = session.exec(stmt).all()
    mapping: dict[int, list[str]] = defaultdict(list)
    for link in links:
        regions = mapping[link.tissue_id]
        if link.region not in regions:
            regions.append(link.region)
    return mapping


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


def _serialize_pain_check_in(
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
        "check_in_kind": "pain",
        "target_kind": "tracked_tissue" if row.tracked_tissue_id is not None else "region",
        "target_key": recovery_checkin_target_key(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
        ),
        "target_label": tracked["display_name"] if tracked else _region_label(row.region),
        "tracked_tissue": tracked,
        "pain_0_10": row.pain_0_10,
        "notes": row.notes,
    }


def _serialize_region_soreness_check_in(
    row: RegionSorenessCheckIn | RecoveryCheckIn,
) -> dict:
    canonical_region = canonicalize_region(row.region) or row.region
    return {
        "id": row.id,
        "date": row.date.isoformat(),
        "region": canonical_region,
        "tracked_tissue_id": None,
        "check_in_kind": "soreness",
        "target_kind": "region",
        "target_key": recovery_checkin_target_key(
            region=canonical_region,
            tracked_tissue_id=None,
        ),
        "target_label": _region_label(canonical_region),
        "tracked_tissue": None,
        "soreness_0_10": int(getattr(row, "soreness_0_10", 0) or 0),
        "notes": row.notes,
    }


def _check_in_sort_key(row: RecoveryCheckIn | RegionSorenessCheckIn) -> tuple:
    return (
        row.date,
        row.created_at,
        row.id or 0,
    )


def _latest_rows(rows, *, key_fn):
    latest = {}
    for row in sorted(rows, key=_check_in_sort_key, reverse=True):
        latest.setdefault(key_fn(row), row)
    return sorted(latest.values(), key=_check_in_sort_key, reverse=True)


def _list_pain_check_in_rows(
    session: Session,
    *,
    date: datetime.date | None = None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> list[RecoveryCheckIn]:
    stmt = select(RecoveryCheckIn).where(RecoveryCheckIn.tracked_tissue_id.is_not(None))
    if date is not None:
        stmt = stmt.where(RecoveryCheckIn.date == date)
    else:
        if start_date is not None:
            stmt = stmt.where(col(RecoveryCheckIn.date) >= start_date)
        if end_date is not None:
            stmt = stmt.where(col(RecoveryCheckIn.date) <= end_date)
        if start_date is None and end_date is None:
            stmt = stmt.where(RecoveryCheckIn.date == datetime.date.today())
    rows = list(session.exec(stmt).all())
    return _latest_rows(
        rows,
        key_fn=lambda row: (row.date, row.region, row.tracked_tissue_id),
    )


def _list_region_soreness_rows(
    session: Session,
    *,
    date: datetime.date | None = None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> list[RegionSorenessCheckIn | RecoveryCheckIn]:
    soreness_stmt = select(RegionSorenessCheckIn)
    legacy_stmt = select(RecoveryCheckIn).where(RecoveryCheckIn.tracked_tissue_id.is_(None))
    if date is not None:
        soreness_stmt = soreness_stmt.where(RegionSorenessCheckIn.date == date)
        legacy_stmt = legacy_stmt.where(RecoveryCheckIn.date == date)
    else:
        if start_date is not None:
            soreness_stmt = soreness_stmt.where(col(RegionSorenessCheckIn.date) >= start_date)
            legacy_stmt = legacy_stmt.where(col(RecoveryCheckIn.date) >= start_date)
        if end_date is not None:
            soreness_stmt = soreness_stmt.where(col(RegionSorenessCheckIn.date) <= end_date)
            legacy_stmt = legacy_stmt.where(col(RecoveryCheckIn.date) <= end_date)
        if start_date is None and end_date is None:
            today = datetime.date.today()
            soreness_stmt = soreness_stmt.where(RegionSorenessCheckIn.date == today)
            legacy_stmt = legacy_stmt.where(RecoveryCheckIn.date == today)
    rows = list(session.exec(soreness_stmt).all()) + list(session.exec(legacy_stmt).all())
    return _latest_rows(
        rows,
        key_fn=lambda row: (row.date, canonicalize_region(row.region) or row.region),
    )


def _resolve_tracked_tissue_check_in_target(
    *,
    session: Session,
    region: str | None,
    tracked_tissue_id: int | None,
) -> tuple[str, int | None]:
    if tracked_tissue_id is None:
        raise HTTPException(status_code=400, detail="Tracked tissue is required for pain check-ins")
    cleaned_region = region.strip() if region else None
    tracked = session.get(TrackedTissue, tracked_tissue_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    tissue = session.get(Tissue, tracked.tissue_id)
    if tissue is None:
        raise HTTPException(status_code=404, detail="Tracked tissue tissue not found")
    if cleaned_region and cleaned_region != tissue.region:
        raise HTTPException(status_code=400, detail="Tracked tissue region does not match request region")
    return tissue.region, tracked.id


def _resolve_region_soreness_target(
    *,
    session: Session,
    region: str | None,
) -> str:
    del session
    cleaned_region = region.strip() if region else None
    if not cleaned_region:
        raise HTTPException(status_code=400, detail="Region is required for soreness check-ins")
    canonical_region = canonicalize_region(cleaned_region)
    if canonical_region is None or not is_canonical_region(canonical_region):
        raise HTTPException(status_code=400, detail="Unknown soreness region")
    return canonical_region


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
                ExerciseTissue.tissue_id,
                Tissue.region,
                ExerciseTissue.role,
                ExerciseTissue.routing_factor,
                ExerciseTissue.loading_factor,
            )
            .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
            .where(col(ExerciseTissue.exercise_id).in_(exercise_ids))
        ).all()
        links_by_tissue = _tissue_region_links(
            session,
            tissue_ids={tissue_id for _, tissue_id, _, _, _, _ in mappings},
        )
        mappings_by_exercise: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
        for exercise_id, tissue_id, primary_region, role, routing, loading in mappings:
            regions = links_by_tissue.get(tissue_id) or [primary_region]
            if not regions:
                continue
            per_region_routing = (routing or loading or 1.0) / len(regions)
            for region in regions:
                mappings_by_exercise[exercise_id].append(
                    (
                        region,
                        role,
                        per_region_routing,
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
                key=lambda item: (-item[1], *canonical_region_sort_key(item[0])),
            )
            if score >= threshold
        ]
    return []


def _target_reason(code: str) -> dict[str, str]:
    return {"code": code, "label": _CHECK_IN_REASON_LABELS[code]}


def _invalidate_planned_session_for_date(
    session: Session,
    *,
    target_date: datetime.date,
) -> None:
    planned = session.exec(
        select(PlannedSession)
        .where(
            PlannedSession.date == target_date,
            PlannedSession.status == "planned",
            PlannedSession.workout_session_id.is_(None),
        )
        .order_by(col(PlannedSession.id).desc())
        .limit(1)
    ).first()
    if planned is None:
        return

    day = session.get(ProgramDay, planned.program_day_id)
    if day is not None:
        exercises = session.exec(
            select(ProgramDayExercise).where(ProgramDayExercise.program_day_id == day.id)
        ).all()
        for exercise in exercises:
            session.delete(exercise)
        session.delete(day)
    session.delete(planned)


def _condition_requires_recovery_checkin(condition: TissueCondition) -> bool:
    return condition.status in {"tender", "injured"}


def _rehab_plan_requires_recovery_checkin(plan: RehabPlan) -> bool:
    try:
        protocol = get_rehab_protocol(plan.protocol_id)
    except KeyError:
        return True
    return protocol.get("category") == "tendon"

def _build_check_in_targets(
    *,
    session: Session,
    target_date: datetime.date,
) -> dict:
    tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
    active_tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=False)
    today_pain_by_key = {
        item["target_key"]: item
        for item in [
            _serialize_pain_check_in(row, tracked_payloads)
            for row in _list_pain_check_in_rows(session, date=target_date)
        ]
    }
    today_soreness_by_key = {
        item["target_key"]: item
        for item in [
            _serialize_region_soreness_check_in(row)
            for row in _list_region_soreness_rows(session, date=target_date)
        ]
    }
    yesterday_pain_rows = _list_pain_check_in_rows(
        session,
        date=target_date - datetime.timedelta(days=1),
    )
    yesterday_soreness_rows = _list_region_soreness_rows(
        session,
        date=target_date - datetime.timedelta(days=1),
    )

    pain_targets: dict[str, dict] = {}
    soreness_targets: dict[str, dict] = {}

    def add_target(
        *,
        targets: dict[str, dict],
        check_in_kind: str,
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
        existing_check_in = (
            today_pain_by_key.get(target_key)
            if check_in_kind == "pain"
            else today_soreness_by_key.get(target_key)
        )
        target = targets.get(target_key)
        if target is None:
            target = {
                "target_key": target_key,
                "check_in_kind": check_in_kind,
                "target_kind": "tracked_tissue" if tracked_tissue_id is not None else "region",
                "region": region,
                "tracked_tissue_id": tracked_tissue_id,
                "target_label": tracked["display_name"] if tracked else _region_label(region),
                "tracked_tissue": tracked,
                "reasons": [],
                "existing_check_in": existing_check_in,
            }
            targets[target_key] = target
        if all(reason["code"] != reason_code for reason in target["reasons"]):
            target["reasons"].append(_target_reason(reason_code))
            target["reasons"].sort(
                key=lambda reason: _CHECK_IN_REASON_PRIORITY.get(reason["code"], 99)
            )
        if target["existing_check_in"] is None:
            target["existing_check_in"] = existing_check_in

    tracked_conditions: dict[int, TissueCondition] = get_all_current_tracked_conditions(session)
    for tracked_id, condition in tracked_conditions.items():
        if not _condition_requires_recovery_checkin(condition):
            continue
        tracked = tracked_payloads.get(tracked_id)
        if tracked is None:
            continue
        add_target(
            targets=pain_targets,
            check_in_kind="pain",
            region=tracked["region"],
            tracked_tissue_id=tracked_id,
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
            targets=pain_targets,
            check_in_kind="pain",
            region=tracked["region"],
            tracked_tissue_id=tracked_id,
            reason_code="active_rehab",
        )

    for region in _last_workout_major_regions(session):
        add_target(
            targets=soreness_targets,
            check_in_kind="soreness",
            region=region,
            tracked_tissue_id=None,
            reason_code="worked_last_workout",
        )

    for row in yesterday_pain_rows:
        if not recovery_checkin_has_symptoms(row):
            continue
        add_target(
            targets=pain_targets,
            check_in_kind="pain",
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
            reason_code="symptomatic_yesterday",
        )

    for row in yesterday_soreness_rows:
        if int(getattr(row, "soreness_0_10", 0) or 0) <= 0:
            continue
        region = canonicalize_region(row.region) or row.region
        add_target(
            targets=soreness_targets,
            check_in_kind="soreness",
            region=region,
            tracked_tissue_id=None,
            reason_code="symptomatic_yesterday",
        )

    for row in _list_pain_check_in_rows(session, date=target_date):
        target_key = recovery_checkin_target_key(
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
        )
        if target_key in pain_targets:
            continue
        add_target(
            targets=pain_targets,
            check_in_kind="pain",
            region=row.region,
            tracked_tissue_id=row.tracked_tissue_id,
            reason_code="checked_in_today",
        )

    for row in _list_region_soreness_rows(session, date=target_date):
        region = canonicalize_region(row.region) or row.region
        target_key = recovery_checkin_target_key(
            region=region,
            tracked_tissue_id=None,
        )
        if target_key in soreness_targets:
            continue
        add_target(
            targets=soreness_targets,
            check_in_kind="soreness",
            region=region,
            tracked_tissue_id=None,
            reason_code="checked_in_today",
        )

    pain_target_keys = set(pain_targets.keys())
    soreness_target_keys = set(soreness_targets.keys())
    region_options = [
        {
            "target_key": recovery_checkin_target_key(region=region, tracked_tissue_id=None),
            "check_in_kind": "soreness",
            "target_kind": "region",
            "region": region,
            "tracked_tissue_id": None,
            "target_label": _region_label(region),
            "tracked_tissue": None,
        }
        for region in canonical_region_names()
        if recovery_checkin_target_key(region=region, tracked_tissue_id=None) not in soreness_target_keys
    ]
    tracked_options = [
        {
            "target_key": recovery_checkin_target_key(
                region=tracked["region"],
                tracked_tissue_id=tracked["id"],
            ),
            "check_in_kind": "pain",
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
        ) not in pain_target_keys
    ]

    sorted_pain_targets = sorted(
        pain_targets.values(),
        key=lambda target: (
            min(
                _CHECK_IN_REASON_PRIORITY.get(reason["code"], 99)
                for reason in target["reasons"]
            ),
            0 if target["existing_check_in"] is None else 1,
            target["target_label"].lower(),
        ),
    )
    sorted_soreness_targets = sorted(
        soreness_targets.values(),
        key=lambda target: (
            min(
                _CHECK_IN_REASON_PRIORITY.get(reason["code"], 99)
                for reason in target["reasons"]
            ),
            0 if target["existing_check_in"] is None else 1,
            target["target_label"].lower(),
        ),
    )
    combined_targets = sorted_pain_targets + sorted_soreness_targets
    today_pain_check_ins = list(today_pain_by_key.values())
    today_soreness_check_ins = list(today_soreness_by_key.values())
    return {
        "date": target_date.isoformat(),
        "targets": combined_targets,
        "pain_targets": sorted_pain_targets,
        "soreness_targets": sorted_soreness_targets,
        "today_check_ins": today_pain_check_ins + today_soreness_check_ins,
        "today_pain_check_ins": today_pain_check_ins,
        "today_soreness_check_ins": today_soreness_check_ins,
        "other_options": {
            "regions": region_options,
            "tracked_tissues": tracked_options,
            "soreness_regions": region_options,
            "pain_tracked_tissues": tracked_options,
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
    if data.tracked_tissue_id is not None:
        if (data.soreness_0_10 or 0) > 0:
            raise HTTPException(
                status_code=400,
                detail="Tracked tissue check-ins are pain-only",
            )
        region, tracked_tissue_id = _resolve_tracked_tissue_check_in_target(
            session=session,
            region=data.region,
            tracked_tissue_id=data.tracked_tissue_id,
        )
        existing = session.exec(
            select(RecoveryCheckIn)
            .where(
                RecoveryCheckIn.date == data.date,
                RecoveryCheckIn.region == region,
                RecoveryCheckIn.tracked_tissue_id == tracked_tissue_id,
            )
            .order_by(RecoveryCheckIn.id.desc())
            .limit(1)
        ).first()
        if existing:
            existing.region = region
            existing.tracked_tissue_id = tracked_tissue_id
            existing.soreness_0_10 = 0
            existing.pain_0_10 = data.pain_0_10 or 0
            existing.stiffness_0_10 = 0
            existing.readiness_0_10 = 5
            existing.notes = data.notes
            row = existing
        else:
            row = RecoveryCheckIn(
                date=data.date,
                region=region,
                tracked_tissue_id=tracked_tissue_id,
                soreness_0_10=0,
                pain_0_10=data.pain_0_10 or 0,
                stiffness_0_10=0,
                readiness_0_10=5,
                notes=data.notes,
            )
        session.add(row)
        _invalidate_planned_session_for_date(session, target_date=data.date)
        session.commit()
        session.refresh(row)
        tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
        return _serialize_pain_check_in(row, tracked_payloads)

    if (data.pain_0_10 or 0) > 0:
        raise HTTPException(
            status_code=400,
            detail="Region check-ins are soreness-only",
        )
    region = _resolve_region_soreness_target(session=session, region=data.region)
    existing_soreness = session.exec(
        select(RegionSorenessCheckIn)
        .where(
            RegionSorenessCheckIn.date == data.date,
            RegionSorenessCheckIn.region == region,
        )
        .order_by(RegionSorenessCheckIn.id.desc())
        .limit(1)
    ).first()
    if existing_soreness:
        existing_soreness.soreness_0_10 = data.soreness_0_10 or 0
        existing_soreness.notes = data.notes
        row = existing_soreness
    else:
        row = RegionSorenessCheckIn(
            date=data.date,
            region=region,
            soreness_0_10=data.soreness_0_10 or 0,
            notes=data.notes,
        )
    session.add(row)
    _invalidate_planned_session_for_date(session, target_date=data.date)
    session.commit()
    session.refresh(row)
    return _serialize_region_soreness_check_in(row)


@router.get("/check-ins")
def get_check_ins(
    date: datetime.date | None = Query(default=None),
    start_date: datetime.date | None = Query(default=None),
    end_date: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    pain_rows = _list_pain_check_in_rows(
        session,
        date=date,
        start_date=start_date,
        end_date=end_date,
    )
    soreness_rows = _list_region_soreness_rows(
        session,
        date=date,
        start_date=start_date,
        end_date=end_date,
    )
    tracked_payloads = _tracked_tissue_payload_map(session, include_inactive=True)
    combined = [
        *[_serialize_pain_check_in(row, tracked_payloads) for row in pain_rows],
        *[_serialize_region_soreness_check_in(row) for row in soreness_rows],
    ]
    return sorted(
        combined,
        key=lambda row: (row["date"], row["target_key"], row["check_in_kind"]),
        reverse=True,
    )


@router.get("/regions")
def get_regions(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue).order_by(Tissue.display_name, Tissue.name)).all()
    }
    links = session.exec(
        select(TissueRegionLink).order_by(
            col(TissueRegionLink.is_primary).desc(),
            col(TissueRegionLink.region),
            col(TissueRegionLink.tissue_id),
        )
    ).all()
    regions: dict[str, list[dict]] = {region: [] for region in canonical_region_names()}
    for link in links:
        tissue = tissues.get(link.tissue_id)
        if tissue is None:
            continue
        regions.setdefault(link.region, []).append(
            {
                "id": tissue.id,
                "name": tissue.name,
                "display_name": tissue.display_name,
                "type": tissue.type,
                "primary_region": tissue.region,
                "is_primary": bool(link.is_primary),
            }
        )
    linked_tissue_ids = {link.tissue_id for link in links}
    for tissue in tissues.values():
        if tissue.id in linked_tissue_ids:
            continue
        regions.setdefault(tissue.region, []).append(
            {
                "id": tissue.id,
                "name": tissue.name,
                "display_name": tissue.display_name,
                "type": tissue.type,
                "primary_region": tissue.region,
                "is_primary": True,
            }
        )
    payload = []
    for region in sorted(regions, key=canonical_region_sort_key):
        tissues_list = sorted(
            regions[region],
            key=lambda item: (
                0 if item["is_primary"] else 1,
                item["display_name"].lower(),
                item["name"],
            ),
        )
        payload.append(
            {
                "region": region,
                "label": _region_label(region),
                "tissues": tissues_list,
            }
        )
    return payload


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
            ExerciseTissue.tissue_id,
            Tissue.region,
            ExerciseTissue.routing_factor,
            ExerciseTissue.loading_factor,
        )
        .join(Tissue, Tissue.id == ExerciseTissue.tissue_id)
        .where(col(ExerciseTissue.role).in_(["primary", "secondary"]))
    ).all()

    exercise_regions: dict = defaultdict(list)
    links_by_tissue = _tissue_region_links(
        session,
        tissue_ids={tissue_id for _, tissue_id, _, _, _ in region_rows},
    )
    for ex_id, tissue_id, primary_region, routing, loading in region_rows:
        regions = links_by_tissue.get(tissue_id) or [primary_region]
        if not regions:
            continue
        per_region_routing = (routing or loading or 1.0) / len(regions)
        for region in regions:
            exercise_regions[ex_id].append((region, per_region_routing))

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

    all_regions = sorted(
        totals.keys(),
        key=lambda region: (-totals[region], *canonical_region_sort_key(region)),
    )

    return {
        "dates": date_range,
        "regions": all_regions,
        "daily": {d: dict(daily.get(d, {})) for d in date_range},
        "totals": dict(totals),
        "region_labels": {region: _region_label(region) for region in all_regions},
    }
