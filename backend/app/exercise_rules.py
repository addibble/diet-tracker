"""Business rules for exercise metadata, isolated from engine/migrations.

Currently hosts the heavy-loading policy that decides which exercises should
have `allow_heavy_loading=False` based on targeted tissues and name keywords.
The policy is used both to backfill the default on catalog load and to
document the intent in one place.
"""
from __future__ import annotations

from sqlmodel import Session, select

SHOULDER_HEAVY_DISABLE_THRESHOLD = 0.5
AB_HEAVY_DISABLE_THRESHOLD = 0.3

ABDOMINAL_TISSUE_NAMES: frozenset[str] = frozenset({
    "rectus_abdominis",
    "external_oblique",
    "internal_oblique",
    "transverse_abdominis",
})

SHOULDER_NAME_KEYWORDS: tuple[str, ...] = (
    "shoulder press",
    "overhead press",
    "arnold press",
    "lateral raise",
    "front raise",
    "rear delt",
    "rear-delt",
    "upright row",
    "face pull",
)

AB_NAME_KEYWORDS: tuple[str, ...] = (
    "plank",
    "crunch",
    "sit up",
    "sit-up",
    "leg raise",
    "flutter kick",
    "ab wheel",
    "oblique",
    "hollow body",
    "dead bug",
    "pallof",
    "chop",
    "v-up",
    "v up",
)


def should_disable_heavy_loading(
    *,
    exercise,
    mappings: list[tuple[object, object]],
) -> bool:
    """Return True if the exercise should be flagged `allow_heavy_loading=False`.

    Rules (in order):
      1. Primary shoulder-region mapping at or above the shoulder threshold,
         unless the same exercise also has a primary chest mapping at or above
         the threshold (compound pressing is OK).
      2. Any primary/secondary mapping onto an abdominal tissue at or above the
         abdominal threshold.
      3. Name keyword match (shoulder or ab lift names).
    """
    has_primary_chest = any(
        getattr(mapping, "role", None) == "primary"
        and getattr(tissue, "region", None) == "chest"
        and (
            (
                getattr(mapping, "loading_factor", None)
                or getattr(mapping, "routing_factor", 0.0)
                or 0.0
            )
            >= SHOULDER_HEAVY_DISABLE_THRESHOLD
        )
        for mapping, tissue in mappings
    )
    for mapping, tissue in mappings:
        if getattr(mapping, "role", None) not in {"primary", "secondary"}:
            continue
        mapping_factor = getattr(mapping, "loading_factor", None)
        if mapping_factor is None:
            mapping_factor = getattr(mapping, "routing_factor", 0.0) or 0.0
        region = getattr(tissue, "region", None)
        tissue_name = getattr(tissue, "name", "") or ""
        if (
            getattr(mapping, "role", None) == "primary"
            and region == "shoulders"
            and mapping_factor >= SHOULDER_HEAVY_DISABLE_THRESHOLD
            and not has_primary_chest
        ):
            return True
        if (
            tissue_name in ABDOMINAL_TISSUE_NAMES
            and mapping_factor >= AB_HEAVY_DISABLE_THRESHOLD
        ):
            return True

    exercise_name = (getattr(exercise, "name", "") or "").lower()
    return any(
        keyword in exercise_name
        for keyword in (*SHOULDER_NAME_KEYWORDS, *AB_NAME_KEYWORDS)
    )


def backfill_heavy_loading_defaults(session: Session) -> None:
    """Apply `should_disable_heavy_loading` across all exercises in `session`."""
    from app.models import Exercise, ExerciseTissue, Tissue

    mapping_rows = session.exec(
        select(ExerciseTissue, Tissue).join(
            Tissue, Tissue.id == ExerciseTissue.tissue_id
        )
    ).all()
    mappings_by_exercise: dict[int, list[tuple[object, object]]] = {}
    for mapping, tissue in mapping_rows:
        mappings_by_exercise.setdefault(mapping.exercise_id, []).append(
            (mapping, tissue)
        )

    updated = False
    for exercise in session.exec(select(Exercise)).all():
        if exercise.id is None:
            continue
        if not should_disable_heavy_loading(
            exercise=exercise,
            mappings=mappings_by_exercise.get(exercise.id, []),
        ):
            continue
        if exercise.allow_heavy_loading:
            exercise.allow_heavy_loading = False
            session.add(exercise)
            updated = True

    if updated:
        session.commit()
