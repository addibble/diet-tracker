from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.database import get_session
from app.exercise_history import build_scheme_history, get_exercise_history_map
from app.exercise_loads import effective_bodyweight_component
from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    TissueRelationship,
    WorkoutSet,
)
from app.tracked_tissues import default_mapping_laterality_mode, infer_exercise_laterality
from app.workout_queries import get_current_exercise_tissues

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


class TissueMappingInput(BaseModel):
    tissue_id: int
    role: str = "primary"
    loading_factor: float = 1.0
    routing_factor: float | None = None
    fatigue_factor: float | None = None
    joint_strain_factor: float | None = None
    tendon_strain_factor: float | None = None
    laterality_mode: str | None = None


class ExerciseCreate(BaseModel):
    name: str
    equipment: str | None = None
    allow_heavy_loading: bool = True
    load_input_mode: str = "external_weight"
    laterality: str | None = None
    bodyweight_fraction: float = 0.0
    external_load_multiplier: float = 1.0
    variant_group: str | None = None
    grip_style: str = "none"
    grip_width: str = "none"
    support_style: str = "none"
    set_metric_mode: str = "reps"
    estimated_minutes_per_set: float = 2.0
    notes: str | None = None
    tissues: list[TissueMappingInput] = []


class ExerciseUpdate(BaseModel):
    name: str | None = None
    equipment: str | None = None
    allow_heavy_loading: bool | None = None
    load_input_mode: str | None = None
    laterality: str | None = None
    bodyweight_fraction: float | None = None
    external_load_multiplier: float | None = None
    variant_group: str | None = None
    grip_style: str | None = None
    grip_width: str | None = None
    support_style: str | None = None
    set_metric_mode: str | None = None
    estimated_minutes_per_set: float | None = None
    notes: str | None = None
    tissues: list[TissueMappingInput] | None = None


class ApplyMappingWarningInput(BaseModel):
    code: str
    source_tissue_id: int
    target_tissue_id: int


def _build_exercise_response(exercise: Exercise, session: Session) -> dict:
    mappings = get_current_exercise_tissues(session, exercise.id)  # type: ignore[arg-type]
    tissues_by_id = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    tissues = []
    for m in mappings:
        tissue = tissues_by_id.get(m.tissue_id)
        tissues.append({
            "tissue_id": m.tissue_id,
            "tissue_name": tissue.name if tissue else "unknown",
            "tissue_display_name": tissue.display_name if tissue else "unknown",
            "tissue_type": tissue.type if tissue else "muscle",
            "role": m.role,
            "loading_factor": m.loading_factor,
            "routing_factor": m.routing_factor,
            "fatigue_factor": m.fatigue_factor,
            "joint_strain_factor": m.joint_strain_factor,
            "tendon_strain_factor": m.tendon_strain_factor,
            "laterality_mode": m.laterality_mode,
        })
    mapping_warnings = _mapping_warnings_for_exercise(
        exercise=exercise,
        mappings=mappings,
        tissues_by_id=tissues_by_id,
        session=session,
    )
    return {
        "id": exercise.id,
        "name": exercise.name,
        "equipment": exercise.equipment,
        "allow_heavy_loading": exercise.allow_heavy_loading,
        "load_input_mode": exercise.load_input_mode,
        "laterality": exercise.laterality,
        "bodyweight_fraction": exercise.bodyweight_fraction,
        "external_load_multiplier": exercise.external_load_multiplier,
        "variant_group": exercise.variant_group,
        "grip_style": exercise.grip_style,
        "grip_width": exercise.grip_width,
        "support_style": exercise.support_style,
        "set_metric_mode": exercise.set_metric_mode,
        "estimated_minutes_per_set": exercise.estimated_minutes_per_set,
        "load_preview": _build_load_preview(exercise),
        "notes": exercise.notes,
        "created_at": exercise.created_at,
        "tissues": tissues,
        "mapping_warnings": mapping_warnings,
    }


def _build_load_preview(exercise: Exercise) -> dict:
    sample_bodyweight = 180.0
    bodyweight_component = effective_bodyweight_component(exercise, sample_bodyweight)
    sample_input = 50.0
    external_component = sample_input * (exercise.external_load_multiplier or 1.0)
    mode = exercise.load_input_mode or "external_weight"
    if mode == "bodyweight":
        effective_input = bodyweight_component
    elif mode == "mixed":
        effective_input = bodyweight_component + external_component
    elif mode == "assisted_bodyweight":
        effective_input = max(0.0, bodyweight_component - external_component)
    else:
        effective_input = external_component
    return {
        "sample_input_weight": sample_input if mode != "bodyweight" else None,
        "sample_bodyweight": sample_bodyweight,
        "bodyweight_component": round(bodyweight_component, 2),
        "effective_weight": round(effective_input, 2),
        "set_metric_mode": exercise.set_metric_mode or "reps",
        "external_load_multiplier": exercise.external_load_multiplier or 1.0,
    }


def _mapping_warnings_for_exercise(
    *,
    exercise: Exercise,
    mappings: list[ExerciseTissue],
    tissues_by_id: dict[int, Tissue],
    session: Session,
) -> list[dict]:
    warnings: list[dict] = []
    mapping_ids = {mapping.tissue_id for mapping in mappings}
    relationships = session.exec(select(TissueRelationship)).all()
    exercises_by_variant = {}
    if exercise.variant_group:
        siblings = session.exec(
            select(Exercise).where(Exercise.variant_group == exercise.variant_group)
        ).all()
        for sibling in siblings:
            if sibling.id == exercise.id:
                continue
            sibling_mappings = get_current_exercise_tissues(session, sibling.id)  # type: ignore[arg-type]
            exercises_by_variant[sibling.id] = sibling_mappings

    for mapping in mappings:
        tissue = tissues_by_id.get(mapping.tissue_id)
        if tissue is None:
            continue
        if exercise.laterality == "unilateral" and mapping.laterality_mode == "bilateral_equal":
            warnings.append({
                "code": "unilateral-bilateral-equal",
                "message": f"{exercise.name} is unilateral but {tissue.display_name} still uses bilateral_equal laterality.",
                "source_tissue_id": tissue.id,
                "target_tissue_id": tissue.id,
                "suggested_mapping": None,
            })
        if mapping.loading_factor < 0.3 and mapping.routing_factor < 0.3:
            continue
        related_rows = [
            row for row in relationships
            if row.source_tissue_id == mapping.tissue_id and row.required_for_mapping_warning
        ]
        for row in related_rows:
            if row.target_tissue_id in mapping_ids:
                continue
            target = tissues_by_id.get(row.target_tissue_id)
            if not target:
                continue
            warnings.append({
                "code": "missing-related-tissue",
                "message": (
                    f"{tissue.display_name} is mapped but related {target.display_name} "
                    f"({row.relationship_type}) is missing."
                ),
                "source_tissue_id": tissue.id,
                "target_tissue_id": target.id,
                "suggested_mapping": _suggested_mapping_from_source(
                    exercise=exercise,
                    source_mapping=mapping,
                    target_tissue=target,
                ),
            })

    if exercise.variant_group and exercises_by_variant:
        current_roles = {
            mapping.tissue_id: mapping.role
            for mapping in mappings
            if (mapping.loading_factor or 0) >= 0.25 or (mapping.routing_factor or 0) >= 0.25
        }
        for sibling_id, sibling_mappings in exercises_by_variant.items():
            sibling = session.get(Exercise, sibling_id)
            if sibling is None:
                continue
            sibling_roles = {
                mapping.tissue_id: mapping.role
                for mapping in sibling_mappings
                if (mapping.loading_factor or 0) >= 0.25 or (mapping.routing_factor or 0) >= 0.25
            }
            missing_from_current = [
                tissue_id
                for tissue_id in sibling_roles
                if tissue_id not in current_roles
            ]
            missing_from_sibling = [
                tissue_id
                for tissue_id in current_roles
                if tissue_id not in sibling_roles
            ]
            if not missing_from_current and not missing_from_sibling:
                continue
            target_tissue_id = (
                missing_from_current[0]
                if missing_from_current
                else missing_from_sibling[0]
            )
            warnings.append({
                "code": "variant-mapping-divergence",
                "message": (
                    f"{exercise.name} and sibling variant {sibling.name} in {exercise.variant_group} "
                    "have meaningfully different mapping coverage."
                ),
                "source_tissue_id": target_tissue_id,
                "target_tissue_id": target_tissue_id,
                "suggested_mapping": None,
            })
            break

    return warnings


def _mapping_factor_defaults_for_tissue(
    *,
    loading_factor: float,
    role: str,
    tissue_type: str | None,
) -> dict[str, float]:
    base = loading_factor or 1.0
    role_scale = {"primary": 1.0, "secondary": 0.65, "stabilizer": 0.35}.get(role, 0.5)
    routing = max(0.05, round(base * role_scale, 4))
    return {
        "routing_factor": routing,
        "fatigue_factor": max(0.05, round(routing * 0.9, 4)),
        "joint_strain_factor": (
            max(0.05, round(routing * 1.25, 4)) if tissue_type == "joint" else routing
        ),
        "tendon_strain_factor": (
            max(0.05, round(routing * 1.15, 4)) if tissue_type == "tendon" else routing
        ),
    }


def _suggested_mapping_from_source(
    *,
    exercise: Exercise,
    source_mapping: ExerciseTissue,
    target_tissue: Tissue,
) -> dict:
    defaults = _mapping_factor_defaults_for_tissue(
        loading_factor=source_mapping.loading_factor,
        role=source_mapping.role,
        tissue_type=target_tissue.type,
    )
    return {
        "role": source_mapping.role,
        "loading_factor": source_mapping.loading_factor,
        "routing_factor": defaults["routing_factor"],
        "fatigue_factor": defaults["fatigue_factor"],
        "joint_strain_factor": defaults["joint_strain_factor"],
        "tendon_strain_factor": defaults["tendon_strain_factor"],
        "laterality_mode": default_mapping_laterality_mode(
            exercise_laterality=exercise.laterality,
            tissue_type=target_tissue.type,
            role=source_mapping.role,
        ),
    }


@router.get("")
def list_exercises(
    search: str | None = Query(default=None),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    stmt = select(Exercise)
    if search:
        stmt = stmt.where(Exercise.name.contains(search))  # type: ignore[union-attr]
    stmt = stmt.order_by(Exercise.name)
    exercises = session.exec(stmt).all()
    return [_build_exercise_response(e, session) for e in exercises]


@router.get("/{exercise_id}")
def get_exercise(
    exercise_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return _build_exercise_response(exercise, session)


@router.post("/{exercise_id}/mapping-warnings/apply")
def apply_mapping_warning(
    exercise_id: int,
    data: ApplyMappingWarningInput,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    if data.code != "missing-related-tissue":
        raise HTTPException(status_code=400, detail="Only missing-related-tissue warnings are actionable")

    mappings = get_current_exercise_tissues(session, exercise.id)  # type: ignore[arg-type]
    if any(mapping.tissue_id == data.target_tissue_id for mapping in mappings):
        return _build_exercise_response(exercise, session)

    source_mapping = next(
        (mapping for mapping in mappings if mapping.tissue_id == data.source_tissue_id),
        None,
    )
    if source_mapping is None:
        raise HTTPException(status_code=400, detail="Source mapping not found")

    tissues_by_id = {tissue.id: tissue for tissue in session.exec(select(Tissue)).all()}
    target_tissue = tissues_by_id.get(data.target_tissue_id)
    if target_tissue is None:
        raise HTTPException(status_code=400, detail="Target tissue not found")

    warnings = _mapping_warnings_for_exercise(
        exercise=exercise,
        mappings=mappings,
        tissues_by_id=tissues_by_id,
        session=session,
    )
    matching_warning = next(
        (
            warning
            for warning in warnings
            if warning["code"] == data.code
            and warning["source_tissue_id"] == data.source_tissue_id
            and warning["target_tissue_id"] == data.target_tissue_id
        ),
        None,
    )
    if matching_warning is None:
        raise HTTPException(status_code=400, detail="Warning is no longer actionable")

    suggested_mapping = matching_warning.get("suggested_mapping") or _suggested_mapping_from_source(
        exercise=exercise,
        source_mapping=source_mapping,
        target_tissue=target_tissue,
    )
    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=target_tissue.id,
            role=suggested_mapping["role"],
            loading_factor=suggested_mapping["loading_factor"],
            routing_factor=suggested_mapping["routing_factor"],
            fatigue_factor=suggested_mapping["fatigue_factor"],
            joint_strain_factor=suggested_mapping["joint_strain_factor"],
            tendon_strain_factor=suggested_mapping["tendon_strain_factor"],
            laterality_mode=suggested_mapping["laterality_mode"],
        )
    )
    session.commit()
    return _build_exercise_response(exercise, session)


@router.post("", status_code=201)
def create_exercise(
    data: ExerciseCreate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    existing = session.exec(select(Exercise).where(Exercise.name == data.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Exercise already exists")
    exercise = Exercise(
        name=data.name,
        equipment=data.equipment,
        allow_heavy_loading=data.allow_heavy_loading,
        load_input_mode=data.load_input_mode,
        laterality=data.laterality or infer_exercise_laterality(data.name),
        bodyweight_fraction=data.bodyweight_fraction,
        external_load_multiplier=data.external_load_multiplier,
        variant_group=data.variant_group,
        grip_style=data.grip_style,
        grip_width=data.grip_width,
        support_style=data.support_style,
        set_metric_mode=data.set_metric_mode,
        estimated_minutes_per_set=data.estimated_minutes_per_set,
        notes=data.notes,
    )
    session.add(exercise)
    session.commit()
    session.refresh(exercise)
    for t in data.tissues:
        tissue = session.get(Tissue, t.tissue_id)
        if not tissue:
            raise HTTPException(status_code=400, detail=f"Tissue {t.tissue_id} not found")
        session.add(ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=t.tissue_id,
            role=t.role,
            loading_factor=t.loading_factor,
            routing_factor=t.routing_factor if t.routing_factor is not None else t.loading_factor,
            fatigue_factor=t.fatigue_factor if t.fatigue_factor is not None else t.loading_factor,
            joint_strain_factor=t.joint_strain_factor if t.joint_strain_factor is not None else t.loading_factor,
            tendon_strain_factor=t.tendon_strain_factor if t.tendon_strain_factor is not None else t.loading_factor,
            laterality_mode=t.laterality_mode or default_mapping_laterality_mode(
                exercise_laterality=exercise.laterality,
                tissue_type=tissue.type,
                role=t.role,
            ),
        ))
    session.commit()
    return _build_exercise_response(exercise, session)


@router.put("/{exercise_id}")
def update_exercise(
    exercise_id: int,
    data: ExerciseUpdate,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    if data.name is not None:
        exercise.name = data.name
    if data.equipment is not None:
        exercise.equipment = data.equipment
    if data.allow_heavy_loading is not None:
        exercise.allow_heavy_loading = data.allow_heavy_loading
    if data.load_input_mode is not None:
        exercise.load_input_mode = data.load_input_mode
    if data.laterality is not None:
        exercise.laterality = data.laterality
    if data.bodyweight_fraction is not None:
        exercise.bodyweight_fraction = data.bodyweight_fraction
    if data.external_load_multiplier is not None:
        exercise.external_load_multiplier = data.external_load_multiplier
    if data.variant_group is not None:
        exercise.variant_group = data.variant_group
    if data.grip_style is not None:
        exercise.grip_style = data.grip_style
    if data.grip_width is not None:
        exercise.grip_width = data.grip_width
    if data.support_style is not None:
        exercise.support_style = data.support_style
    if data.set_metric_mode is not None:
        exercise.set_metric_mode = data.set_metric_mode
    if data.estimated_minutes_per_set is not None:
        exercise.estimated_minutes_per_set = data.estimated_minutes_per_set
    if data.notes is not None:
        exercise.notes = data.notes
    session.add(exercise)
    session.commit()
    if data.tissues is not None:
        # Delete existing mappings and replace
        old = session.exec(
            select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
        ).all()
        for et in old:
            session.delete(et)
        session.flush()
        for t in data.tissues:
            tissue = session.get(Tissue, t.tissue_id)
            if not tissue:
                raise HTTPException(status_code=400, detail=f"Tissue {t.tissue_id} not found")
            session.add(ExerciseTissue(
                exercise_id=exercise.id,
                tissue_id=t.tissue_id,
                role=t.role,
                loading_factor=t.loading_factor,
                routing_factor=t.routing_factor if t.routing_factor is not None else t.loading_factor,
                fatigue_factor=t.fatigue_factor if t.fatigue_factor is not None else t.loading_factor,
                joint_strain_factor=t.joint_strain_factor if t.joint_strain_factor is not None else t.loading_factor,
                tendon_strain_factor=t.tendon_strain_factor if t.tendon_strain_factor is not None else t.loading_factor,
                laterality_mode=t.laterality_mode or default_mapping_laterality_mode(
                    exercise_laterality=exercise.laterality,
                    tissue_type=tissue.type,
                    role=t.role,
                ),
            ))
        session.commit()
    return _build_exercise_response(exercise, session)


@router.delete("/{exercise_id}", status_code=204)
def delete_exercise(
    exercise_id: int,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")
    # Delete related sets, exercise_tissues, program_day_exercises
    for s in session.exec(select(WorkoutSet).where(WorkoutSet.exercise_id == exercise_id)).all():
        session.delete(s)
    ex_tissues = session.exec(
        select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise_id)
    ).all()
    for et in ex_tissues:
        session.delete(et)
    session.delete(exercise)
    session.commit()


@router.get("/{exercise_id}/history")
def get_exercise_history(
    exercise_id: int,
    limit: int = Query(default=20, ge=1, le=500),
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
):
    exercise = session.get(Exercise, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    sessions_out = get_exercise_history_map(session, [exercise_id], limit=limit).get(exercise_id, [])

    return {
        "exercise": _build_exercise_response(exercise, session),
        "sessions": sessions_out,
        "scheme_history": build_scheme_history(sessions_out),
    }
