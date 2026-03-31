from datetime import date

from app.models import Exercise, WorkoutSession, WorkoutSet
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


def test_select_exercises_hard_excludes_severely_fatigued_tissue():
    """Exercises where a significant tissue is below the hard floor are dropped."""
    # Leg Press with one tissue severely fatigued (recovery_state = 0.25 < 0.4)
    leg_press = {
        "id": 10,
        "name": "Leg Press",
        "recommendation": "good",
        "suitability_score": 80,
        "weighted_risk_7d": 20.0,
        "tissues": [
            {"tissue_id": 1, "tissue_display_name": "Quad", "routing_factor": 0.9, "recovery_state": 0.8},
            {"tissue_id": 2, "tissue_display_name": "Adductor", "routing_factor": 0.5, "recovery_state": 0.25},
        ],
    }
    hamstring_curl = {
        "id": 11,
        "name": "Hamstring Curl",
        "recommendation": "good",
        "suitability_score": 85,
        "weighted_risk_7d": 10.0,
        "tissues": [
            {"tissue_id": 3, "tissue_display_name": "Hamstring", "routing_factor": 1.0, "recovery_state": 0.9},
        ],
    }
    exercise_region_map = {
        10: [{"region": "quads", "role": "primary", "routing": 1.0}],
        11: [{"region": "hamstrings", "role": "primary", "routing": 1.0}],
    }
    result = _select_exercises(
        [leg_press, hamstring_curl],
        target_regions={"quads", "hamstrings"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
    )
    names = [r["name"] for r in result]
    assert "Leg Press" not in names, (
        "Leg Press should be excluded when a primary tissue is below the hard fatigue floor"
    )
    assert "Hamstring Curl" in names


def test_select_exercises_demotes_moderately_fatigued_tissue():
    """Exercises with a tissue between the hard and soft floor rank lower than fresh alternatives."""
    # Leg Press with adductor at recovery_state = 0.55 (between 0.4 and 0.7)
    leg_press = {
        "id": 20,
        "name": "Leg Press",
        "recommendation": "good",
        "suitability_score": 80,
        "weighted_risk_7d": 20.0,
        "tissues": [
            {"tissue_id": 1, "tissue_display_name": "Quad", "routing_factor": 0.9, "recovery_state": 0.85},
            {"tissue_id": 2, "tissue_display_name": "Adductor", "routing_factor": 0.45, "recovery_state": 0.55},
        ],
    }
    hamstring_curl = {
        "id": 21,
        "name": "Hamstring Curl",
        "recommendation": "good",
        "suitability_score": 75,
        "weighted_risk_7d": 15.0,
        "tissues": [
            {"tissue_id": 3, "tissue_display_name": "Hamstring", "routing_factor": 1.0, "recovery_state": 0.95},
        ],
    }
    exercise_region_map = {
        20: [{"region": "quads", "role": "primary", "routing": 1.0}],
        21: [{"region": "hamstrings", "role": "primary", "routing": 1.0}],
    }
    result = _select_exercises(
        [leg_press, hamstring_curl],
        target_regions={"quads", "hamstrings"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
    )
    names = [r["name"] for r in result]
    assert "Leg Press" in names, "Leg Press should still be a candidate (above hard floor)"
    assert names.index("Hamstring Curl") < names.index("Leg Press"), (
        "Hamstring Curl (fresh tissue) should rank above Leg Press (moderately fatigued adductor)"
    )


def test_select_exercises_does_not_penalise_distant_stabilisers():
    """Tissues with routing_factor below the significant threshold don't trigger fatigue gating."""
    # Exercise where only a distant stabilizer is fatigued (routing < 0.3)
    ex_with_minor_fatigued_stabilizer = {
        "id": 30,
        "name": "Cable Fly",
        "recommendation": "good",
        "suitability_score": 85,
        "weighted_risk_7d": 10.0,
        "tissues": [
            {"tissue_id": 5, "tissue_display_name": "Pec", "routing_factor": 1.0, "recovery_state": 0.9},
            {"tissue_id": 6, "tissue_display_name": "Wrist", "routing_factor": 0.15, "recovery_state": 0.2},
        ],
    }
    exercise_region_map = {
        30: [{"region": "chest", "role": "primary", "routing": 1.0}],
    }
    result = _select_exercises(
        [ex_with_minor_fatigued_stabilizer],
        target_regions={"chest"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
    )
    assert len(result) == 1, (
        "Exercise should not be excluded when only a distant stabilizer (routing < threshold) is fatigued"
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


def test_prescribe_all_prefers_unaffected_side_for_cross_education(session):
    from app.models import RehabPlan, Tissue, TissueCondition, TrackedTissue

    exercise = Exercise(
        name="Single Arm Shoulder Press",
        equipment="dumbbell",
        laterality="unilateral",
    )
    session.add(exercise)
    tissue = Tissue(
        name="lateral_deltoid",
        display_name="Lateral Deltoid",
        type="muscle",
        tracking_mode="paired",
    )
    session.add(tissue)
    session.commit()
    session.refresh(exercise)
    session.refresh(tissue)

    left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Lateral Deltoid")
    right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Lateral Deltoid")
    session.add(left)
    session.add(right)
    session.commit()
    session.refresh(left)

    session.add(
        TissueCondition(
            tissue_id=tissue.id,
            tracked_tissue_id=left.id,
            status="rehabbing",
            severity=2,
        )
    )
    session.add(
        RehabPlan(
            tracked_tissue_id=left.id,
            protocol_id="cervical-radiculopathy-deltoid",
            stage_id="activation-and-control",
            status="active",
        )
    )
    session.commit()

    exercises_data = [
        {
            "id": exercise.id,
            "name": exercise.name,
            "suitability_score": 70,
            "recommendation": "good",
            "weighted_risk_7d": 15.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "routing_factor": 0.9,
                    "laterality_mode": "contralateral_carryover",
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, [])
    assert prescribed[0]["performed_side"] == "right"
    assert "cross-education" in (prescribed[0]["side_explanation"] or "")


def test_prescribe_all_limits_heavy_exercises_per_primary_region(session):
    heavy_a = Exercise(name="Heavy Press A", equipment="barbell")
    heavy_b = Exercise(name="Heavy Press B", equipment="barbell")
    session.add(heavy_a)
    session.add(heavy_b)
    session.commit()

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": heavy_a.id,
                "name": heavy_a.name,
                "suitability_score": 95,
                "recommendation": "good",
                "weighted_risk_7d": 10.0,
                "target_hits": {"chest"},
                "primary_regions": {"chest"},
                "tissues": [],
            },
            {
                "id": heavy_b.id,
                "name": heavy_b.name,
                "suitability_score": 93,
                "recommendation": "good",
                "weighted_risk_7d": 12.0,
                "target_hits": {"chest"},
                "primary_regions": {"chest"},
                "tissues": [],
            },
        ],
        [],
    )

    assert prescribed[0]["rep_scheme"] == "heavy"
    assert prescribed[1]["rep_scheme"] == "volume"
    assert "Heavy slot already used" in prescribed[1]["rationale"]


def test_prescribe_all_blends_heavy_target_with_recent_high_rep_weight(session):
    exercise = Exercise(name="Blend Press", equipment="barbell")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    workout = WorkoutSession(date=date(2026, 3, 30))
    session.add(workout)
    session.commit()
    session.refresh(workout)
    for set_order in range(3):
        session.add(
            WorkoutSet(
                session_id=workout.id,
                exercise_id=exercise.id,
                set_order=set_order,
                reps=12,
                weight=100.0,
                rep_completion="full",
            )
        )
    session.commit()

    original_build_exercise_strength = __import__("app.planner", fromlist=["build_exercise_strength"]).build_exercise_strength

    def fake_strength(_session, _exercise_id, as_of=None):  # noqa: ARG001
        return {"current_e1rm": 200.0}

    import app.planner as planner_module

    planner_module.build_exercise_strength = fake_strength
    try:
        prescribed = _prescribe_all(
            session,
            [
                {
                    "id": exercise.id,
                    "name": exercise.name,
                    "suitability_score": 95,
                    "recommendation": "good",
                    "weighted_risk_7d": 5.0,
                    "target_hits": {"chest"},
                    "primary_regions": {"chest"},
                    "tissues": [],
                }
            ],
            [],
        )
    finally:
        planner_module.build_exercise_strength = original_build_exercise_strength

    result = prescribed[0]
    assert result["rep_scheme"] == "heavy"
    assert result["target_weight"] == 130
    assert "blends e1RM" in (result["overload_note"] or "")
