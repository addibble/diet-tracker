from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import (
    RehabCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TrackedTissue,
)
from app.rehab_protocols import get_rehab_protocol, list_rehab_protocols
from app.tracked_tissues import (
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_current_tracked_tissue_condition,
    list_tracked_tissues,
    seed_tracked_tissues,
    tissue_tracking_mode,
)
from app.workout_queries import (
    get_all_current_conditions,
    get_current_tissue_condition,
    get_current_tissues,
)

router = APIRouter(prefix="/api/tissues", tags=["tissues"])


class TissueCreate(BaseModel):
    name: str
    display_name: str
    type: str = "muscle"
    tracking_mode: Literal["paired", "center"] | None = None
    recovery_hours: float = 48.0
    notes: str | None = None


class TissueUpdate(BaseModel):
    tracking_mode: Literal["paired", "center"] | None = None
    recovery_hours: float | None = None
    notes: str | None = None
    capacity_prior: float | None = None
    recovery_tau_days: float | None = None
    fatigue_tau_days: float | None = None
    collapse_drop_threshold: float | None = None
    ramp_sensitivity: float | None = None
    risk_sensitivity: float | None = None


class TissueConditionCreate(BaseModel):
    tissue_id: int | None = None
    tracked_tissue_id: int | None = None
    status: Literal["healthy", "tender", "injured", "rehabbing"]
    severity: int = 0
    max_loading_factor: float | None = None
    recovery_hours_override: float | None = None
    rehab_protocol: str | None = None
    notes: str | None = None


class RehabPlanCreate(BaseModel):
    tracked_tissue_id: int
    protocol_id: str
    stage_id: str
    status: Literal["active", "paused", "completed"] = "active"
    pain_monitoring_threshold: int | None = None
    max_next_day_flare: int | None = None
    sessions_per_week_target: float | None = None
    max_weekly_set_progression: int | None = None
    max_load_progression_pct: float | None = None
    notes: str | None = None


class RehabPlanUpdate(BaseModel):
    protocol_id: str | None = None
    stage_id: str | None = None
    status: Literal["active", "paused", "completed"] | None = None
    pain_monitoring_threshold: int | None = None
    max_next_day_flare: int | None = None
    sessions_per_week_target: float | None = None
    max_weekly_set_progression: int | None = None
    max_load_progression_pct: float | None = None
    notes: str | None = None


class RehabCheckInCreate(BaseModel):
    tracked_tissue_id: int
    rehab_plan_id: int | None = None
    pain_0_10: int = 0
    stiffness_0_10: int = 0
    weakness_0_10: int = 0
    neural_symptoms_0_10: int = 0
    during_load_pain_0_10: int = 0
    next_day_flare: int = 0
    confidence_0_10: int = 5
    notes: str | None = None


@router.get("")
def list_tissues(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return [
        _serialize_tissue(
            t,
            session.get(TissueModelConfig, t.id),
            tracked_rows=session.exec(
                select(TrackedTissue)
                .where(TrackedTissue.tissue_id == t.id)
                .order_by(TrackedTissue.side)
            ).all(),
        )
        for t in get_current_tissues(session)
    ]


@router.get("/conditions/current")
def list_conditions(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    conditions = get_all_current_conditions(session)
    result = []
    for condition in conditions:
        tissue = session.get(Tissue, condition.tissue_id)
        tracked = (
            session.get(TrackedTissue, condition.tracked_tissue_id)
            if condition.tracked_tissue_id
            else None
        )
        serialized = _serialize_condition(condition)
        result.append({
            "tissue_id": condition.tissue_id,
            "tissue_name": tissue.name if tissue else "unknown",
            "tissue_display_name": tissue.display_name if tissue else "unknown",
            "tracked_tissue_id": tracked.id if tracked else None,
            "tracked_tissue_display_name": tracked.display_name if tracked else None,
            **(serialized or {}),
        })
    return result


@router.get("/conditions/{tissue_id:int}/history")
def get_condition_history(
    tissue_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    tracked_lookup = {
        row.id: row
        for row in session.exec(
            select(TrackedTissue).where(TrackedTissue.tissue_id == tissue_id)
        ).all()
    }
    conditions = session.exec(
        select(TissueCondition)
        .where(TissueCondition.tissue_id == tissue_id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(limit)
    ).all()
    return [
        {
            "tracked_tissue_id": row.tracked_tissue_id,
            "tracked_tissue_display_name": (
                tracked_lookup[row.tracked_tissue_id].display_name
                if row.tracked_tissue_id in tracked_lookup
                else None
            ),
            **(_serialize_condition(row) or {}),
        }
        for row in conditions
    ]


@router.post("/conditions", status_code=201)
def create_condition(
    data: TissueConditionCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue_id, tracked_tissue = _resolve_condition_target(session, data.tissue_id, data.tracked_tissue_id)
    condition = TissueCondition(
        tissue_id=tissue_id,
        tracked_tissue_id=tracked_tissue.id if tracked_tissue else None,
        status=data.status,
        severity=data.severity,
        max_loading_factor=data.max_loading_factor,
        recovery_hours_override=data.recovery_hours_override,
        rehab_protocol=data.rehab_protocol,
        notes=data.notes,
    )
    session.add(condition)
    session.commit()
    session.refresh(condition)
    return {
        "id": condition.id,
        "tissue_id": condition.tissue_id,
        "tracked_tissue_id": condition.tracked_tissue_id,
        "tracked_tissue_display_name": tracked_tissue.display_name if tracked_tissue else None,
        "status": condition.status,
        "severity": condition.severity,
        "updated_at": condition.updated_at,
    }


@router.get("/tracked")
def get_tracked(
    tissue_id: int | None = Query(default=None),
    side: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tracked_rows = list_tracked_tissues(session, include_inactive=include_inactive)
    tracked_conditions = get_all_current_tracked_conditions(session)
    active_plans = get_active_rehab_plans_by_tracked_tissue(session)
    results = []
    for tracked in tracked_rows:
        if tissue_id is not None and tracked.tissue_id != tissue_id:
            continue
        if side is not None and tracked.side != side:
            continue
        tissue = session.get(Tissue, tracked.tissue_id)
        if tissue is None:
            continue
        results.append(
            _serialize_tracked_tissue(
                tracked,
                tissue,
                tracked_conditions.get(tracked.id),
                active_plans.get(tracked.id),
            )
        )
    return results


@router.get("/tracked/{tracked_tissue_id:int}")
def get_tracked_tissue(
    tracked_tissue_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tracked = session.get(TrackedTissue, tracked_tissue_id)
    if not tracked:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    tissue = session.get(Tissue, tracked.tissue_id)
    if tissue is None:
        raise HTTPException(status_code=404, detail="Canonical tissue not found")
    condition = get_current_tracked_tissue_condition(session, tracked_tissue_id)
    plan = get_active_rehab_plans_by_tracked_tissue(session).get(tracked_tissue_id)
    return _serialize_tracked_tissue(tracked, tissue, condition, plan)


@router.get("/tracked/{tracked_tissue_id:int}/conditions/history")
def get_tracked_condition_history(
    tracked_tissue_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tracked = session.get(TrackedTissue, tracked_tissue_id)
    if not tracked:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    rows = session.exec(
        select(TissueCondition)
        .where(TissueCondition.tracked_tissue_id == tracked_tissue_id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(limit)
    ).all()
    return [_serialize_condition(row) for row in rows]


@router.get("/rehab-protocols")
def get_rehab_protocols(
    _user: str = Depends(get_current_user),
):
    return list_rehab_protocols()


@router.get("/rehab-protocols/{protocol_id}")
def get_rehab_protocol_detail(
    protocol_id: str,
    _user: str = Depends(get_current_user),
):
    try:
        return get_rehab_protocol(protocol_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/rehab-plans")
def list_rehab_plans(
    tracked_tissue_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(RehabPlan).order_by(col(RehabPlan.updated_at).desc())
    if tracked_tissue_id is not None:
        stmt = stmt.where(RehabPlan.tracked_tissue_id == tracked_tissue_id)
    if status is not None:
        stmt = stmt.where(RehabPlan.status == status)
    rows = session.exec(stmt).all()
    return [_serialize_rehab_plan(session, row) for row in rows]


@router.post("/rehab-plans", status_code=201)
def create_rehab_plan(
    data: RehabPlanCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tracked = session.get(TrackedTissue, data.tracked_tissue_id)
    if not tracked:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    protocol = _validate_protocol_stage(data.protocol_id, data.stage_id)
    active = get_active_rehab_plans_by_tracked_tissue(session).get(data.tracked_tissue_id)
    if active:
        active.status = "paused"
        active.updated_at = datetime.now(UTC)
        session.add(active)
    row = RehabPlan(
        tracked_tissue_id=data.tracked_tissue_id,
        protocol_id=data.protocol_id,
        stage_id=data.stage_id,
        status=data.status,
        pain_monitoring_threshold=(
            data.pain_monitoring_threshold
            if data.pain_monitoring_threshold is not None
            else protocol["default_pain_monitoring_threshold"]
        ),
        max_next_day_flare=(
            data.max_next_day_flare
            if data.max_next_day_flare is not None
            else protocol["default_max_next_day_flare"]
        ),
        sessions_per_week_target=data.sessions_per_week_target,
        max_weekly_set_progression=data.max_weekly_set_progression,
        max_load_progression_pct=data.max_load_progression_pct,
        notes=data.notes,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_rehab_plan(session, row)


@router.patch("/rehab-plans/{rehab_plan_id:int}")
def update_rehab_plan(
    rehab_plan_id: int,
    data: RehabPlanUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    row = session.get(RehabPlan, rehab_plan_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rehab plan not found")

    protocol_id = data.protocol_id or row.protocol_id
    stage_id = data.stage_id or row.stage_id
    _validate_protocol_stage(protocol_id, stage_id)
    row.protocol_id = protocol_id
    row.stage_id = stage_id
    if data.status is not None:
        row.status = data.status
    if data.pain_monitoring_threshold is not None:
        row.pain_monitoring_threshold = data.pain_monitoring_threshold
    if data.max_next_day_flare is not None:
        row.max_next_day_flare = data.max_next_day_flare
    if data.sessions_per_week_target is not None:
        row.sessions_per_week_target = data.sessions_per_week_target
    if data.max_weekly_set_progression is not None:
        row.max_weekly_set_progression = data.max_weekly_set_progression
    if data.max_load_progression_pct is not None:
        row.max_load_progression_pct = data.max_load_progression_pct
    if data.notes is not None:
        row.notes = data.notes
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_rehab_plan(session, row)


@router.get("/rehab-check-ins")
def list_rehab_check_ins(
    tracked_tissue_id: int | None = Query(default=None),
    rehab_plan_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(RehabCheckIn).order_by(col(RehabCheckIn.recorded_at).desc()).limit(limit)
    if tracked_tissue_id is not None:
        stmt = stmt.where(RehabCheckIn.tracked_tissue_id == tracked_tissue_id)
    if rehab_plan_id is not None:
        stmt = stmt.where(RehabCheckIn.rehab_plan_id == rehab_plan_id)
    rows = session.exec(stmt).all()
    return [_serialize_rehab_check_in(session, row) for row in rows]


@router.post("/rehab-check-ins", status_code=201)
def create_rehab_check_in(
    data: RehabCheckInCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tracked = session.get(TrackedTissue, data.tracked_tissue_id)
    if not tracked:
        raise HTTPException(status_code=404, detail="Tracked tissue not found")
    if data.rehab_plan_id is not None and session.get(RehabPlan, data.rehab_plan_id) is None:
        raise HTTPException(status_code=404, detail="Rehab plan not found")
    row = RehabCheckIn(
        tracked_tissue_id=data.tracked_tissue_id,
        rehab_plan_id=data.rehab_plan_id,
        pain_0_10=data.pain_0_10,
        stiffness_0_10=data.stiffness_0_10,
        weakness_0_10=data.weakness_0_10,
        neural_symptoms_0_10=data.neural_symptoms_0_10,
        during_load_pain_0_10=data.during_load_pain_0_10,
        next_day_flare=data.next_day_flare,
        confidence_0_10=data.confidence_0_10,
        notes=data.notes,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_rehab_check_in(session, row)


@router.get("/{tissue_id:int}")
def get_tissue(
    tissue_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    condition = get_current_tissue_condition(session, tissue_id)
    model_config = session.get(TissueModelConfig, tissue_id)
    tracked_rows = session.exec(
        select(TrackedTissue)
        .where(TrackedTissue.tissue_id == tissue_id)
        .order_by(TrackedTissue.side)
    ).all()
    return {
        **_serialize_tissue(tissue, model_config, tracked_rows=tracked_rows),
        "condition": _serialize_condition(condition),
    }


@router.post("", status_code=201)
def create_tissue(
    data: TissueCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = Tissue(
        name=data.name,
        display_name=data.display_name,
        type=data.type,
        tracking_mode=data.tracking_mode or tissue_tracking_mode(data.name),
        recovery_hours=data.recovery_hours,
        notes=data.notes,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)
    seed_tracked_tissues(session)
    return _serialize_tissue(
        tissue,
        session.get(TissueModelConfig, tissue.id),
        tracked_rows=session.exec(
            select(TrackedTissue).where(TrackedTissue.tissue_id == tissue.id)
        ).all(),
    )


@router.put("/{tissue_id:int}")
def update_tissue(
    tissue_id: int,
    data: TissueUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    if data.tracking_mode is not None:
        tissue.tracking_mode = data.tracking_mode
    if data.recovery_hours is not None:
        tissue.recovery_hours = data.recovery_hours
    if data.notes is not None:
        tissue.notes = data.notes
    tissue.updated_at = datetime.now(UTC)
    session.add(tissue)
    config = session.get(TissueModelConfig, tissue_id)
    if config is None:
        config = TissueModelConfig(tissue_id=tissue_id)
    if data.capacity_prior is not None:
        config.capacity_prior = data.capacity_prior
    if data.recovery_tau_days is not None:
        config.recovery_tau_days = data.recovery_tau_days
    if data.fatigue_tau_days is not None:
        config.fatigue_tau_days = data.fatigue_tau_days
    if data.collapse_drop_threshold is not None:
        config.collapse_drop_threshold = data.collapse_drop_threshold
    if data.ramp_sensitivity is not None:
        config.ramp_sensitivity = data.ramp_sensitivity
    if data.risk_sensitivity is not None:
        config.risk_sensitivity = data.risk_sensitivity
    session.add(config)
    session.commit()
    seed_tracked_tissues(session)
    tracked_rows = session.exec(
        select(TrackedTissue)
        .where(TrackedTissue.tissue_id == tissue.id)
        .order_by(TrackedTissue.side)
    ).all()
    return _serialize_tissue(tissue, config, tracked_rows=tracked_rows)


def _resolve_condition_target(
    session: Session,
    tissue_id: int | None,
    tracked_tissue_id: int | None,
) -> tuple[int, TrackedTissue | None]:
    tracked_tissue = None
    resolved_tissue_id = tissue_id
    if tracked_tissue_id is not None:
        tracked_tissue = session.get(TrackedTissue, tracked_tissue_id)
        if not tracked_tissue:
            raise HTTPException(status_code=404, detail="Tracked tissue not found")
        if resolved_tissue_id is not None and resolved_tissue_id != tracked_tissue.tissue_id:
            raise HTTPException(status_code=400, detail="tissue_id does not match tracked_tissue_id")
        resolved_tissue_id = tracked_tissue.tissue_id
    if resolved_tissue_id is None:
        raise HTTPException(status_code=400, detail="tissue_id or tracked_tissue_id is required")
    tissue = session.get(Tissue, resolved_tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    return resolved_tissue_id, tracked_tissue


def _validate_protocol_stage(protocol_id: str, stage_id: str) -> dict:
    try:
        protocol = get_rehab_protocol(protocol_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not any(stage["id"] == stage_id for stage in protocol["stages"]):
        raise HTTPException(status_code=400, detail=f"Unknown stage '{stage_id}' for protocol '{protocol_id}'")
    return protocol


def _serialize_tissue(
    tissue: Tissue,
    model_config: TissueModelConfig | None,
    *,
    tracked_rows: list[TrackedTissue],
) -> dict:
    return {
        "id": tissue.id,
        "name": tissue.name,
        "display_name": tissue.display_name,
        "type": tissue.type,
        "tracking_mode": tissue.tracking_mode,
        "region": tissue.region,
        "recovery_hours": tissue.recovery_hours,
        "notes": tissue.notes,
        "model_config": _serialize_model_config(model_config),
        "tracked_tissues": [
            {
                "id": row.id,
                "side": row.side,
                "display_name": row.display_name,
                "active": row.active,
            }
            for row in tracked_rows
        ],
    }


def _serialize_condition(condition: TissueCondition | None) -> dict | None:
    if condition is None:
        return None
    return {
        "id": condition.id,
        "status": condition.status,
        "severity": condition.severity,
        "max_loading_factor": condition.max_loading_factor,
        "recovery_hours_override": condition.recovery_hours_override,
        "rehab_protocol": condition.rehab_protocol,
        "notes": condition.notes,
        "updated_at": condition.updated_at,
    }


def _serialize_rehab_plan(session: Session, row: RehabPlan) -> dict:
    tracked = session.get(TrackedTissue, row.tracked_tissue_id)
    tissue = session.get(Tissue, tracked.tissue_id) if tracked else None
    protocol = get_rehab_protocol(row.protocol_id)
    stage = next((item for item in protocol["stages"] if item["id"] == row.stage_id), None)
    return {
        "id": row.id,
        "tracked_tissue_id": row.tracked_tissue_id,
        "tracked_tissue_display_name": tracked.display_name if tracked else None,
        "tissue_id": tissue.id if tissue else None,
        "tissue_name": tissue.name if tissue else None,
        "protocol_id": row.protocol_id,
        "protocol_title": protocol["title"],
        "stage_id": row.stage_id,
        "stage_label": stage["label"] if stage else row.stage_id,
        "status": row.status,
        "pain_monitoring_threshold": row.pain_monitoring_threshold,
        "max_next_day_flare": row.max_next_day_flare,
        "sessions_per_week_target": row.sessions_per_week_target,
        "max_weekly_set_progression": row.max_weekly_set_progression,
        "max_load_progression_pct": row.max_load_progression_pct,
        "notes": row.notes,
        "started_at": row.started_at,
        "updated_at": row.updated_at,
    }


def _serialize_tracked_tissue(
    tracked: TrackedTissue,
    tissue: Tissue,
    condition: TissueCondition | None,
    rehab_plan: RehabPlan | None,
) -> dict:
    return {
        "id": tracked.id,
        "tissue_id": tracked.tissue_id,
        "tissue_name": tissue.name,
        "tissue_display_name": tissue.display_name,
        "tissue_type": tissue.type,
        "region": tissue.region,
        "side": tracked.side,
        "display_name": tracked.display_name,
        "active": tracked.active,
        "notes": tracked.notes,
        "tracking_mode": tissue.tracking_mode,
        "recovery_hours": tissue.recovery_hours,
        "condition": _serialize_condition(condition),
        "active_rehab_plan": _serialize_rehab_plan_for_embedded(rehab_plan),
    }


def _serialize_rehab_plan_for_embedded(rehab_plan: RehabPlan | None) -> dict | None:
    if rehab_plan is None:
        return None
    protocol = get_rehab_protocol(rehab_plan.protocol_id)
    stage = next((item for item in protocol["stages"] if item["id"] == rehab_plan.stage_id), None)
    return {
        "id": rehab_plan.id,
        "protocol_id": rehab_plan.protocol_id,
        "protocol_title": protocol["title"],
        "stage_id": rehab_plan.stage_id,
        "stage_label": stage["label"] if stage else rehab_plan.stage_id,
        "status": rehab_plan.status,
        "pain_monitoring_threshold": rehab_plan.pain_monitoring_threshold,
        "max_next_day_flare": rehab_plan.max_next_day_flare,
        "sessions_per_week_target": rehab_plan.sessions_per_week_target,
        "max_weekly_set_progression": rehab_plan.max_weekly_set_progression,
        "max_load_progression_pct": rehab_plan.max_load_progression_pct,
        "notes": rehab_plan.notes,
    }


def _serialize_rehab_check_in(session: Session, row: RehabCheckIn) -> dict:
    tracked = session.get(TrackedTissue, row.tracked_tissue_id)
    return {
        "id": row.id,
        "tracked_tissue_id": row.tracked_tissue_id,
        "tracked_tissue_display_name": tracked.display_name if tracked else None,
        "rehab_plan_id": row.rehab_plan_id,
        "pain_0_10": row.pain_0_10,
        "stiffness_0_10": row.stiffness_0_10,
        "weakness_0_10": row.weakness_0_10,
        "neural_symptoms_0_10": row.neural_symptoms_0_10,
        "during_load_pain_0_10": row.during_load_pain_0_10,
        "next_day_flare": row.next_day_flare,
        "confidence_0_10": row.confidence_0_10,
        "notes": row.notes,
        "recorded_at": row.recorded_at,
    }


def _serialize_model_config(config: TissueModelConfig | None) -> dict | None:
    if config is None:
        return None
    return {
        "capacity_prior": config.capacity_prior,
        "recovery_tau_days": config.recovery_tau_days,
        "fatigue_tau_days": config.fatigue_tau_days,
        "collapse_drop_threshold": config.collapse_drop_threshold,
        "ramp_sensitivity": config.ramp_sensitivity,
        "risk_sensitivity": config.risk_sensitivity,
    }
