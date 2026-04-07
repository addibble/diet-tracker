from __future__ import annotations

from collections.abc import Iterable

from app.reference_exercises import normalize_reference_name

CANONICAL_REGION_ORDER: tuple[str, ...] = (
    "calves",
    "tibs",
    "hamstrings",
    "quads",
    "inner_leg_adductor",
    "outer_leg_abductor",
    "glutes",
    "core",
    "lower_back",
    "upper_back",
    "chest",
    "triceps",
    "biceps",
    "forearms",
    "shoulders",
    "neck",
    "hands",
    "feet",
)

CANONICAL_REGION_LABELS: dict[str, str] = {
    "calves": "Calves",
    "tibs": "Tibs",
    "hamstrings": "Hamstrings",
    "quads": "Quads",
    "inner_leg_adductor": "Inner Leg (Adductor)",
    "outer_leg_abductor": "Outer Leg (Abductor)",
    "glutes": "Glutes",
    "core": "Core",
    "lower_back": "Lower Back",
    "upper_back": "Upper Back",
    "chest": "Chest",
    "triceps": "Triceps",
    "biceps": "Biceps",
    "forearms": "Forearms",
    "shoulders": "Shoulders",
    "neck": "Neck",
    "hands": "Hands",
    "feet": "Feet",
}


def _single(region: str, *names: str) -> dict[str, tuple[str, ...]]:
    return {name: (region,) for name in names}


def _multi(*regions: str) -> tuple[str, ...]:
    return regions


TISSUE_REGION_ASSOCIATIONS: dict[str, tuple[str, ...]] = {
    **_single(
        "chest",
        "pectoralis_major",
        "pec_clavicular_head",
        "pec_sternal_head",
        "pectoralis_minor",
    ),
    "serratus_anterior": _multi("chest", "shoulders"),
    **_single(
        "upper_back",
        "latissimus_dorsi",
        "rhomboids",
        "rhomboid_major",
        "rhomboid_minor",
        "mid_trapezius",
        "middle_trapezius",
        "lower_trapezius",
        "teres_major",
        "thoracic_spine",
    ),
    "upper_trapezius": _multi("upper_back", "neck"),
    "levator_scapulae": _multi("upper_back", "neck"),
    **_single(
        "lower_back",
        "erector_spinae",
        "iliocostalis",
        "longissimus",
        "spinalis",
        "multifidus",
        "quadratus_lumborum",
        "lumbar_spine",
    ),
    **_single(
        "shoulders",
        "anterior_deltoid",
        "lateral_deltoid",
        "posterior_deltoid",
        "deltoid_anterior",
        "deltoid_lateral",
        "deltoid_posterior",
        "rotator_cuff",
        "supraspinatus",
        "infraspinatus",
        "teres_minor",
        "subscapularis",
        "supraspinatus_tendon",
        "shoulder_joint",
    ),
    **_single(
        "biceps",
        "biceps_brachii",
        "biceps_long_head",
        "biceps_short_head",
        "brachialis",
        "biceps_long_head_tendon",
    ),
    **_single(
        "triceps",
        "triceps_brachii",
        "triceps_long_head",
        "triceps_lateral_head",
        "triceps_medial_head",
    ),
    "elbow_joint": _multi("triceps", "biceps", "forearms"),
    **_single(
        "forearms",
        "brachioradialis",
        "brachioradialis_tendon",
        "pronator_teres",
        "supinator",
    ),
    "wrist_flexors": _multi("forearms", "hands"),
    "wrist_extensors": _multi("forearms", "hands"),
    "flexor_carpi_radialis": _multi("forearms", "hands"),
    "flexor_carpi_ulnaris": _multi("forearms", "hands"),
    "palmaris_longus": _multi("forearms", "hands"),
    "flexor_digitorum_superficialis": _multi("forearms", "hands"),
    "flexor_digitorum_profundus": _multi("forearms", "hands"),
    "extensor_carpi_radialis_longus": _multi("forearms", "hands"),
    "extensor_carpi_radialis_brevis": _multi("forearms", "hands"),
    "extensor_carpi_ulnaris": _multi("forearms", "hands"),
    "extensor_digitorum": _multi("forearms", "hands"),
    "common_extensor_tendon": _multi("forearms", "hands"),
    "common_flexor_tendon": _multi("forearms", "hands"),
    "wrist_joint": _multi("forearms", "hands"),
    **_single(
        "core",
        "rectus_abdominis",
        "external_oblique",
        "internal_oblique",
        "transverse_abdominis",
        "diaphragm",
        "pelvic_floor",
        "psoas_major",
        "iliacus",
    ),
    **_single(
        "glutes",
        "gluteus_maximus",
        "piriformis",
    ),
    "gluteus_medius": _multi("glutes", "outer_leg_abductor"),
    "gluteus_minimus": _multi("glutes", "outer_leg_abductor"),
    "hip_joint": _multi("glutes", "inner_leg_adductor", "outer_leg_abductor"),
    "obturator_internus": _multi("glutes", "outer_leg_abductor"),
    "obturator_externus": _multi("glutes", "inner_leg_adductor"),
    **_single(
        "inner_leg_adductor",
        "adductors",
        "adductor_magnus",
        "adductor_longus",
        "adductor_brevis",
        "gracilis",
        "pectineus",
    ),
    **_single(
        "outer_leg_abductor",
        "hip_abductors",
        "tensor_fasciae_latae",
    ),
    "sartorius": _multi("quads", "outer_leg_abductor"),
    **_single(
        "quads",
        "rectus_femoris",
        "vastus_lateralis",
        "vastus_medialis",
        "vastus_intermedius",
        "patellar_tendon",
    ),
    "knee_joint": _multi("quads", "hamstrings"),
    **_single(
        "hamstrings",
        "biceps_femoris",
        "biceps_femoris_long_head",
        "biceps_femoris_short_head",
        "semitendinosus",
        "semimembranosus",
        "hamstring_tendons",
    ),
    **_single(
        "calves",
        "gastrocnemius",
        "gastrocnemius_medial_head",
        "gastrocnemius_lateral_head",
        "soleus",
        "popliteus",
    ),
    "achilles_tendon": _multi("calves", "feet"),
    "ankle_joint": _multi("calves", "feet"),
    **_single(
        "tibs",
        "tibialis_anterior",
        "fibularis_longus",
        "fibularis_brevis",
        "peroneus_longus",
        "peroneus_brevis",
    ),
    "tibialis_posterior": _multi("tibs", "feet"),
    "extensor_digitorum_longus": _multi("tibs", "feet"),
    "extensor_hallucis_longus": _multi("tibs", "feet"),
    **_single(
        "neck",
        "sternocleidomastoid",
        "cervical_spine",
        "scalenes",
    ),
}

_LEGACY_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "calves": ("calves",),
    "tibs": ("tibs",),
    "hamstrings": ("hamstrings",),
    "quads": ("quads",),
    "inner_leg_adductor": ("inner_leg_adductor",),
    "outer_leg_abductor": ("outer_leg_abductor",),
    "glutes": ("glutes",),
    "core": ("core",),
    "lower_back": ("lower_back",),
    "upper_back": ("upper_back",),
    "chest": ("chest",),
    "triceps": ("triceps",),
    "biceps": ("biceps",),
    "forearms": ("forearms",),
    "shoulders": ("shoulders",),
    "neck": ("neck",),
    "hands": ("hands",),
    "feet": ("feet",),
    "hips": ("glutes",),
    "arms": ("forearms",),
    "ankles": ("feet",),
    "knees": ("quads",),
    "other": (),
}


def normalize_tissue_name(value: str | None) -> str:
    if not value:
        return ""
    return normalize_reference_name(value).replace(" ", "_")


def canonical_region_names() -> tuple[str, ...]:
    return CANONICAL_REGION_ORDER


def canonical_region_label(region: str) -> str:
    return CANONICAL_REGION_LABELS.get(
        region,
        region.replace("_", " ").title(),
    )


def canonical_region_sort_key(region: str) -> tuple[int, str]:
    try:
        return (CANONICAL_REGION_ORDER.index(region), canonical_region_label(region).lower())
    except ValueError:
        return (len(CANONICAL_REGION_ORDER), canonical_region_label(region).lower())


def is_canonical_region(region: str) -> bool:
    return region in CANONICAL_REGION_LABELS


def _dedupe(regions: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for region in regions:
        if region in seen:
            continue
        seen.add(region)
        ordered.append(region)
    return tuple(ordered)


def _fallback_regions(region: str | None) -> tuple[str, ...]:
    normalized = normalize_tissue_name(region)
    if not normalized:
        return ()
    return _LEGACY_REGION_ALIASES.get(normalized, (normalized,) if is_canonical_region(normalized) else ())


def canonicalize_region(region: str | None) -> str | None:
    fallback = _fallback_regions(region)
    if fallback:
        return fallback[0]
    return None


def regions_for_tissue(
    name: str,
    *,
    display_name: str | None = None,
    fallback_region: str | None = None,
) -> tuple[str, ...]:
    candidates = [normalize_tissue_name(name)]
    display_key = normalize_tissue_name(display_name)
    if display_key:
        candidates.append(display_key)

    for candidate in candidates:
        regions = TISSUE_REGION_ASSOCIATIONS.get(candidate)
        if regions:
            return _dedupe(regions)

    fallback = _fallback_regions(fallback_region)
    if fallback:
        return fallback
    return ()


def primary_region_for_tissue(
    name: str,
    *,
    display_name: str | None = None,
    fallback_region: str | None = None,
) -> str:
    regions = regions_for_tissue(
        name,
        display_name=display_name,
        fallback_region=fallback_region,
    )
    if regions:
        return regions[0]
    fallback = normalize_tissue_name(fallback_region)
    if fallback and is_canonical_region(fallback):
        return fallback
    return "core"
