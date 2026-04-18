"""Exercise/tissue laterality inference utilities.

Extracted from the legacy ``app.tracked_tissues`` module. These helpers
are purely name/role-based inference and have no coupling to the old
rehab / injury-tracking subsystem.
"""

from __future__ import annotations

import re

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


def infer_exercise_laterality(name: str) -> str:
    return (
        "unilateral"
        if any(pattern.search(name) for pattern in UNILATERAL_EXERCISE_PATTERNS)
        else "bilateral"
    )


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


def tissue_tracking_mode(name: str) -> str:
    from app.reference_exercises import normalize_reference_name

    normalized = normalize_reference_name(name).replace(" ", "_")
    return "center" if normalized in CENTER_TRACKING_TISSUES else "paired"


def seed_exercise_tissue_laterality_modes(session) -> None:
    from sqlmodel import select

    from app.models import Exercise, ExerciseTissue, Tissue

    exercises = {e.id: e for e in session.exec(select(Exercise)).all()}
    tissues = {t.id: t for t in session.exec(select(Tissue)).all()}
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
