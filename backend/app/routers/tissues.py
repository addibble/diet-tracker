from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.database import get_session
from app.models import Tissue, TissueCondition, TissueModelConfig
from app.workout_queries import (
    get_all_current_conditions,
    get_current_tissue_condition,
)

router = APIRouter(prefix="/api/tissues", tags=["tissues"])


class TissueCreate(BaseModel):
    name: str
    display_name: str
    type: str = "muscle"
    recovery_hours: float = 48.0
    notes: str | None = None


class TissueUpdate(BaseModel):
    recovery_hours: float | None = None
    notes: str | None = None
    capacity_prior: float | None = None
    recovery_tau_days: float | None = None
    fatigue_tau_days: float | None = None
    collapse_drop_threshold: float | None = None
    ramp_sensitivity: float | None = None
    risk_sensitivity: float | None = None


class TissueConditionCreate(BaseModel):
    tissue_id: int
    status: str  # "healthy", "tender", "injured", "rehabbing"
    severity: int = 0
    max_loading_factor: float | None = None
    recovery_hours_override: float | None = None
    rehab_protocol: str | None = None
    notes: str | None = None


@router.get("")
def list_tissues(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    from app.workout_queries import get_current_tissues
    return [
        {
            "id": t.id,
            "name": t.name,
            "display_name": t.display_name,
            "type": t.type,
            "recovery_hours": t.recovery_hours,
            "notes": t.notes,
            "model_config": _serialize_model_config(session.get(TissueModelConfig, t.id)),
        }
        for t in get_current_tissues(session)
    ]


@router.get("/{tissue_id}")
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
    return {
        "id": tissue.id,
        "name": tissue.name,
        "display_name": tissue.display_name,
        "type": tissue.type,
        "recovery_hours": tissue.recovery_hours,
        "notes": tissue.notes,
        "model_config": _serialize_model_config(model_config),
        "condition": {
            "status": condition.status,
            "severity": condition.severity,
            "max_loading_factor": condition.max_loading_factor,
            "recovery_hours_override": condition.recovery_hours_override,
            "rehab_protocol": condition.rehab_protocol,
            "notes": condition.notes,
            "updated_at": condition.updated_at,
        } if condition else None,
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
        recovery_hours=data.recovery_hours,
        notes=data.notes,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)
    return {"id": tissue.id, "name": tissue.name, "display_name": tissue.display_name}


@router.put("/{tissue_id}")
def update_tissue(
    tissue_id: int,
    data: TissueUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    if data.recovery_hours is not None:
        tissue.recovery_hours = data.recovery_hours
    if data.notes is not None:
        tissue.notes = data.notes
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
    session.refresh(tissue)
    return {
        "id": tissue.id,
        "name": tissue.name,
        "recovery_hours": tissue.recovery_hours,
        "model_config": _serialize_model_config(config),
    }


# ── Tissue Conditions ──


@router.get("/conditions/current")
def list_conditions(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    conditions = get_all_current_conditions(session)
    result = []
    for c in conditions:
        tissue = session.get(Tissue, c.tissue_id)
        result.append({
            "tissue_id": c.tissue_id,
            "tissue_name": tissue.name if tissue else "unknown",
            "tissue_display_name": tissue.display_name if tissue else "unknown",
            "status": c.status,
            "severity": c.severity,
            "max_loading_factor": c.max_loading_factor,
            "recovery_hours_override": c.recovery_hours_override,
            "rehab_protocol": c.rehab_protocol,
            "notes": c.notes,
            "updated_at": c.updated_at,
        })
    return result


@router.get("/conditions/{tissue_id}/history")
def get_condition_history(
    tissue_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    conditions = session.exec(
        select(TissueCondition)
        .where(TissueCondition.tissue_id == tissue_id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": c.id,
            "status": c.status,
            "severity": c.severity,
            "max_loading_factor": c.max_loading_factor,
            "recovery_hours_override": c.recovery_hours_override,
            "rehab_protocol": c.rehab_protocol,
            "notes": c.notes,
            "updated_at": c.updated_at,
        }
        for c in conditions
    ]


@router.post("/conditions", status_code=201)
def create_condition(
    data: TissueConditionCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, data.tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    condition = TissueCondition(
        tissue_id=data.tissue_id,
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
        "status": condition.status,
        "severity": condition.severity,
        "updated_at": condition.updated_at,
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
