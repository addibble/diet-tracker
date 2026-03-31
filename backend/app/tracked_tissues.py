from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    RehabPlan,
    Tissue,
    TissueCondition,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
)
from app.reference_exercises import normalize_reference_name
from app.workout_queries import session_trained_at

SIDE_ORDER = {"left": 0, "right": 1, "center": 2}
VALID_TRACKING_MODES = {"paired", "center"}
VALID_PERFORMED_SIDES = {"left", "right", "center", "bilateral"}
CENTER_TRACKING_TISSUES = {
    "cervical_spine",
    "thoracic_spine",
    "lumbar_spine",
    "diaphragm",
    "pelvic_floor",
    "rectus_abdominis",
    "transverse_abdominis",
}
UNILATERAL_EXERCISE_PATTERNS = (
    re.compile(r"\bsingle(?:\s|-)(?:arm|leg)\b", re.IGNORECASE),
    re.compile(r"\bone(?:\s|-)(?:arm|leg)\b", re.IGNORECASE),
    re.compile(r"\b(?:left|right)\s+only\b", re.IGNORECASE),
)
LEFT_PATTERN = re.compile(r"\bleft\b", re.IGNORECASE)
RIGHT_PATTERN = re.compile(r"\bright\b", re.IGNORECASE)


def tissue_tracking_mode(name: str) -> str:
    normalized = normalize_reference_name(name).replace(" ", "_")
    return "center" if normalized in CENTER_TRACKING_TISSUES else "paired"


def tracked_tissue_sides(tissue: Tissue) -> tuple[str, ...]:
    return ("center",) if tissue.tracking_mode == "center" else ("left", "right")


def tracked_tissue_display_name(tissue: Tissue, side: str) -> str:
    return tissue.display_name if side == "center" else f"{side.title()} {tissue.display_name}"


def infer_exercise_laterality(name: str) -> str:
    return "unilateral" if any(pattern.search(name) for pattern in UNILATERAL_EXERCISE_PATTERNS) else "bilateral"


def infer_performed_side_from_name(name: str) -> str | None:
    has_left = bool(LEFT_PATTERN.search(name))
    has_right = bool(RIGHT_PATTERN.search(name))
    if has_left and not has_right:
        return "left"
    if has_right and not has_left:
        return "right"
    return None


def default_mapping_laterality_mode(
    *,
    exercise_laterality: str,
    tissue_type: str,
    role: str,
) -> str:
    if exercise_laterality != "unilateral":
        return "bilateral_equal"
    if tissue_type == "muscle" and role in {"primary", "secondary"}:
        return "contralateral_carryover"
    return "selected_side_only"


def default_performed_side(
    *,
    exercise_name: str,
    exercise_laterality: str,
    provided_side: str | None,
) -> str | None:
    if provided_side in VALID_PERFORMED_SIDES:
        return provided_side
    inferred = infer_performed_side_from_name(exercise_name)
    if inferred is not None:
        return inferred
    if exercise_laterality == "bilateral":
        return "bilateral"
    return None


def seed_tracked_tissues(
    session: Session,
    *,
    force_inferred_mode: bool = False,
) -> None:
    tissues = session.exec(select(Tissue).order_by(Tissue.name)).all()
    changed = False
    for tissue in tissues:
        existing_rows = session.exec(
            select(TrackedTissue).where(TrackedTissue.tissue_id == tissue.id)
        ).all()
        expected_mode = tissue_tracking_mode(tissue.name)
        if tissue.tracking_mode not in VALID_TRACKING_MODES:
            tissue.tracking_mode = expected_mode
            tissue.updated_at = datetime.now(UTC)
            session.add(tissue)
            changed = True
        elif (
            force_inferred_mode
            and expected_mode == "center"
            and tissue.tracking_mode != expected_mode
        ):
            tissue.tracking_mode = expected_mode
            tissue.updated_at = datetime.now(UTC)
            session.add(tissue)
            changed = True

        existing_by_side = {row.side: row for row in existing_rows}
        desired_sides = set(tracked_tissue_sides(tissue))
        for side in desired_sides:
            display_name = tracked_tissue_display_name(tissue, side)
            row = existing_by_side.get(side)
            if row is None:
                session.add(
                    TrackedTissue(
                        tissue_id=tissue.id,
                        side=side,
                        display_name=display_name,
                        active=True,
                    )
                )
                changed = True
                continue
            if row.display_name != display_name or not row.active:
                row.display_name = display_name
                row.active = True
                row.updated_at = datetime.now(UTC)
                session.add(row)
                changed = True
        for side, row in existing_by_side.items():
            should_be_active = side in desired_sides
            if row.active != should_be_active:
                row.active = should_be_active
                row.updated_at = datetime.now(UTC)
                session.add(row)
                changed = True
    if changed:
        session.commit()


def seed_exercise_laterality(session: Session) -> None:
    exercises = session.exec(select(Exercise).order_by(Exercise.name)).all()
    changed = False
    for exercise in exercises:
        inferred = infer_exercise_laterality(exercise.name)
        if exercise.laterality not in {"bilateral", "unilateral", "either"}:
            exercise.laterality = inferred
            session.add(exercise)
            changed = True
        elif exercise.laterality == "bilateral" and inferred == "unilateral":
            exercise.laterality = inferred
            session.add(exercise)
            changed = True
    if changed:
        session.commit()


def seed_exercise_tissue_laterality_modes(session: Session) -> None:
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    mappings = session.exec(select(ExerciseTissue)).all()
    changed = False
    for mapping in mappings:
        exercise = exercises.get(mapping.exercise_id)
        tissue = tissues.get(mapping.tissue_id)
        target_mode = default_mapping_laterality_mode(
            exercise_laterality=exercise.laterality if exercise else "bilateral",
            tissue_type=tissue.type if tissue else "muscle",
            role=mapping.role,
        )
        if mapping.laterality_mode not in {
            "bilateral_equal",
            "selected_side_only",
            "selected_side_primary",
            "contralateral_carryover",
        }:
            mapping.laterality_mode = target_mode
            session.add(mapping)
            changed = True
        elif mapping.laterality_mode == "bilateral_equal" and target_mode != "bilateral_equal":
            mapping.laterality_mode = target_mode
            session.add(mapping)
            changed = True
    if changed:
        session.commit()


def backfill_workout_set_performed_side(session: Session) -> None:
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.performed_side.is_(None))
    ).all()
    changed = False
    for workout_set in sets:
        exercise = exercises.get(workout_set.exercise_id)
        if exercise is None:
            continue
        inferred_side = infer_performed_side_from_name(exercise.name)
        if inferred_side:
            workout_set.performed_side = inferred_side
            session.add(workout_set)
            changed = True
        elif exercise.laterality == "bilateral":
            workout_set.performed_side = "bilateral"
            session.add(workout_set)
            changed = True
    if changed:
        session.commit()


def backfill_tissue_conditions_to_tracked_tissues(session: Session) -> None:
    tracked_lookup = get_tracked_tissue_lookup(session)
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    rows = session.exec(
        select(TissueCondition).where(TissueCondition.tracked_tissue_id.is_(None))
    ).all()
    changed = False
    for row in rows:
        tissue = tissues.get(row.tissue_id)
        if tissue is None:
            continue
        if tissue.tracking_mode == "center":
            tracked = tracked_lookup.get((row.tissue_id, "center"))
            if tracked is None:
                continue
            row.tracked_tissue_id = tracked.id
            session.add(row)
            changed = True
            continue
        inferred_side = None
        haystacks = [row.notes or "", row.rehab_protocol or ""]
        for haystack in haystacks:
            if LEFT_PATTERN.search(haystack) and not RIGHT_PATTERN.search(haystack):
                inferred_side = "left"
                break
            if RIGHT_PATTERN.search(haystack) and not LEFT_PATTERN.search(haystack):
                inferred_side = "right"
                break
        if inferred_side is None:
            continue
        tracked = tracked_lookup.get((row.tissue_id, inferred_side))
        if tracked is None:
            continue
        row.tracked_tissue_id = tracked.id
        session.add(row)
        changed = True
    if changed:
        session.commit()


def get_tracked_tissue_lookup(session: Session, include_inactive: bool = False) -> dict[tuple[int, str], TrackedTissue]:
    stmt = select(TrackedTissue)
    if not include_inactive:
        stmt = stmt.where(TrackedTissue.active.is_(True))
    rows = session.exec(stmt).all()
    return {(row.tissue_id, row.side): row for row in rows}


def get_current_tracked_tissue_condition(
    session: Session, tracked_tissue_id: int
) -> TissueCondition | None:
    stmt = (
        select(TissueCondition)
        .where(TissueCondition.tracked_tissue_id == tracked_tissue_id)
        .order_by(col(TissueCondition.updated_at).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def get_all_current_tracked_conditions(session: Session) -> dict[int, TissueCondition]:
    sub = (
        select(
            TissueCondition.tracked_tissue_id,
            func.max(TissueCondition.updated_at).label("max_updated"),
        )
        .where(TissueCondition.tracked_tissue_id.is_not(None))
        .group_by(TissueCondition.tracked_tissue_id)
        .subquery()
    )
    stmt = (
        select(TissueCondition)
        .join(
            sub,
            (TissueCondition.tracked_tissue_id == sub.c.tracked_tissue_id)
            & (TissueCondition.updated_at == sub.c.max_updated),
        )
    )
    rows = session.exec(stmt).all()
    return {
        row.tracked_tissue_id: row
        for row in rows
        if row.tracked_tissue_id is not None
    }


def get_active_rehab_plans_by_tracked_tissue(session: Session) -> dict[int, RehabPlan]:
    rows = session.exec(
        select(RehabPlan)
        .where(RehabPlan.status == "active")
        .order_by(col(RehabPlan.updated_at).desc())
    ).all()
    result: dict[int, RehabPlan] = {}
    for row in rows:
        result.setdefault(row.tracked_tissue_id, row)
    return result


def list_tracked_tissues(session: Session, include_inactive: bool = False) -> list[TrackedTissue]:
    stmt = select(TrackedTissue)
    if not include_inactive:
        stmt = stmt.where(TrackedTissue.active.is_(True))
    rows = session.exec(stmt).all()
    tissue_lookup = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    return sorted(
        rows,
        key=lambda row: (
            tissue_lookup.get(row.tissue_id).name if tissue_lookup.get(row.tissue_id) else "",
            SIDE_ORDER.get(row.side, 99),
        ),
    )


def tracked_tissue_side_weights(
    *,
    exercise_laterality: str,
    laterality_mode: str,
    performed_side: str | None,
    tissue_tracking_mode: str,
) -> tuple[dict[str, float], dict[str, float]]:
    if tissue_tracking_mode == "center":
        return {"center": 1.0}, {}

    selected_side = performed_side if performed_side in {"left", "right"} else None
    if laterality_mode == "contralateral_carryover":
        if selected_side is None:
            return {"left": 0.5, "right": 0.5}, {}
        opposite = "right" if selected_side == "left" else "left"
        return {selected_side: 1.0}, {opposite: 0.25}

    if laterality_mode == "selected_side_primary":
        if selected_side is None:
            return {"left": 0.5, "right": 0.5}, {}
        opposite = "right" if selected_side == "left" else "left"
        return {selected_side: 1.0, opposite: 0.35}, {}

    if laterality_mode == "selected_side_only":
        if selected_side is None:
            if performed_side == "bilateral" or exercise_laterality == "bilateral":
                return {"left": 1.0, "right": 1.0}, {}
            return {"left": 0.5, "right": 0.5}, {}
        return {selected_side: 1.0}, {}

    return {"left": 1.0, "right": 1.0}, {}


def tracked_volume_and_last_trained(
    *,
    session: Session,
    set_rows: list[tuple[WorkoutSession, WorkoutSet, float]],
) -> tuple[
    dict[int, float],
    dict[int, float],
    dict[int, datetime],
]:
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    tracked_lookup = get_tracked_tissue_lookup(session)
    mappings_by_exercise: dict[int, list[ExerciseTissue]] = defaultdict(list)
    for mapping in session.exec(select(ExerciseTissue)).all():
        mappings_by_exercise[mapping.exercise_id].append(mapping)

    volume_7d: dict[int, float] = defaultdict(float)
    cross_education_7d: dict[int, float] = defaultdict(float)
    last_trained: dict[int, datetime] = {}
    for workout_session, workout_set, effective_load in set_rows:
        if effective_load <= 0:
            continue
        exercise = exercises.get(workout_set.exercise_id)
        if exercise is None:
            continue
        trained_at = session_trained_at(workout_session)
        for mapping in mappings_by_exercise.get(workout_set.exercise_id, []):
            tissue = tissues.get(mapping.tissue_id)
            if tissue is None:
                continue
            load_weights, cross_weights = tracked_tissue_side_weights(
                exercise_laterality=exercise.laterality,
                laterality_mode=mapping.laterality_mode,
                performed_side=workout_set.performed_side,
                tissue_tracking_mode=tissue.tracking_mode,
            )
            routing = mapping.routing_factor or mapping.loading_factor or 1.0
            for side, weight in load_weights.items():
                tracked = tracked_lookup.get((tissue.id, side))
                if tracked is None:
                    continue
                contribution = effective_load * routing * weight
                volume_7d[tracked.id] += contribution
                previous = last_trained.get(tracked.id)
                if previous is None or trained_at > previous:
                    last_trained[tracked.id] = trained_at
            for side, weight in cross_weights.items():
                tracked = tracked_lookup.get((tissue.id, side))
                if tracked is None:
                    continue
                cross_education_7d[tracked.id] += effective_load * routing * weight
    return dict(volume_7d), dict(cross_education_7d), last_trained
