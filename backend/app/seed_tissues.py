"""Seed the tissue table with the complete human musculoskeletal system."""

from sqlmodel import Session, select

from app.models import Tissue

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
            recovery_hours=info.get("recovery_hours", 48),
            notes=info.get("notes"),
        )
        session.add(tissue)
    session.commit()
