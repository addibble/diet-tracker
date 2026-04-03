from datetime import UTC, date, datetime

from sqlmodel import select

from app.models import (
    Exercise,
    RecoveryCheckIn,
    RehabCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TrackedTissue,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.planner import (
    _build_region_state,
    _build_rehab_priority_map,
    _load_todays_checkins,
    _planner_preferred_side,
    _prescribe_all,
    _select_exercises,
    _soft_blocked_regions,
)


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


def test_soft_blocked_regions_include_moderate_soreness():
    blocked = _soft_blocked_regions({
        "chest": {"pain_0_10": 0, "soreness_0_10": 5, "stiffness_0_10": 0, "readiness_0_10": 4},
        "shoulders": {"pain_0_10": 0, "soreness_0_10": 2, "stiffness_0_10": 0, "readiness_0_10": 7},
    })
    assert "chest" in blocked
    assert "shoulders" not in blocked


def test_select_exercises_skips_direct_primary_region_when_same_day_soreness_is_moderate(session):
    session.add(
        RecoveryCheckIn(
            date=date(2026, 4, 2),
            region="chest",
            soreness_0_10=5,
            pain_0_10=0,
            stiffness_0_10=0,
            readiness_0_10=4,
        )
    )
    session.commit()

    checkins = _load_todays_checkins(session, date(2026, 4, 2))
    soft_blocked = _soft_blocked_regions(checkins)

    cable_fly = {
        "id": 40,
        "name": "Low-High Cable Fly",
        "recommendation": "good",
        "suitability_score": 88,
        "weighted_risk_7d": 8.0,
        "tissues": [
            {"tissue_id": 1, "tissue_display_name": "Pec", "routing_factor": 1.0, "recovery_state": 0.9},
        ],
    }
    landmine_press = {
        "id": 41,
        "name": "Landmine Press",
        "recommendation": "good",
        "suitability_score": 84,
        "weighted_risk_7d": 10.0,
        "tissues": [
            {"tissue_id": 2, "tissue_display_name": "Anterior Deltoid", "routing_factor": 0.6, "recovery_state": 0.9},
            {"tissue_id": 1, "tissue_display_name": "Pec", "routing_factor": 0.55, "recovery_state": 0.9},
        ],
    }
    row = {
        "tissue": {"id": 1, "region": "chest"},
        "recovery_estimate": 0.92,
        "risk_7d": 10,
        "current_condition": None,
    }
    region_state = _build_region_state([row], checkins)
    assert region_state["chest"]["readiness"] <= 0.45

    exercise_region_map = {
        40: [{"region": "chest", "role": "primary", "routing": 1.0}],
        41: [{"region": "chest", "role": "primary", "routing": 0.55}, {"region": "shoulders", "role": "primary", "routing": 0.6}],
    }
    result = _select_exercises(
        [cable_fly, landmine_press],
        target_regions={"chest", "shoulders"},
        adjacent_regions=set(),
        blocked_regions=set(),
        soft_blocked_regions=soft_blocked,
        exercise_region_map=exercise_region_map,
    )
    names = [row["name"] for row in result]
    assert "Low-High Cable Fly" not in names
    assert "Landmine Press" not in names


def test_load_todays_checkins_aggregates_region_and_tracked_rows_by_region(session):
    tissue = Tissue(
        name="pectoralis_major",
        display_name="Pectoralis Major",
        region="chest",
        tracking_mode="paired",
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(
        tissue_id=tissue.id,
        side="left",
        display_name="Left Pectoralis Major",
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    session.add(
        RecoveryCheckIn(
            date=date(2026, 4, 2),
            region="chest",
            soreness_0_10=2,
            pain_0_10=0,
            stiffness_0_10=1,
            readiness_0_10=7,
        )
    )
    session.add(
        RecoveryCheckIn(
            date=date(2026, 4, 2),
            region="chest",
            tracked_tissue_id=tracked.id,
            soreness_0_10=5,
            pain_0_10=3,
            stiffness_0_10=0,
            readiness_0_10=4,
        )
    )
    session.commit()

    checkins = _load_todays_checkins(session, date(2026, 4, 2))

    assert checkins["chest"] == {
        "soreness_0_10": 5,
        "pain_0_10": 3,
        "stiffness_0_10": 1,
        "readiness_0_10": 4,
    }


def test_select_exercises_inserts_direct_rehab_unilateral_candidate_first(session):
    tissue = Tissue(
        name="lateral_deltoid",
        display_name="Lateral Deltoid",
        type="muscle",
        tracking_mode="paired",
        region="shoulders",
    )
    direct_rehab = Exercise(
        name="Single Arm Shoulder Press",
        equipment="dumbbell",
        laterality="unilateral",
    )
    bilateral = Exercise(
        name="Bench Press",
        equipment="barbell",
        laterality="bilateral",
    )
    session.add(tissue)
    session.add(direct_rehab)
    session.add(bilateral)
    session.commit()
    session.refresh(tissue)
    session.refresh(direct_rehab)
    session.refresh(bilateral)

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
            "id": bilateral.id,
            "name": bilateral.name,
            "laterality": "bilateral",
            "suitability_score": 92,
            "recommendation": "good",
            "weighted_risk_7d": 5.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "routing_factor": 0.4,
                    "laterality_mode": "bilateral_equal",
                }
            ],
        },
        {
            "id": direct_rehab.id,
            "name": direct_rehab.name,
            "laterality": "unilateral",
            "suitability_score": 70,
            "recommendation": "good",
            "weighted_risk_7d": 15.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "routing_factor": 0.95,
                    "laterality_mode": "selected_side_only",
                }
            ],
        },
    ]
    exercise_region_map = {
        bilateral.id: [{"region": "chest", "role": "primary", "routing": 1.0}],
        direct_rehab.id: [{"region": "shoulders", "role": "primary", "routing": 1.0}],
    }
    tracked_lookup = {left.id: left, right.id: right}
    tracked_conditions = {left.id: session.exec(select(TissueCondition)).first()}
    active_rehab_plans = {left.id: session.exec(select(RehabPlan)).first()}
    rehab_priorities = _build_rehab_priority_map(
        session=session,
        exercises_data=exercises_data,
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )

    result = _select_exercises(
        exercises_data,
        target_regions={"chest"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
        rehab_priorities=rehab_priorities,
    )

    assert result[0]["name"] == "Single Arm Shoulder Press"
    assert result[0]["selection_mode"] == "direct_rehab"


def test_select_exercises_prefers_direct_rehab_over_cross_education_when_direct_work_is_available(session):
    tissue = Tissue(
        name="lateral_deltoid",
        display_name="Lateral Deltoid",
        type="muscle",
        tracking_mode="paired",
        region="shoulders",
    )
    cross_education = Exercise(
        name="Single Arm Shoulder Press",
        equipment="dumbbell",
        laterality="unilateral",
    )
    bilateral = Exercise(
        name="Lat Pulldown",
        equipment="cable",
        laterality="bilateral",
    )
    session.add(tissue)
    session.add(cross_education)
    session.add(bilateral)
    session.commit()
    session.refresh(tissue)
    session.refresh(cross_education)
    session.refresh(bilateral)

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
            "id": bilateral.id,
            "name": bilateral.name,
            "laterality": "bilateral",
            "suitability_score": 90,
            "recommendation": "good",
            "weighted_risk_7d": 8.0,
            "tissues": [],
        },
        {
            "id": cross_education.id,
            "name": cross_education.name,
            "laterality": "unilateral",
            "suitability_score": 72,
            "recommendation": "good",
            "weighted_risk_7d": 12.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "routing_factor": 0.9,
                    "laterality_mode": "contralateral_carryover",
                }
            ],
        },
    ]
    exercise_region_map = {
        bilateral.id: [{"region": "upper_back", "role": "primary", "routing": 1.0}],
        cross_education.id: [{"region": "shoulders", "role": "primary", "routing": 1.0}],
    }
    tracked_lookup = {left.id: left, right.id: right}
    tracked_conditions = {left.id: session.exec(select(TissueCondition)).first()}
    active_rehab_plans = {left.id: session.exec(select(RehabPlan)).first()}
    rehab_priorities = _build_rehab_priority_map(
        session=session,
        exercises_data=exercises_data,
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )

    result = _select_exercises(
        exercises_data,
        target_regions={"upper_back"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
        rehab_priorities=rehab_priorities,
    )

    assert result[0]["name"] == "Single Arm Shoulder Press"
    assert result[0]["selection_mode"] == "direct_rehab"


def test_select_exercises_prefers_direct_rehab_in_late_stage_over_cross_education(session):
    tissue = Tissue(
        name="biceps_long_head",
        display_name="Biceps Long Head",
        type="muscle",
        tracking_mode="paired",
        region="biceps",
    )
    unilateral_curl = Exercise(
        name="Single-Arm Cable Curl",
        equipment="cable",
        laterality="unilateral",
    )
    bilateral = Exercise(
        name="Lat Pulldown",
        equipment="cable",
        laterality="bilateral",
    )
    session.add(tissue)
    session.add(unilateral_curl)
    session.add(bilateral)
    session.commit()
    session.refresh(tissue)
    session.refresh(unilateral_curl)
    session.refresh(bilateral)

    left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Biceps Long Head")
    right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Biceps Long Head")
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
            stage_id="strength-rebuild",
            status="active",
        )
    )
    session.commit()

    exercises_data = [
        {
            "id": bilateral.id,
            "name": bilateral.name,
            "laterality": "bilateral",
            "suitability_score": 90,
            "recommendation": "good",
            "weighted_risk_7d": 8.0,
            "tissues": [],
        },
        {
            "id": unilateral_curl.id,
            "name": unilateral_curl.name,
            "laterality": "unilateral",
            "suitability_score": 72,
            "recommendation": "good",
            "weighted_risk_7d": 12.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "routing_factor": 0.7,
                    "laterality_mode": "contralateral_carryover",
                }
            ],
        },
    ]
    exercise_region_map = {
        bilateral.id: [{"region": "upper_back", "role": "primary", "routing": 1.0}],
        unilateral_curl.id: [{"region": "biceps", "role": "primary", "routing": 1.0}],
    }
    tracked_lookup = {left.id: left, right.id: right}
    tracked_conditions = {left.id: session.exec(select(TissueCondition)).first()}
    active_rehab_plans = {left.id: session.exec(select(RehabPlan)).first()}
    rehab_priorities = _build_rehab_priority_map(
        session=session,
        exercises_data=exercises_data,
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )

    result = _select_exercises(
        exercises_data,
        target_regions={"upper_back"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
        rehab_priorities=rehab_priorities,
    )

    assert result[0]["name"] == "Single-Arm Cable Curl"
    assert result[0]["selection_mode"] == "direct_rehab"

    preferred_side, _side_explanation, rehab_stage, prescription_mode = _planner_preferred_side(
        exercise=unilateral_curl,
        exercise_summary=exercises_data[1],
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )
    assert preferred_side == "left"
    assert rehab_stage == "strength-rebuild"
    assert prescription_mode == "direct_rehab"


def test_select_exercises_swaps_blocked_variant_for_safer_sibling(session):
    tissue = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        tracking_mode="paired",
        region="forearms",
    )
    neutral = Exercise(
        name="Neutral Grip Cable Curl",
        equipment="cable",
        laterality="unilateral",
        variant_group="curl_family",
        grip_style="neutral",
        support_style="cable_stabilized",
    )
    pronated = Exercise(
        name="Pronated Cable Curl",
        equipment="cable",
        laterality="unilateral",
        variant_group="curl_family",
        grip_style="pronated",
        support_style="unsupported",
    )
    session.add(tissue)
    session.add(neutral)
    session.add(pronated)
    session.commit()
    session.refresh(tissue)
    session.refresh(neutral)
    session.refresh(pronated)

    left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Brachioradialis")
    right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Brachioradialis")
    session.add(left)
    session.add(right)
    session.commit()
    session.refresh(left)

    session.add(
        TissueCondition(
            tissue_id=tissue.id,
            tracked_tissue_id=left.id,
            status="tender",
            severity=2,
            max_loading_factor=0.25,
        )
    )
    session.add(
        RehabPlan(
            tracked_tissue_id=left.id,
            protocol_id="lateral-elbow-brachioradialis",
            stage_id="eccentric-concentric",
            status="active",
        )
    )
    session.add(
        RehabCheckIn(
            tracked_tissue_id=left.id,
            pain_0_10=5,
            during_load_pain_0_10=5,
            recorded_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        )
    )
    session.commit()

    exercises_data = [
        {
            "id": pronated.id,
            "name": pronated.name,
            "laterality": "unilateral",
            "variant_group": "curl_family",
            "grip_style": "pronated",
            "support_style": "unsupported",
            "suitability_score": 88,
            "recommendation": "good",
            "weighted_risk_7d": 12.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "tissue_type": "muscle",
                    "loading_factor": 0.6,
                    "routing_factor": 0.6,
                    "fatigue_factor": 0.6,
                    "joint_strain_factor": 0.6,
                    "tendon_strain_factor": 0.6,
                    "laterality_mode": "selected_side_only",
                    "recovery_state": 0.9,
                }
            ],
        },
        {
            "id": neutral.id,
            "name": neutral.name,
            "laterality": "unilateral",
            "variant_group": "curl_family",
            "grip_style": "neutral",
            "support_style": "cable_stabilized",
            "suitability_score": 74,
            "recommendation": "good",
            "weighted_risk_7d": 16.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "tissue_type": "muscle",
                    "loading_factor": 0.18,
                    "routing_factor": 0.18,
                    "fatigue_factor": 0.18,
                    "joint_strain_factor": 0.18,
                    "tendon_strain_factor": 0.18,
                    "laterality_mode": "selected_side_only",
                    "recovery_state": 0.9,
                }
            ],
        },
    ]
    exercise_region_map = {
        pronated.id: [{"region": "forearms", "role": "primary", "routing": 1.0}],
        neutral.id: [{"region": "forearms", "role": "primary", "routing": 1.0}],
    }

    result = _select_exercises(
        exercises_data,
        target_regions={"forearms"},
        adjacent_regions=set(),
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
        protection_profiles=__import__(
            "app.exercise_protection",
            fromlist=["build_tracked_protection_profiles"],
        ).build_tracked_protection_profiles(session, as_of=date(2026, 4, 1)),
    )

    assert result[0]["name"] == "Neutral Grip Cable Curl"
    assert result[0]["blocked_variant"] == "Pronated Cable Curl"
    assert "Swapped Pronated Cable Curl" in (result[0]["selection_note"] or "")


def test_select_exercises_blocks_later_accessory_when_session_budget_is_spent(session):
    tissue = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        tracking_mode="paired",
        region="forearms",
    )
    first = Exercise(
        name="Neutral Grip Cable Curl",
        equipment="cable",
        laterality="unilateral",
        grip_style="neutral",
        support_style="cable_stabilized",
    )
    second = Exercise(
        name="Face Pull",
        equipment="cable",
        laterality="bilateral",
        grip_style="neutral",
        support_style="cable_stabilized",
    )
    session.add(tissue)
    session.add(first)
    session.add(second)
    session.commit()
    session.refresh(tissue)
    session.refresh(first)
    session.refresh(second)

    left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Brachioradialis")
    right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Brachioradialis")
    session.add(left)
    session.add(right)
    session.commit()
    session.refresh(left)

    session.add(
        TissueCondition(
            tissue_id=tissue.id,
            tracked_tissue_id=left.id,
            status="tender",
            severity=2,
            max_loading_factor=0.3,
        )
    )
    session.add(
        RehabPlan(
            tracked_tissue_id=left.id,
            protocol_id="lateral-elbow-brachioradialis",
            stage_id="eccentric-concentric",
            status="active",
        )
    )
    session.add(
        RehabCheckIn(
            tracked_tissue_id=left.id,
            pain_0_10=5,
            during_load_pain_0_10=5,
            recorded_at=datetime(2026, 4, 1, 8, 30, tzinfo=UTC),
        )
    )
    session.commit()

    workout = WorkoutSession(date=date(2026, 4, 1))
    session.add(workout)
    session.commit()
    session.refresh(workout)
    logged = WorkoutSet(
        session_id=workout.id,
        exercise_id=first.id,
        set_order=0,
        performed_side="left",
        reps=12,
        weight=25.0,
        completed_at=datetime(2026, 4, 1, 9, 15, tzinfo=UTC),
    )
    session.add(logged)
    session.add(
        WorkoutSet(
            session_id=workout.id,
            exercise_id=first.id,
            set_order=1,
            performed_side="left",
            reps=12,
            weight=25.0,
            completed_at=datetime(2026, 4, 1, 9, 18, tzinfo=UTC),
        )
    )
    session.commit()
    session.refresh(logged)

    exercises_data = [
        {
            "id": first.id,
            "name": first.name,
            "laterality": "unilateral",
            "grip_style": "neutral",
            "support_style": "cable_stabilized",
            "suitability_score": 72,
            "recommendation": "good",
            "weighted_risk_7d": 15.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "tissue_type": "muscle",
                    "loading_factor": 0.18,
                    "routing_factor": 0.18,
                    "fatigue_factor": 0.18,
                    "joint_strain_factor": 0.18,
                    "tendon_strain_factor": 0.18,
                    "laterality_mode": "selected_side_only",
                    "recovery_state": 0.9,
                }
            ],
        },
        {
            "id": second.id,
            "name": second.name,
            "laterality": "bilateral",
            "grip_style": "neutral",
            "support_style": "cable_stabilized",
            "suitability_score": 80,
            "recommendation": "good",
            "weighted_risk_7d": 10.0,
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "tissue_display_name": tissue.display_name,
                    "tissue_type": "muscle",
                    "loading_factor": 0.28,
                    "routing_factor": 0.28,
                    "fatigue_factor": 0.28,
                    "joint_strain_factor": 0.28,
                    "tendon_strain_factor": 0.28,
                    "laterality_mode": "bilateral_equal",
                    "recovery_state": 0.9,
                }
            ],
        },
    ]
    exercise_region_map = {
        first.id: [{"region": "forearms", "role": "primary", "routing": 1.0}],
        second.id: [{"region": "shoulders", "role": "secondary", "routing": 0.5}],
    }

    result = _select_exercises(
        exercises_data,
        target_regions={"forearms"},
        adjacent_regions={"shoulders"},
        blocked_regions=set(),
        exercise_region_map=exercise_region_map,
        protection_profiles=__import__(
            "app.exercise_protection",
            fromlist=["build_tracked_protection_profiles"],
        ).build_tracked_protection_profiles(session, as_of=date(2026, 4, 1)),
    )

    names = [row["name"] for row in result]
    assert "Neutral Grip Cable Curl" in names
    assert "Face Pull" not in names


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


def test_prescribe_all_prefers_direct_side_when_rehab_side_can_be_loaded(session):
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
    assert prescribed[0]["performed_side"] == "left"
    assert "directly" in (prescribed[0]["side_explanation"] or "")
    assert "cross-education" not in (prescribed[0]["side_explanation"] or "")


def test_prescribe_all_uses_unaffected_side_for_explicit_cross_education_protocol(session):
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
            protocol_id="contralateral-cross-education",
            stage_id="high-intent-support",
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


def test_prescribe_all_prefers_direct_side_for_late_stage_rehab(session):
    exercise = Exercise(
        name="Single-Arm Cable Curl",
        equipment="cable",
        laterality="unilateral",
    )
    session.add(exercise)
    tissue = Tissue(
        name="biceps_long_head",
        display_name="Biceps Long Head",
        type="muscle",
        tracking_mode="paired",
    )
    session.add(tissue)
    session.commit()
    session.refresh(exercise)
    session.refresh(tissue)

    left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Biceps Long Head")
    right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Biceps Long Head")
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
            stage_id="strength-rebuild",
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
                    "routing_factor": 0.7,
                    "laterality_mode": "contralateral_carryover",
                }
            ],
        }
    ]

    prescribed = _prescribe_all(session, exercises_data, [])
    assert prescribed[0]["performed_side"] == "left"
    assert "directly" in (prescribed[0]["side_explanation"] or "")
    assert "cross-education" not in (prescribed[0]["side_explanation"] or "")


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


def test_prescribe_all_reduces_assist_for_progressive_overload(session):
    exercise = Exercise(
        name="Assisted Pull-Ups",
        equipment="machine",
        load_input_mode="assisted_bodyweight",
        bodyweight_fraction=1.0,
    )
    session.add(exercise)
    session.add(WeightLog(weight_lb=200.0, logged_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC)))
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
                weight=50.0,
                rep_completion="full",
            )
        )
    session.commit()

    original_build_exercise_strength = __import__("app.planner", fromlist=["build_exercise_strength"]).build_exercise_strength

    def fake_strength(_session, _exercise_id, as_of=None):  # noqa: ARG001
        return {"current_e1rm": 180.0}

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
                    "target_hits": {"upper_back"},
                    "primary_regions": {"upper_back"},
                    "tissues": [],
                }
            ],
            [],
        )
    finally:
        planner_module.build_exercise_strength = original_build_exercise_strength

    result = prescribed[0]
    assert result["target_weight"] == 45.0
    assert "assist" in (result["overload_note"] or "").lower()
