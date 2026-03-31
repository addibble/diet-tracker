from __future__ import annotations

from copy import deepcopy

_PROTOCOLS: dict[str, dict] = {
    "achilles-tendinopathy": {
        "id": "achilles-tendinopathy",
        "title": "Achilles Tendinopathy",
        "category": "tendon",
        "summary": "Progressive Achilles loading with symptom monitoring, rep-first progression, and conservative return to heavier calf work.",
        "default_pain_monitoring_threshold": 3,
        "default_max_next_day_flare": 2,
        "stages": [
            {
                "id": "calm-and-isometric",
                "label": "Calm / Isometric",
                "focus": "Reduce irritability while maintaining load tolerance with low-risk isometrics and easy calf work.",
            },
            {
                "id": "rebuild-capacity",
                "label": "Rebuild Capacity",
                "focus": "Controlled calf raise progression, usually reps before load, with next-day symptom checks.",
            },
            {
                "id": "return-to-heavy-slow",
                "label": "Return to Heavy Slow",
                "focus": "Heavy-slow resistance only after symptom stability and stage completion.",
            },
        ],
    },
    "rotator-cuff-supraspinatus": {
        "id": "rotator-cuff-supraspinatus",
        "title": "Rotator Cuff / Supraspinatus",
        "category": "tendon",
        "summary": "Cuff-friendly staged loading with elevation control, tendon-safe progression, and delayed return to higher-strain shoulder work.",
        "default_pain_monitoring_threshold": 3,
        "default_max_next_day_flare": 2,
        "stages": [
            {
                "id": "protected-range",
                "label": "Protected Range",
                "focus": "Isometrics and low-irritability cuff work in safe ranges while avoiding provocative heavy pressing/raising.",
            },
            {
                "id": "controlled-dynamic",
                "label": "Controlled Dynamic",
                "focus": "Restore cuff and deltoid function with controlled tempo and modest volume.",
            },
            {
                "id": "return-to-overhead",
                "label": "Return to Overhead",
                "focus": "Gradual return to heavier and overhead loading only when symptoms remain stable.",
            },
        ],
    },
    "lateral-elbow-brachioradialis": {
        "id": "lateral-elbow-brachioradialis",
        "title": "Lateral Elbow / Brachioradialis",
        "category": "tendon",
        "summary": "Loading-based lateral elbow rehab that avoids forcing painful progression and emphasizes tolerance-first forearm loading.",
        "default_pain_monitoring_threshold": 3,
        "default_max_next_day_flare": 2,
        "stages": [
            {
                "id": "tolerance-building",
                "label": "Tolerance Building",
                "focus": "Pain-monitored isometrics and easy forearm work without chasing high pain.",
            },
            {
                "id": "eccentric-concentric",
                "label": "Eccentric / Concentric Loading",
                "focus": "Progressive wrist-extensor and elbow-flexor loading while monitoring next-day flare.",
            },
            {
                "id": "return-to-grip-load",
                "label": "Return to Grip Load",
                "focus": "Careful return to grip-heavy and curl variations after tolerance is established.",
            },
        ],
    },
    "cervical-radiculopathy-deltoid": {
        "id": "cervical-radiculopathy-deltoid",
        "title": "Cervical Radiculopathy / Deltoid Weakness",
        "category": "neural",
        "summary": (
            "Side-aware protocol combining neural symptom monitoring, cervical "
            "isometrics/mobilization context, and low-irritability shoulder rebuilding."
        ),
        "default_pain_monitoring_threshold": 3,
        "default_max_next_day_flare": 2,
        "stages": [
            {
                "id": "neural-calming",
                "label": "Neural Calming",
                "focus": "Prioritize neural symptom control and low-threat activation before meaningful loading progression.",
            },
            {
                "id": "activation-and-control",
                "label": "Activation / Control",
                "focus": "Restore affected-side activation and controlled deltoid/cuff function without neural flare.",
            },
            {
                "id": "strength-rebuild",
                "label": "Strength Rebuild",
                "focus": "Gradual return to harder unilateral shoulder work with neural symptoms still gating progress.",
            },
        ],
    },
    "contralateral-cross-education": {
        "id": "contralateral-cross-education",
        "title": "Contralateral Cross-Education Support",
        "category": "support",
        "summary": (
            "High-intensity unaffected-side support intended to preserve or improve "
            "neural strength carryover without counting as injured-side tendon loading."
        ),
        "default_pain_monitoring_threshold": 0,
        "default_max_next_day_flare": 0,
        "stages": [
            {
                "id": "high-intent-support",
                "label": "High-Intent Support",
                "focus": "Train the unaffected side with high intent while keeping carryover separate from local tissue remodeling credit.",
            },
        ],
    },
}


def list_rehab_protocols() -> list[dict]:
    return [deepcopy(_PROTOCOLS[key]) for key in sorted(_PROTOCOLS)]


def get_rehab_protocol(protocol_id: str) -> dict:
    try:
        return deepcopy(_PROTOCOLS[protocol_id])
    except KeyError as exc:
        raise KeyError(f"Unknown rehab protocol '{protocol_id}'") from exc
