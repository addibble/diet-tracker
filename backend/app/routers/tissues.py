"""Lean tissue CRUD router.

After the injury/rehab/tracked-tissue subsystem was removed, this router
exposes only basic tissue metadata needed by the admin UI. Rehab plans,
check-ins, tracked-tissue conditions, and protocol lookups are gone.

The underlying SQLModel tables (``TissueCondition``, ``RehabPlan``,
``RehabCheckIn``, ``TrackedTissue``, ``TissueModelConfig``) remain so
prod data is preserved for future analysis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.models import Tissue
from app.tissue_regions import load_tissue_regions, primary_region_from_regions
from app.workout_queries import get_current_tissues

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


@router.get("")
def list_tissues(
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissues = list(get_current_tissues(session))
    regions_by_tissue = load_tissue_regions(session, tissues=tissues)
    return [
        _serialize_tissue(t, regions=regions_by_tissue.get(t.id, ()))
        for t in tissues
    ]


@router.get("/{tissue_id:int}")
def get_tissue(
    tissue_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    regions_by_tissue = load_tissue_regions(session, tissue_ids=[tissue.id])
    return _serialize_tissue(tissue, regions=regions_by_tissue.get(tissue.id, ()))


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
        tracking_mode=data.tracking_mode or "paired",
        recovery_hours=data.recovery_hours,
        notes=data.notes,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)
    regions_by_tissue = load_tissue_regions(session, tissue_ids=[tissue.id])
    return _serialize_tissue(tissue, regions=regions_by_tissue.get(tissue.id, ()))


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
    session.commit()
    regions_by_tissue = load_tissue_regions(session, tissue_ids=[tissue.id])
    return _serialize_tissue(tissue, regions=regions_by_tissue.get(tissue.id, ()))


def _serialize_tissue(tissue: Tissue, *, regions: tuple[str, ...]) -> dict:
    return {
        "id": tissue.id,
        "name": tissue.name,
        "display_name": tissue.display_name,
        "type": tissue.type,
        "tracking_mode": tissue.tracking_mode,
        "region": primary_region_from_regions(regions),
        "regions": list(regions),
        "recovery_hours": tissue.recovery_hours,
        "notes": tissue.notes,
    }
