"""Seed the tissue table with the complete human musculoskeletal system."""

from datetime import date

from sqlmodel import Session, select

from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    TissueModelConfig,
    TrainingExclusionWindow,
)
from app.reference_exercises import (
    REFERENCE_EXERCISE_FIXUPS,
    TISSUE_RECOVERY_HOURS_FIXUPS,
    normalize_reference_name,
)

# Flat tissue list: {name: {type, recovery_hours, display_name?}}
# display_name is auto-generated from name if not provided.
TISSUES: list[dict] = [
    # ── Chest ──
    {"name": "pectoralis_major", "type": "muscle", "recovery_hours": 48},
    {"name": "pec_clavicular_head", "type": "muscle", "recovery_hours": 48},
    {"name": "pec_sternal_head", "type": "muscle", "recovery_hours": 48},
    {"name": "pectoralis_minor", "type": "muscle", "recovery_hours": 48},
    {"name": "serratus_anterior", "type": "muscle", "recovery_hours": 48},

    # ── Upper Back ──
    {"name": "latissimus_dorsi", "type": "muscle", "recovery_hours": 48},
    {"name": "rhomboid_major", "type": "muscle", "recovery_hours": 48},
    {"name": "rhomboid_minor", "type": "muscle", "recovery_hours": 48},
    {"name": "upper_trapezius", "type": "muscle", "recovery_hours": 48},
    {"name": "mid_trapezius", "type": "muscle", "recovery_hours": 48},
    {"name": "lower_trapezius", "type": "muscle", "recovery_hours": 48},
    {"name": "teres_major", "type": "muscle", "recovery_hours": 48},
    {"name": "levator_scapulae", "type": "muscle", "recovery_hours": 48},

    # ── Lower Back ──
    {"name": "iliocostalis", "type": "muscle", "recovery_hours": 72},
    {"name": "longissimus", "type": "muscle", "recovery_hours": 72},
    {"name": "spinalis", "type": "muscle", "recovery_hours": 72},
    {"name": "quadratus_lumborum", "type": "muscle", "recovery_hours": 72},
    {"name": "multifidus", "type": "muscle", "recovery_hours": 72},

    # ── Shoulders ──
    {"name": "anterior_deltoid", "type": "muscle", "recovery_hours": 48},
    {"name": "lateral_deltoid", "type": "muscle", "recovery_hours": 48},
    {"name": "posterior_deltoid", "type": "muscle", "recovery_hours": 48},
    {"name": "supraspinatus", "type": "muscle", "recovery_hours": 72},
    {"name": "infraspinatus", "type": "muscle", "recovery_hours": 72},
    {"name": "teres_minor", "type": "muscle", "recovery_hours": 72},
    {"name": "subscapularis", "type": "muscle", "recovery_hours": 72},
    {"name": "supraspinatus_tendon", "type": "tendon", "recovery_hours": 72},

    # ── Biceps ──
    {"name": "biceps_long_head", "type": "muscle", "recovery_hours": 48},
    {"name": "biceps_short_head", "type": "muscle", "recovery_hours": 48},
    {"name": "brachialis", "type": "muscle", "recovery_hours": 48},
    {"name": "biceps_long_head_tendon", "type": "tendon", "recovery_hours": 72},

    # ── Triceps ──
    {"name": "triceps_long_head", "type": "muscle", "recovery_hours": 48},
    {"name": "triceps_lateral_head", "type": "muscle", "recovery_hours": 48},
    {"name": "triceps_medial_head", "type": "muscle", "recovery_hours": 48},

    # ── Forearms ──
    {"name": "brachioradialis", "type": "muscle", "recovery_hours": 72},
    {"name": "flexor_carpi_radialis", "type": "muscle", "recovery_hours": 48},
    {"name": "flexor_carpi_ulnaris", "type": "muscle", "recovery_hours": 48},
    {"name": "palmaris_longus", "type": "muscle", "recovery_hours": 48},
    {"name": "flexor_digitorum_superficialis", "type": "muscle", "recovery_hours": 48},
    {"name": "flexor_digitorum_profundus", "type": "muscle", "recovery_hours": 48},
    {"name": "extensor_carpi_radialis_longus", "type": "muscle", "recovery_hours": 48},
    {"name": "extensor_carpi_radialis_brevis", "type": "muscle", "recovery_hours": 48},
    {"name": "extensor_carpi_ulnaris", "type": "muscle", "recovery_hours": 48},
    {"name": "extensor_digitorum", "type": "muscle", "recovery_hours": 48},
    {"name": "pronator_teres", "type": "muscle", "recovery_hours": 48},
    {"name": "supinator", "type": "muscle", "recovery_hours": 48},
    {"name": "common_extensor_tendon", "type": "tendon", "recovery_hours": 72},
    {"name": "common_flexor_tendon", "type": "tendon", "recovery_hours": 72},

    # ── Quads ──
    {"name": "rectus_femoris", "type": "muscle", "recovery_hours": 72},
    {"name": "vastus_lateralis", "type": "muscle", "recovery_hours": 72},
    {"name": "vastus_medialis", "type": "muscle", "recovery_hours": 72},
    {"name": "vastus_intermedius", "type": "muscle", "recovery_hours": 72},
    {"name": "patellar_tendon", "type": "tendon", "recovery_hours": 72},

    # ── Hamstrings ──
    {"name": "biceps_femoris_long_head", "type": "muscle", "recovery_hours": 72},
    {"name": "biceps_femoris_short_head", "type": "muscle", "recovery_hours": 72},
    {"name": "semitendinosus", "type": "muscle", "recovery_hours": 72},
    {"name": "semimembranosus", "type": "muscle", "recovery_hours": 72},
    {"name": "hamstring_tendons", "type": "tendon", "recovery_hours": 72},

    # ── Glutes ──
    {"name": "gluteus_maximus", "type": "muscle", "recovery_hours": 72},
    {"name": "gluteus_medius", "type": "muscle", "recovery_hours": 72},
    {"name": "gluteus_minimus", "type": "muscle", "recovery_hours": 72},

    # ── Calves ──
    {"name": "gastrocnemius_medial_head", "type": "muscle", "recovery_hours": 48},
    {"name": "gastrocnemius_lateral_head", "type": "muscle", "recovery_hours": 48},
    {"name": "soleus", "type": "muscle", "recovery_hours": 48},
    {"name": "achilles_tendon", "type": "tendon", "recovery_hours": 72},

    # ── Hip Adductors ──
    {"name": "adductor_magnus", "type": "muscle", "recovery_hours": 48},
    {"name": "adductor_longus", "type": "muscle", "recovery_hours": 48},
    {"name": "adductor_brevis", "type": "muscle", "recovery_hours": 48},
    {"name": "gracilis", "type": "muscle", "recovery_hours": 48},
    {"name": "pectineus", "type": "muscle", "recovery_hours": 48},

    # ── Hip Abductors ──
    {"name": "tensor_fasciae_latae", "type": "muscle", "recovery_hours": 48},

    # ── Hip Flexors ──
    {"name": "psoas_major", "type": "muscle", "recovery_hours": 48},
    {"name": "iliacus", "type": "muscle", "recovery_hours": 48},
    {"name": "sartorius", "type": "muscle", "recovery_hours": 48},

    # ── Lower Leg ──
    {"name": "tibialis_anterior", "type": "muscle", "recovery_hours": 48},
    {"name": "tibialis_posterior", "type": "muscle", "recovery_hours": 48},
    {"name": "peroneus_longus", "type": "muscle", "recovery_hours": 48},
    {"name": "peroneus_brevis", "type": "muscle", "recovery_hours": 48},
    {"name": "popliteus", "type": "muscle", "recovery_hours": 48},

    # ── Core ──
    {"name": "rectus_abdominis", "type": "muscle", "recovery_hours": 24},
    {"name": "internal_oblique", "type": "muscle", "recovery_hours": 24},
    {"name": "external_oblique", "type": "muscle", "recovery_hours": 24},
    {"name": "transverse_abdominis", "type": "muscle", "recovery_hours": 24},
    {"name": "diaphragm", "type": "muscle", "recovery_hours": 24},
    {"name": "pelvic_floor", "type": "muscle", "recovery_hours": 24},

    # ── Joints ──
    {"name": "shoulder_joint", "type": "joint", "recovery_hours": 72},
    {"name": "elbow_joint", "type": "joint", "recovery_hours": 72},
    {"name": "wrist_joint", "type": "joint", "recovery_hours": 48},
    {"name": "hip_joint", "type": "joint", "recovery_hours": 72},
    {"name": "knee_joint", "type": "joint", "recovery_hours": 72},
    {"name": "ankle_joint", "type": "joint", "recovery_hours": 72},
    {"name": "cervical_spine", "type": "joint", "recovery_hours": 72},
    {"name": "thoracic_spine", "type": "joint", "recovery_hours": 72},
    {"name": "lumbar_spine", "type": "joint", "recovery_hours": 72},
]


# Region mapping: tissue name -> body region
TISSUE_REGION_MAP: dict[str, str] = {
    # shoulders
    "anterior_deltoid": "shoulders",
    "lateral_deltoid": "shoulders",
    "posterior_deltoid": "shoulders",
    "deltoid_anterior": "shoulders",
    "deltoid_lateral": "shoulders",
    "deltoid_posterior": "shoulders",
    "rotator_cuff": "shoulders",
    "supraspinatus": "shoulders",
    "infraspinatus": "shoulders",
    "teres_minor": "shoulders",
    "subscapularis": "shoulders",
    "supraspinatus_tendon": "shoulders",
    "shoulder_joint": "shoulders",
    # upper_back
    "upper_trapezius": "upper_back",
    "mid_trapezius": "upper_back",
    "middle_trapezius": "upper_back",
    "lower_trapezius": "upper_back",
    "rhomboids": "upper_back",
    "rhomboid_major": "upper_back",
    "rhomboid_minor": "upper_back",
    "latissimus_dorsi": "upper_back",
    "teres_major": "upper_back",
    "levator_scapulae": "upper_back",
    "thoracic_spine": "upper_back",
    # lower_back
    "erector_spinae": "lower_back",
    "iliocostalis": "lower_back",
    "longissimus": "lower_back",
    "spinalis": "lower_back",
    "lumbar_spine": "lower_back",
    "multifidus": "lower_back",
    "quadratus_lumborum": "lower_back",
    # chest
    "pectoralis_major": "chest",
    "pec_sternal_head": "chest",
    "pec_clavicular_head": "chest",
    "pectoralis_minor": "chest",
    "serratus_anterior": "chest",
    # biceps
    "biceps_brachii": "biceps",
    "biceps_long_head": "biceps",
    "biceps_short_head": "biceps",
    "biceps_long_head_tendon": "biceps",
    "brachialis": "biceps",
    # triceps
    "triceps_brachii": "triceps",
    "triceps_long_head": "triceps",
    "triceps_lateral_head": "triceps",
    "triceps_medial_head": "triceps",
    "elbow_joint": "triceps",
    # forearms
    "brachioradialis": "forearms",
    "wrist_flexors": "forearms",
    "wrist_extensors": "forearms",
    "flexor_carpi_radialis": "forearms",
    "flexor_carpi_ulnaris": "forearms",
    "palmaris_longus": "forearms",
    "flexor_digitorum_superficialis": "forearms",
    "flexor_digitorum_profundus": "forearms",
    "extensor_carpi_radialis_longus": "forearms",
    "extensor_carpi_radialis_brevis": "forearms",
    "extensor_carpi_ulnaris": "forearms",
    "extensor_digitorum": "forearms",
    "pronator_teres": "forearms",
    "supinator": "forearms",
    "common_extensor_tendon": "forearms",
    "common_flexor_tendon": "forearms",
    "wrist_joint": "forearms",
    # core
    "rectus_abdominis": "core",
    "external_oblique": "core",
    "internal_oblique": "core",
    "transverse_abdominis": "core",
    "diaphragm": "core",
    "pelvic_floor": "core",
    # glutes
    "gluteus_maximus": "glutes",
    "gluteus_medius": "glutes",
    "gluteus_minimus": "glutes",
    "piriformis": "glutes",
    # hips
    "hip_flexors": "hips",
    "psoas_major": "hips",
    "iliacus": "hips",
    "sartorius": "hips",
    "hip_joint": "hips",
    "adductors": "hips",
    "adductor_magnus": "hips",
    "adductor_longus": "hips",
    "adductor_brevis": "hips",
    "gracilis": "hips",
    "pectineus": "hips",
    "tensor_fasciae_latae": "hips",
    # quads
    "rectus_femoris": "quads",
    "vastus_lateralis": "quads",
    "vastus_medialis": "quads",
    "vastus_intermedius": "quads",
    "knee_joint": "quads",
    "patellar_tendon": "quads",
    # hamstrings
    "biceps_femoris": "hamstrings",
    "biceps_femoris_long_head": "hamstrings",
    "biceps_femoris_short_head": "hamstrings",
    "semitendinosus": "hamstrings",
    "semimembranosus": "hamstrings",
    "hamstring_tendons": "hamstrings",
    # calves
    "gastrocnemius": "calves",
    "gastrocnemius_medial_head": "calves",
    "gastrocnemius_lateral_head": "calves",
    "soleus": "calves",
    "achilles_tendon": "calves",
    "popliteus": "calves",
    "ankle_joint": "calves",
    # tibs
    "tibialis_anterior": "tibs",
    "tibialis_posterior": "tibs",
    "fibularis_longus": "tibs",
    "fibularis_brevis": "tibs",
    "peroneus_longus": "tibs",
    "peroneus_brevis": "tibs",
    # neck
    "sternocleidomastoid": "neck",
    "cervical_spine": "neck",
    "scalenes": "neck",
}


def tissue_region(name: str) -> str:
    """Return the body region for a tissue name, defaulting to 'other'."""
    return TISSUE_REGION_MAP.get(name, "other")


def _name_to_display(name: str) -> str:
    """Convert snake_case name to Title Case display name."""
    return name.replace("_", " ").title()


def seed_tissues(session: Session) -> None:
    """Seed the tissue table if empty. Idempotent."""
    existing = session.exec(select(Tissue).limit(1)).first()
    if existing:
        return
    for info in TISSUES:
        tissue = Tissue(
            name=info["name"],
            display_name=info.get("display_name", _name_to_display(info["name"])),
            type=info["type"],
            region=tissue_region(info["name"]),
            recovery_hours=info.get("recovery_hours", 48),
            notes=info.get("notes"),
        )
        session.add(tissue)
    session.commit()


# Tissue mappings for exercises that the LLM tool previously failed to set.
# Leaned-back setup reduces TFL/Sartorius contribution on Hip Abduction Machine.
_HIP_MACHINE_MAPPINGS: dict[str, list[dict]] = {
    "Hip Adduction Machine": [
        {"name": "adductor_magnus",  "role": "primary",    "loading_factor": 1.0},
        {"name": "adductor_longus",  "role": "primary",    "loading_factor": 1.0},
        {"name": "adductor_brevis",  "role": "primary",    "loading_factor": 0.9},
        {"name": "gracilis",         "role": "secondary",  "loading_factor": 0.5},
        {"name": "pectineus",        "role": "secondary",  "loading_factor": 0.5},
        {"name": "hip_joint",        "role": "stabilizer", "loading_factor": 0.7},
        {"name": "pelvic_floor",     "role": "stabilizer", "loading_factor": 0.4},
    ],
    "Hip Abduction Machine": [
        {"name": "gluteus_medius",       "role": "primary",    "loading_factor": 1.0},
        {"name": "gluteus_minimus",      "role": "primary",    "loading_factor": 0.9},
        {"name": "tensor_fasciae_latae", "role": "secondary",  "loading_factor": 0.4},
        {"name": "sartorius",            "role": "secondary",  "loading_factor": 0.3},
        {"name": "hip_joint",            "role": "stabilizer", "loading_factor": 0.7},
        {"name": "pelvic_floor",         "role": "stabilizer", "loading_factor": 0.4},
    ],
}


def seed_tissue_regions(session: Session) -> None:
    """Backfill region field for existing tissues that still have 'other'."""
    tissues = session.exec(select(Tissue)).all()
    for t in tissues:
        expected = tissue_region(t.name)
        if t.region != expected:
            t.region = expected
            session.add(t)
    session.commit()


def seed_tissue_recovery_hours(session: Session) -> None:
    """Apply curated recovery-hour defaults for tissues with updated modeling."""
    tissues = session.exec(select(Tissue)).all()
    changed = False
    for tissue in tissues:
        target = TISSUE_RECOVERY_HOURS_FIXUPS.get(
            normalize_reference_name(tissue.name).replace(" ", "_")
        )
        if target is None:
            target = TISSUE_RECOVERY_HOURS_FIXUPS.get(
                normalize_reference_name(tissue.display_name).replace(" ", "_")
            )
        if target is None or tissue.recovery_hours == target:
            continue
        tissue.recovery_hours = target
        session.add(tissue)
        changed = True
    if changed:
        session.commit()


def seed_reference_exercises(session: Session) -> None:
    """Upsert curated exercise metadata/mappings that the model depends on."""
    tissue_lookup = _reference_tissue_lookup(session)
    changed = False
    for exercise_name, spec in REFERENCE_EXERCISE_FIXUPS.items():
        exercise = session.exec(
            select(Exercise).where(Exercise.name == exercise_name)
        ).first()
        if not exercise:
            exercise = Exercise(name=exercise_name)
            session.add(exercise)
            session.flush()
            changed = True

        metadata_fields = (
            "load_input_mode",
            "bodyweight_fraction",
            "external_load_multiplier",
            "estimated_minutes_per_set",
        )
        for field_name in metadata_fields:
            if field_name not in spec:
                continue
            target_value = spec[field_name]
            if getattr(exercise, field_name) != target_value:
                setattr(exercise, field_name, target_value)
                changed = True
        session.add(exercise)
        session.flush()

        mappings = spec.get("mappings")
        if mappings is None:
            continue

        existing = session.exec(
            select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise.id)
        ).all()
        for row in existing:
            session.delete(row)
        session.flush()

        for mapping_spec in mappings:
            tissue = tissue_lookup.get(
                normalize_reference_name(str(mapping_spec["tissue"]))
            )
            if not tissue:
                continue
            session.add(
                ExerciseTissue(
                    exercise_id=exercise.id,
                    tissue_id=tissue.id,
                    role=str(mapping_spec["role"]),
                    loading_factor=float(mapping_spec["loading_factor"]),
                )
            )
        changed = True

    if changed:
        session.commit()


def seed_hip_machine_tissues(session: Session) -> None:
    """Apply tissue mappings for Hip Abduction/Adduction Machine exercises.

    Skips any exercise that already has mappings so user edits are preserved.
    Safe to call on every startup.
    """
    for exercise_name, tissue_specs in _HIP_MACHINE_MAPPINGS.items():
        exercise = session.exec(
            select(Exercise).where(Exercise.name == exercise_name)
        ).first()
        if not exercise:
            continue  # exercise not yet in this environment

        has_mappings = session.exec(
            select(ExerciseTissue).where(
                ExerciseTissue.exercise_id == exercise.id
            ).limit(1)
        ).first()
        if has_mappings:
            continue  # already mapped; don't overwrite user changes

        for spec in tissue_specs:
            tissue = session.exec(
                select(Tissue).where(Tissue.name == spec["name"])
            ).first()
            if not tissue:
                continue
            session.add(ExerciseTissue(
                exercise_id=exercise.id,
                tissue_id=tissue.id,
                role=spec["role"],
                loading_factor=spec["loading_factor"],
            ))
    session.commit()


def seed_exercise_tissue_model_defaults(session: Session) -> None:
    """Backfill newer exercise-tissue factors from legacy loading factors."""
    mappings = session.exec(select(ExerciseTissue)).all()
    for mapping in mappings:
        defaults = _exercise_tissue_factor_defaults(session, mapping)
        if _should_backfill_model_factors(session, mapping):
            mapping.routing_factor = defaults["routing_factor"]
            mapping.fatigue_factor = defaults["fatigue_factor"]
            mapping.joint_strain_factor = defaults["joint_strain_factor"]
            mapping.tendon_strain_factor = defaults["tendon_strain_factor"]
        else:
            if not mapping.routing_factor:
                mapping.routing_factor = defaults["routing_factor"]
            if not mapping.fatigue_factor:
                mapping.fatigue_factor = defaults["fatigue_factor"]
            if not mapping.joint_strain_factor:
                mapping.joint_strain_factor = defaults["joint_strain_factor"]
            if not mapping.tendon_strain_factor:
                mapping.tendon_strain_factor = defaults["tendon_strain_factor"]
        session.add(mapping)
    session.commit()


def seed_tissue_model_configs(session: Session) -> None:
    """Create per-tissue model config rows if missing."""
    tissues = session.exec(select(Tissue)).all()
    for tissue in tissues:
        existing = session.get(TissueModelConfig, tissue.id)
        if existing:
            continue
        if tissue.type == "joint":
            config = TissueModelConfig(
                tissue_id=tissue.id,
                capacity_prior=1.0,
                recovery_tau_days=4.0,
                fatigue_tau_days=3.0,
                collapse_drop_threshold=0.4,
                ramp_sensitivity=1.25,
                risk_sensitivity=1.35,
            )
        elif tissue.type == "tendon":
            config = TissueModelConfig(
                tissue_id=tissue.id,
                capacity_prior=1.0,
                recovery_tau_days=4.5,
                fatigue_tau_days=3.0,
                collapse_drop_threshold=0.4,
                ramp_sensitivity=1.15,
                risk_sensitivity=1.25,
            )
        else:
            config = TissueModelConfig(
                tissue_id=tissue.id,
                capacity_prior=1.0,
                recovery_tau_days=3.0,
                fatigue_tau_days=2.0,
                collapse_drop_threshold=0.45,
                ramp_sensitivity=1.0,
                risk_sensitivity=1.0,
            )
        session.add(config)
    session.commit()


def seed_default_training_exclusion_windows(session: Session) -> None:
    existing = session.exec(
        select(TrainingExclusionWindow).where(
            TrainingExclusionWindow.start_date == date(2025, 12, 16),
        )
    ).first()
    if existing:
        return
    session.add(
        TrainingExclusionWindow(
            start_date=date(2025, 12, 16),
            end_date=date(2025, 12, 31),
            kind="surgery",
            notes="Post-surgery recovery window excluded from overload learning.",
            exclude_from_model=True,
        )
    )
    session.commit()


def _is_tendon_tissue(session: Session, tissue_id: int) -> bool:
    tissue = session.get(Tissue, tissue_id)
    return bool(tissue and tissue.type == "tendon")


def _is_joint_tissue(session: Session, tissue_id: int) -> bool:
    tissue = session.get(Tissue, tissue_id)
    return bool(tissue and tissue.type == "joint")


def _exercise_tissue_factor_defaults(session: Session, mapping: ExerciseTissue) -> dict[str, float]:
    base = mapping.loading_factor or 1.0
    role_scale = {"primary": 1.0, "secondary": 0.65, "stabilizer": 0.35}.get(
        mapping.role,
        0.5,
    )
    routing = max(0.05, round(base * role_scale, 4))
    return {
        "routing_factor": routing,
        "fatigue_factor": max(0.05, round(routing * 0.9, 4)),
        "joint_strain_factor": (
            max(0.05, round(routing * 1.25, 4)) if _is_joint_tissue(session, mapping.tissue_id) else routing
        ),
        "tendon_strain_factor": (
            max(0.05, round(routing * 1.15, 4)) if _is_tendon_tissue(session, mapping.tissue_id) else routing
        ),
    }


def _should_backfill_model_factors(session: Session, mapping: ExerciseTissue) -> bool:
    if (
        mapping.routing_factor == 1.0
        and mapping.fatigue_factor == 1.0
        and mapping.joint_strain_factor == 1.0
        and mapping.tendon_strain_factor == 1.0
    ):
        if mapping.loading_factor != 1.0 or mapping.role != "primary":
            return True
        if _is_joint_tissue(session, mapping.tissue_id) or _is_tendon_tissue(session, mapping.tissue_id):
            return True
    return False


def _reference_tissue_lookup(session: Session) -> dict[str, Tissue]:
    lookup: dict[str, Tissue] = {}
    for tissue in session.exec(select(Tissue)).all():
        lookup[normalize_reference_name(tissue.name)] = tissue
        lookup[normalize_reference_name(tissue.display_name)] = tissue
    return lookup
