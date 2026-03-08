"""Seed the tissue table with the complete human musculoskeletal system."""

from sqlmodel import Session, select

from app.models import Tissue

# Nested tissue hierarchy: {name: {type, recovery_hours, display_name?, children?}}
# display_name is auto-generated from name if not provided.
TISSUE_TREE: dict = {
    "upper_body": {"type": "tissue_group", "recovery_hours": 48, "children": {
        "chest": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "pectoralis_major": {"type": "muscle", "recovery_hours": 48, "children": {
                "pec_clavicular_head": {"type": "muscle", "recovery_hours": 48},
                "pec_sternal_head": {"type": "muscle", "recovery_hours": 48},
            }},
            "pectoralis_minor": {"type": "muscle", "recovery_hours": 48},
            "serratus_anterior": {"type": "muscle", "recovery_hours": 48},
        }},
        "upper_back": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "latissimus_dorsi": {"type": "muscle", "recovery_hours": 48},
            "rhomboids": {"type": "muscle", "recovery_hours": 48, "children": {
                "rhomboid_major": {"type": "muscle", "recovery_hours": 48},
                "rhomboid_minor": {"type": "muscle", "recovery_hours": 48},
            }},
            "trapezius": {"type": "muscle", "recovery_hours": 48, "children": {
                "upper_trapezius": {"type": "muscle", "recovery_hours": 48},
                "mid_trapezius": {"type": "muscle", "recovery_hours": 48},
                "lower_trapezius": {"type": "muscle", "recovery_hours": 48},
            }},
            "teres_major": {"type": "muscle", "recovery_hours": 48},
            "levator_scapulae": {"type": "muscle", "recovery_hours": 48},
        }},
        "lower_back": {"type": "tissue_group", "recovery_hours": 72, "children": {
            "erector_spinae": {"type": "muscle", "recovery_hours": 72, "children": {
                "iliocostalis": {"type": "muscle", "recovery_hours": 72},
                "longissimus": {"type": "muscle", "recovery_hours": 72},
                "spinalis": {"type": "muscle", "recovery_hours": 72},
            }},
            "quadratus_lumborum": {"type": "muscle", "recovery_hours": 72},
            "multifidus": {"type": "muscle", "recovery_hours": 72},
        }},
        "shoulders": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "deltoid": {"type": "muscle", "recovery_hours": 48, "children": {
                "anterior_deltoid": {"type": "muscle", "recovery_hours": 48},
                "lateral_deltoid": {"type": "muscle", "recovery_hours": 48},
                "posterior_deltoid": {"type": "muscle", "recovery_hours": 48},
            }},
            "rotator_cuff": {"type": "tissue_group", "recovery_hours": 72, "children": {
                "supraspinatus": {"type": "muscle", "recovery_hours": 72},
                "infraspinatus": {"type": "muscle", "recovery_hours": 72},
                "teres_minor": {"type": "muscle", "recovery_hours": 72},
                "subscapularis": {"type": "muscle", "recovery_hours": 72},
                "supraspinatus_tendon": {"type": "tendon", "recovery_hours": 72},
            }},
        }},
        "biceps": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "biceps_brachii": {"type": "muscle", "recovery_hours": 48, "children": {
                "biceps_long_head": {"type": "muscle", "recovery_hours": 48},
                "biceps_short_head": {"type": "muscle", "recovery_hours": 48},
            }},
            "brachialis": {"type": "muscle", "recovery_hours": 48},
            "biceps_long_head_tendon": {"type": "tendon", "recovery_hours": 72},
        }},
        "triceps": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "triceps_long_head": {"type": "muscle", "recovery_hours": 48},
            "triceps_lateral_head": {"type": "muscle", "recovery_hours": 48},
            "triceps_medial_head": {"type": "muscle", "recovery_hours": 48},
        }},
        "forearms": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "brachioradialis": {"type": "muscle", "recovery_hours": 72},
            "wrist_flexors": {"type": "muscle", "recovery_hours": 48, "children": {
                "flexor_carpi_radialis": {"type": "muscle", "recovery_hours": 48},
                "flexor_carpi_ulnaris": {"type": "muscle", "recovery_hours": 48},
                "palmaris_longus": {"type": "muscle", "recovery_hours": 48},
                "flexor_digitorum_superficialis": {"type": "muscle", "recovery_hours": 48},
                "flexor_digitorum_profundus": {"type": "muscle", "recovery_hours": 48},
            }},
            "wrist_extensors": {"type": "muscle", "recovery_hours": 48, "children": {
                "extensor_carpi_radialis_longus": {"type": "muscle", "recovery_hours": 48},
                "extensor_carpi_radialis_brevis": {"type": "muscle", "recovery_hours": 48},
                "extensor_carpi_ulnaris": {"type": "muscle", "recovery_hours": 48},
                "extensor_digitorum": {"type": "muscle", "recovery_hours": 48},
            }},
            "pronator_teres": {"type": "muscle", "recovery_hours": 48},
            "supinator": {"type": "muscle", "recovery_hours": 48},
            "common_extensor_tendon": {"type": "tendon", "recovery_hours": 72},
            "common_flexor_tendon": {"type": "tendon", "recovery_hours": 72},
        }},
    }},
    "lower_body": {"type": "tissue_group", "recovery_hours": 72, "children": {
        "quads": {"type": "tissue_group", "recovery_hours": 72, "children": {
            "rectus_femoris": {"type": "muscle", "recovery_hours": 72},
            "vastus_lateralis": {"type": "muscle", "recovery_hours": 72},
            "vastus_medialis": {"type": "muscle", "recovery_hours": 72},
            "vastus_intermedius": {"type": "muscle", "recovery_hours": 72},
            "patellar_tendon": {"type": "tendon", "recovery_hours": 72},
        }},
        "hamstrings": {"type": "tissue_group", "recovery_hours": 72, "children": {
            "biceps_femoris": {"type": "muscle", "recovery_hours": 72, "children": {
                "biceps_femoris_long_head": {"type": "muscle", "recovery_hours": 72},
                "biceps_femoris_short_head": {"type": "muscle", "recovery_hours": 72},
            }},
            "semitendinosus": {"type": "muscle", "recovery_hours": 72},
            "semimembranosus": {"type": "muscle", "recovery_hours": 72},
            "hamstring_tendons": {"type": "tendon", "recovery_hours": 72},
        }},
        "glutes": {"type": "tissue_group", "recovery_hours": 72, "children": {
            "gluteus_maximus": {"type": "muscle", "recovery_hours": 72},
            "gluteus_medius": {"type": "muscle", "recovery_hours": 72},
            "gluteus_minimus": {"type": "muscle", "recovery_hours": 72},
        }},
        "calves": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "gastrocnemius": {"type": "muscle", "recovery_hours": 48, "children": {
                "gastrocnemius_medial_head": {"type": "muscle", "recovery_hours": 48},
                "gastrocnemius_lateral_head": {"type": "muscle", "recovery_hours": 48},
            }},
            "soleus": {"type": "muscle", "recovery_hours": 48},
            "achilles_tendon": {"type": "tendon", "recovery_hours": 72},
        }},
        "hip_adductors": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "adductor_magnus": {"type": "muscle", "recovery_hours": 48},
            "adductor_longus": {"type": "muscle", "recovery_hours": 48},
            "adductor_brevis": {"type": "muscle", "recovery_hours": 48},
            "gracilis": {"type": "muscle", "recovery_hours": 48},
            "pectineus": {"type": "muscle", "recovery_hours": 48},
        }},
        "hip_abductors": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "tensor_fasciae_latae": {"type": "muscle", "recovery_hours": 48},
        }},
        "hip_flexors": {"type": "tissue_group", "recovery_hours": 48, "children": {
            "iliopsoas": {"type": "muscle", "recovery_hours": 48, "children": {
                "psoas_major": {"type": "muscle", "recovery_hours": 48},
                "iliacus": {"type": "muscle", "recovery_hours": 48},
            }},
            "sartorius": {"type": "muscle", "recovery_hours": 48},
        }},
        "tibialis_anterior": {"type": "muscle", "recovery_hours": 48},
        "tibialis_posterior": {"type": "muscle", "recovery_hours": 48},
        "peroneals": {"type": "muscle", "recovery_hours": 48, "children": {
            "peroneus_longus": {"type": "muscle", "recovery_hours": 48},
            "peroneus_brevis": {"type": "muscle", "recovery_hours": 48},
        }},
        "popliteus": {"type": "muscle", "recovery_hours": 48},
    }},
    "core": {"type": "tissue_group", "recovery_hours": 24, "children": {
        "abs": {"type": "tissue_group", "recovery_hours": 24, "children": {
            "rectus_abdominis": {"type": "muscle", "recovery_hours": 24},
            "obliques": {"type": "muscle", "recovery_hours": 24, "children": {
                "internal_oblique": {"type": "muscle", "recovery_hours": 24},
                "external_oblique": {"type": "muscle", "recovery_hours": 24},
            }},
            "transverse_abdominis": {"type": "muscle", "recovery_hours": 24},
        }},
        "diaphragm": {"type": "muscle", "recovery_hours": 24},
        "pelvic_floor": {"type": "muscle", "recovery_hours": 24},
    }},
    # Joints (for injury tracking)
    "shoulder_joint": {"type": "joint", "recovery_hours": 72},
    "elbow_joint": {"type": "joint", "recovery_hours": 72},
    "wrist_joint": {"type": "joint", "recovery_hours": 48},
    "hip_joint": {"type": "joint", "recovery_hours": 72},
    "knee_joint": {"type": "joint", "recovery_hours": 72},
    "ankle_joint": {"type": "joint", "recovery_hours": 72},
    "spine": {"type": "joint", "recovery_hours": 72, "children": {
        "cervical_spine": {"type": "joint", "recovery_hours": 72},
        "thoracic_spine": {"type": "joint", "recovery_hours": 72},
        "lumbar_spine": {"type": "joint", "recovery_hours": 72},
    }},
}


def _name_to_display(name: str) -> str:
    """Convert snake_case name to Title Case display name."""
    return name.replace("_", " ").title()


def _insert_tree(
    session: Session,
    tree: dict,
    parent_id: int | None = None,
) -> None:
    """Recursively insert tissues from the nested dict."""
    for name, info in tree.items():
        display_name = info.get("display_name", _name_to_display(name))
        tissue = Tissue(
            name=name,
            display_name=display_name,
            type=info["type"],
            parent_id=parent_id,
            recovery_hours=info.get("recovery_hours", 48),
            notes=info.get("notes"),
        )
        session.add(tissue)
        session.flush()  # get the id for children
        children = info.get("children")
        if children:
            _insert_tree(session, children, parent_id=tissue.id)


def seed_tissues(session: Session) -> None:
    """Seed the tissue table if empty. Idempotent."""
    existing = session.exec(select(Tissue).limit(1)).first()
    if existing:
        return
    _insert_tree(session, TISSUE_TREE)
    session.commit()
