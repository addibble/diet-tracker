from app.models import Exercise, Tissue
from app.planner import _prescribe_all, _select_exercises


def test_prescribe_all_normalizes_suitability_score(session):
    exercise = Exercise(name="Test Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": exercise.id,
                "name": exercise.name,
                "suitability_score": 70,
                "recommendation": "good",
                "weighted_risk_7d": 0.0,
            }
        ],
        [],
    )

    assert prescribed[0]["rep_scheme"] == "volume"


def test_prescribe_all_caps_caution_exercises_out_of_heavy_scheme(session):
    exercise = Exercise(name="Cautious Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": exercise.id,
                "name": exercise.name,
                "suitability_score": 95,
                "recommendation": "caution",
                "weighted_risk_7d": 30.0,
            }
        ],
        [],
    )

    assert prescribed[0]["rep_scheme"] == "volume"


# ── _select_exercises: suitability-based deprioritization ────────────────────


def test_select_exercises_prefers_fresh_muscles_over_fatigued():
    """Exercises targeting fresher muscles (higher suitability) should rank first."""
    # Two exercises both mapping to the same target region. One targets a fresh
    # muscle (high suitability), the other a fatigued one (low suitability).
    fresh_ex = {
        "id": 1,
        "name": "Fresh Squat",
        "recommendation": "good",
        "suitability_score": 90,
        "weighted_risk_7d": 5.0,
        "tissues": [],
    }
    fatigued_ex = {
        "id": 2,
        "name": "Tired Squat",
        "recommendation": "good",
        "suitability_score": 30,
        "weighted_risk_7d": 60.0,
        "tissues": [],
    }
    # Both map to "quads" with equivalent routing
    exercise_region_map = {
        1: [{"region": "quads", "role": "primary", "routing": 1.0}],
        2: [{"region": "quads", "role": "primary", "routing": 1.0}],
    }
    result = _select_exercises(
        [fresh_ex, fatigued_ex],
        target_regions={"quads"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
    )
    names = [r["name"] for r in result]
    assert names.index("Fresh Squat") < names.index("Tired Squat"), (
        "Fresh muscle exercise should be ranked before fatigued one"
    )


# ── _prescribe_all: weight reduction for tissue conditions ───────────────────


def test_prescribe_all_reduces_weight_for_tender_tissue(session):
    """When a significantly loaded tissue is tender, target weight is capped at 60%."""
    exercise = Exercise(name="Calf Raise", equipment="barbell")
    session.add(exercise)
    session.commit()

    tissues_data = [
        {
            "tissue": {"id": 99, "name": "achilles_tendon"},
            "recovery_estimate": 0.5,
            "current_condition": {
                "status": "tender",
                "severity": 1,
                "max_loading_factor": None,
            },
        }
    ]
    exercises_data = [
        {
            "id": exercise.id,
            "name": exercise.name,
            "suitability_score": 50,
            "recommendation": "caution",
            "weighted_risk_7d": 40.0,
            "tissues": [
                {
                    "tissue_id": 99,
                    "tissue_display_name": "Achilles Tendon",
                    "routing_factor": 0.8,
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, tissues_data)
    result = prescribed[0]

    assert result["weight_adjustment_note"] is not None
    assert "tender" in result["weight_adjustment_note"].lower()
    assert "60%" in result["weight_adjustment_note"]


def test_prescribe_all_reduces_weight_for_rehabbing_tissue_with_max_loading_factor(session):
    """max_loading_factor from a rehabbing condition is applied to target weight."""
    exercise = Exercise(name="Shoulder Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    tissues_data = [
        {
            "tissue": {"id": 88, "name": "shoulder_joint"},
            "recovery_estimate": 0.4,
            "current_condition": {
                "status": "rehabbing",
                "severity": 2,
                "max_loading_factor": 0.5,
            },
        }
    ]
    exercises_data = [
        {
            "id": exercise.id,
            "name": exercise.name,
            "suitability_score": 40,
            "recommendation": "caution",
            "weighted_risk_7d": 50.0,
            "tissues": [
                {
                    "tissue_id": 88,
                    "tissue_display_name": "Shoulder Joint",
                    "routing_factor": 0.9,
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, tissues_data)
    result = prescribed[0]

    assert result["weight_adjustment_note"] is not None
    assert "rehabbing" in result["weight_adjustment_note"].lower()
    assert "50%" in result["weight_adjustment_note"]


def test_prescribe_all_ignores_distant_stabilizers_for_weight_reduction(session):
    """Tissues with routing_factor < 0.3 should not trigger weight reduction."""
    exercise = Exercise(name="Bench Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    tissues_data = [
        {
            "tissue": {"id": 77, "name": "wrist_joint"},
            "recovery_estimate": 0.3,
            "current_condition": {
                "status": "tender",
                "severity": 1,
                "max_loading_factor": None,
            },
        }
    ]
    exercises_data = [
        {
            "id": exercise.id,
            "name": exercise.name,
            "suitability_score": 80,
            "recommendation": "good",
            "weighted_risk_7d": 10.0,
            "tissues": [
                {
                    "tissue_id": 77,
                    "tissue_display_name": "Wrist Joint",
                    "routing_factor": 0.1,  # distant stabilizer, below threshold
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, tissues_data)
    result = prescribed[0]

    assert result["weight_adjustment_note"] is None


def test_prescribe_all_skips_progressive_overload_when_condition_restricts_weight(session):
    """When a tissue condition restricts loading, progressive overload is not applied."""
    from datetime import date

    from app.models import WorkoutSession, WorkoutSet

    exercise = Exercise(name="Leg Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    # Log a previous heavy session to trigger overload logic
    ws = WorkoutSession(date=date(2026, 3, 14))
    session.add(ws)
    session.commit()
    for i in range(3):
        session.add(WorkoutSet(
            session_id=ws.id,
            exercise_id=exercise.id,
            set_order=i,
            reps=10,
            weight=200.0,
            rep_completion="full",
        ))
    session.commit()

    tissues_data = [
        {
            "tissue": {"id": 95, "name": "hip_joint"},
            "recovery_estimate": 0.4,
            "current_condition": {
                "status": "rehabbing",
                "severity": 2,
                "max_loading_factor": 0.5,
            },
        }
    ]
    exercises_data = [
        {
            "id": exercise.id,
            "name": exercise.name,
            "suitability_score": 35,
            "recommendation": "caution",
            "weighted_risk_7d": 55.0,
            "tissues": [
                {
                    "tissue_id": 95,
                    "tissue_display_name": "Hip Joint",
                    "routing_factor": 0.6,
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, tissues_data)
    result = prescribed[0]

    assert result["overload_note"] is None, (
        "Progressive overload should be suppressed when tissue condition restricts weight"
    )
    assert result["weight_adjustment_note"] is not None
