"""Tests for exercise_groups module — classification and weekly menu."""

import math

import pytest
from sqlmodel import Session

from app.exercise_groups import (
    GROUP_CENTROIDS,
    WEEKLY_SCHEDULE,
    build_exercise_region_profile,
    classify_exercise,
    cosine_similarity,
    get_weekly_exercise_menu,
)
from app.models import Exercise, ExerciseTissue, Tissue

# ---------------------------------------------------------------------------
# Unit: cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    v = {"chest": 1.0, "triceps": 0.5}
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    a = {"chest": 1.0}
    b = {"quads": 1.0}
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_empty_vector():
    assert cosine_similarity({}, {"chest": 1.0}) == 0.0
    assert cosine_similarity({}, {}) == 0.0


def test_cosine_partial_overlap():
    a = {"chest": 1.0, "triceps": 0.5}
    b = {"chest": 0.5, "shoulders": 1.0}
    dot = 1.0 * 0.5
    norm_a = math.sqrt(1.0 + 0.25)
    norm_b = math.sqrt(0.25 + 1.0)
    assert cosine_similarity(a, b) == pytest.approx(dot / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Unit: classify_exercise
# ---------------------------------------------------------------------------


def test_classify_chest_dominant_as_push():
    group, conf = classify_exercise({"chest": 0.9, "triceps": 0.6, "shoulders": 0.5})
    assert group == "Push"
    assert conf > 0.8


def test_classify_upper_back_dominant_as_pull():
    group, conf = classify_exercise({"upper_back": 1.0, "biceps": 0.5})
    assert group == "Pull"
    assert conf > 0.8


def test_classify_quad_dominant_as_legs():
    group, conf = classify_exercise({"quads": 0.8, "hamstrings": 0.7, "glutes": 0.6})
    assert group == "Legs"
    assert conf > 0.7


def test_classify_shoulder_dominant_as_shoulders():
    group, conf = classify_exercise({"shoulders": 1.0})
    assert group == "Shoulders"
    assert conf > 0.9


def test_classify_bicep_dominant_as_arms():
    group, conf = classify_exercise({"biceps": 0.9, "forearms": 0.7})
    assert group == "Arms"
    assert conf > 0.7


def test_classify_core_dominant_as_core():
    group, conf = classify_exercise({"core": 1.0, "lower_back": 0.4})
    assert group == "Core"
    assert conf > 0.7


def test_classify_incline_press_as_push_not_shoulders():
    """Incline press loads shoulders heavily but chest too → Push."""
    group, _ = classify_exercise({"shoulders": 0.9, "chest": 0.8, "triceps": 0.6})
    assert group == "Push"


def test_classify_ohp_as_shoulders_not_push():
    """OHP loads shoulders dominantly with minor chest → Shoulders."""
    group, _ = classify_exercise({"shoulders": 1.0, "triceps": 0.5, "chest": 0.3})
    assert group == "Shoulders"


def test_classify_empty_as_uncategorized():
    group, conf = classify_exercise({})
    assert group == "Uncategorized"
    assert conf == 0.0


# ---------------------------------------------------------------------------
# Unit: build_exercise_region_profile
# ---------------------------------------------------------------------------


def test_profile_filters_low_load():
    mappings = [
        {"tissue_region": "chest", "loading_factor": 0.9,
         "routing_factor": 0.8, "joint_strain_factor": 0.1, "tendon_strain_factor": 0.1},
        {"tissue_region": "core", "loading_factor": 0.1,
         "routing_factor": 0.1, "joint_strain_factor": 0.1, "tendon_strain_factor": 0.1},
    ]
    profile = build_exercise_region_profile(mappings)
    assert "chest" in profile
    assert "core" not in profile  # below 0.3 threshold


def test_profile_normalizes_region():
    mappings = [
        {"tissue_region": "tibs", "loading_factor": 0.8,
         "routing_factor": 0.0, "joint_strain_factor": 0.0, "tendon_strain_factor": 0.0},
    ]
    profile = build_exercise_region_profile(mappings)
    assert "shins" in profile
    assert "tibs" not in profile


def test_profile_takes_max_per_region():
    mappings = [
        {"tissue_region": "chest", "loading_factor": 0.9,
         "routing_factor": 0.0, "joint_strain_factor": 0.0, "tendon_strain_factor": 0.0},
        {"tissue_region": "chest", "loading_factor": 0.5,
         "routing_factor": 0.0, "joint_strain_factor": 0.0, "tendon_strain_factor": 0.0},
    ]
    profile = build_exercise_region_profile(mappings)
    assert profile["chest"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Integration: weekly menu endpoint
# ---------------------------------------------------------------------------


def _seed_exercise(session: Session, name: str, region: str, load: float = 0.9) -> int:
    """Create an exercise + tissue + mapping for testing."""
    ex = Exercise(name=name)
    session.add(ex)
    session.flush()

    tissue = Tissue(
        name=f"{region}_muscle_{ex.id}",
        display_name=f"{region} muscle",
        type="muscle",
        region=region,
    )
    session.add(tissue)
    session.flush()

    et = ExerciseTissue(
        exercise_id=ex.id,
        tissue_id=tissue.id,
        loading_factor=load,
        routing_factor=0.0,
        joint_strain_factor=0.0,
        tendon_strain_factor=0.0,
    )
    session.add(et)
    session.flush()
    return ex.id


def test_weekly_menu_structure(session: Session):
    _seed_exercise(session, "Test Bench Press", "chest")
    _seed_exercise(session, "Test Squat", "quads")
    session.commit()

    result = get_weekly_exercise_menu(session)

    assert "days" in result
    assert "today_index" in result
    assert len(result["days"]) == 7

    for day in result["days"]:
        assert "day_index" in day
        assert "day_label" in day
        assert "groups" in day
        assert "exercises" in day


def test_weekly_menu_classifies_exercises(session: Session):
    bench_id = _seed_exercise(session, "Test Bench Press", "chest")
    squat_id = _seed_exercise(session, "Test Squat", "quads")
    curl_id = _seed_exercise(session, "Test Curl", "biceps")
    session.commit()

    result = get_weekly_exercise_menu(session)

    # Collect all exercises across all days
    all_exercises = {}
    for day in result["days"]:
        for ex in day["exercises"]:
            all_exercises[ex["exercise_id"]] = ex

    assert all_exercises[bench_id]["group"] == "Push"
    assert all_exercises[squat_id]["group"] == "Legs"
    assert all_exercises[curl_id]["group"] == "Arms"


def test_weekly_menu_day_filtering(session: Session):
    _seed_exercise(session, "Test Bench Press", "chest")  # Push
    _seed_exercise(session, "Test Crunch", "core")  # Core
    session.commit()

    result = get_weekly_exercise_menu(session)

    # Tuesday (index 1) should have Push + Core
    tue = result["days"][1]
    assert "Push" in tue["groups"]
    assert "Core" in tue["groups"]
    assert len(tue["exercises"]) == 2

    # Monday (index 0) is REST — no exercises
    mon = result["days"][0]
    assert mon["groups"] == []
    assert mon["exercises"] == []


def test_weekly_menu_endpoint(client):
    resp = client.get("/api/planner/weekly-menu")
    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
    assert len(data["days"]) == 7


def test_weekly_menu_includes_freshness_fields(session: Session):
    ex_id = _seed_exercise(session, "Test Press", "chest")
    session.commit()

    result = get_weekly_exercise_menu(session)

    # Find the exercise in a day that has Push
    for day in result["days"]:
        for ex in day["exercises"]:
            if ex["exercise_id"] == ex_id:
                assert "days_since_trained" in ex
                assert "allow_heavy_loading" in ex
                assert "load_input_mode" in ex
                assert "is_bodyweight" in ex
                assert "recent_rpe_sets" in ex
                assert "has_curve_fit" in ex
                assert "group" in ex
                assert "confidence" in ex
                return
    pytest.fail("Exercise not found in any day")


def test_weekly_schedule_consistency():
    """Every group should appear on at least one day."""
    all_scheduled = set()
    for groups in WEEKLY_SCHEDULE.values():
        all_scheduled.update(groups)

    for group in GROUP_CENTROIDS:
        assert group in all_scheduled, f"Group {group} not in any scheduled day"
