import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import get_current_user
from app.database import get_session
from app.models import Tissue, TissueRecoveryLog, TrainingExclusionWindow
from app.training_model import (
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


class RecoveryLogCreate(BaseModel):
    date: datetime.date
    tissue_id: int
    soreness_0_10: int = 0
    pain_0_10: int = 0
    readiness_0_10: int = 5
    source_session_id: int | None = None


@router.get("/summary")
def get_summary(
    as_of: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    return build_training_model_summary(session, as_of=as_of)


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


@router.post("/recovery-log", status_code=201)
def create_recovery_log(
    data: RecoveryLogCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    tissue = session.get(Tissue, data.tissue_id)
    if not tissue:
        raise HTTPException(status_code=404, detail="Tissue not found")
    row = TissueRecoveryLog(
        date=data.date,
        tissue_id=data.tissue_id,
        soreness_0_10=data.soreness_0_10,
        pain_0_10=data.pain_0_10,
        readiness_0_10=data.readiness_0_10,
        source_session_id=data.source_session_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "id": row.id,
        "date": row.date.isoformat(),
        "tissue_id": row.tissue_id,
        "soreness_0_10": row.soreness_0_10,
        "pain_0_10": row.pain_0_10,
        "readiness_0_10": row.readiness_0_10,
        "source_session_id": row.source_session_id,
    }
