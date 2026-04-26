"""Tests for exercise_groups module — classification and group menu."""

import math
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.config import user_today
from app.exercise_groups import (
    ALL_GROUPS,
    GROUP_COOLDOWN_DAYS,
    build_exercise_region_profile,
    classify_exercise,
    cosine_similarity,
    get_group_exercise_menu,
)
from app.models import Exercise, ExerciseTissue, Tissue, WorkoutSession, WorkoutSet

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
    assert conf > 0.7


def test_classify_quad_dominant_as_legs():
    group, conf = classify_exercise({"quads": 0.8, "hamstrings": 0.7, "glutes": 0.6})
    assert group == "Legs"
    assert conf > 0.7


def test_classify_shoulder_dominant_as_shoulders():
    group, conf = classify_exercise({"shoulders": 1.0})
    assert group == "Shoulders"
    assert conf > 0.7


def test_classify_bicep_dominant_as_pull():
    """Bicep-dominant exercises should land in Pull (no Arms group)."""
    group, conf = classify_exercise({"biceps": 0.9, "forearms": 0.7})
    assert group == "Pull"
    assert conf > 0.5


def test_classify_tricep_dominant_as_push():
    """Tricep-dominant exercises should land in Push (no Arms group)."""
    group, conf = classify_exercise({"triceps": 0.9})
    assert group == "Push"
    assert conf > 0.5


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


def test_classify_shrug_as_shoulders():
    """Shrugs (upper_back + shoulders) should land in Shoulders."""
    group, _ = classify_exercise({"upper_back": 0.9, "shoulders": 0.6})
    assert group == "Shoulders"


def test_classify_empty_as_uncategorized():
    group, conf = classify_exercise({})
    assert group == "Uncategorized"
    assert conf == 0.0


def test_five_groups_no_arms():
    """Verify there are exactly 5 groups and Arms is not among them."""
    assert len(ALL_GROUPS) == 5
    assert "Arms" not in ALL_GROUPS
    assert set(ALL_GROUPS) == {"Push", "Pull", "Legs", "Shoulders", "Core"}


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
# Integration: group menu
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


def test_group_menu_structure(session: Session):
    _seed_exercise(session, "Test Bench Press", "chest")
    _seed_exercise(session, "Test Squat", "quads")
    session.commit()

    result = get_group_exercise_menu(session)

    assert "groups" in result
    groups = result["groups"]
    assert len(groups) >= 2  # At least Push and Legs should have exercises

    for g in groups:
        assert "name" in g
        assert "available" in g
        assert "cooldown_days" in g
        assert "days_since_freshest" in g
        assert "exercises" in g


def test_group_menu_classifies_exercises(session: Session):
    bench_id = _seed_exercise(session, "Test Bench Press", "chest")
    squat_id = _seed_exercise(session, "Test Squat", "quads")
    curl_id = _seed_exercise(session, "Test Curl", "biceps")
    session.commit()

    result = get_group_exercise_menu(session)

    # Collect all exercises across all groups
    all_exercises = {}
    for g in result["groups"]:
        for ex in g["exercises"]:
            all_exercises[ex["exercise_id"]] = ex

    assert all_exercises[bench_id]["group"] == "Push"
    assert all_exercises[squat_id]["group"] == "Legs"
    assert all_exercises[curl_id]["group"] == "Pull"  # was Arms, now Pull


def test_group_menu_availability_never_trained(session: Session):
    """Groups with never-trained exercises should be available."""
    _seed_exercise(session, "Test Bench Press", "chest")
    session.commit()

    result = get_group_exercise_menu(session)
    push = next(g for g in result["groups"] if g["name"] == "Push")
    assert push["available"] is True
    assert push["days_since_freshest"] is None


def test_group_menu_availability_recently_trained(session: Session):
    """Groups with recently trained exercises should be unavailable."""
    from datetime import UTC, datetime

    ex_id = _seed_exercise(session, "Test Bench Press", "chest")
    ws = WorkoutSession(date=user_today(), notes="")
    session.add(ws)
    session.flush()
    wset = WorkoutSet(
        session_id=ws.id, exercise_id=ex_id, set_order=1,
        endurance_value=10, weight=100.0, rpe=8.0,
        completed_at=datetime.now(UTC),
    )
    session.add(wset)
    session.commit()

    result = get_group_exercise_menu(session)
    push = next(g for g in result["groups"] if g["name"] == "Push")
    assert push["available"] is False
    assert push["days_since_freshest"] == 0


def test_group_menu_availability_old_training(session: Session):
    """Groups with exercises trained >= cooldown days ago should be available."""
    from datetime import UTC, datetime

    ex_id = _seed_exercise(session, "Test Bench Press", "chest")
    old_date = user_today() - timedelta(days=5)
    ws = WorkoutSession(date=old_date, notes="")
    session.add(ws)
    session.flush()
    wset = WorkoutSet(
        session_id=ws.id, exercise_id=ex_id, set_order=1,
        endurance_value=10, weight=100.0, rpe=8.0,
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(wset)
    session.commit()

    result = get_group_exercise_menu(session)
    push = next(g for g in result["groups"] if g["name"] == "Push")
    assert push["available"] is True
    assert push["days_since_freshest"] == 5


def test_group_menu_ignores_incomplete_sets(session: Session):
    """Sets without completed_at should not count for freshness."""
    ex_id = _seed_exercise(session, "Test Bench Press", "chest")
    ws = WorkoutSession(date=user_today(), notes="")
    session.add(ws)
    session.flush()
    # Set with no completed_at (abandoned)
    wset = WorkoutSet(
        session_id=ws.id, exercise_id=ex_id, set_order=1,
        endurance_value=10, weight=100.0, rpe=8.0,
        completed_at=None,
    )
    session.add(wset)
    session.commit()

    result = get_group_exercise_menu(session)
    push = next(g for g in result["groups"] if g["name"] == "Push")
    assert push["available"] is True
    assert push["days_since_freshest"] is None


def test_group_menu_endpoint(client):
    resp = client.get("/api/planner/weekly-menu")
    assert resp.status_code == 200
    data = resp.json()
    assert "groups" in data


def test_group_menu_includes_exercise_fields(session: Session):
    _seed_exercise(session, "Test Press", "chest")
    session.commit()

    result = get_group_exercise_menu(session)

    for g in result["groups"]:
        for ex in g["exercises"]:
            assert "exercise_id" in ex
            assert "days_since_trained" in ex
            assert "allow_heavy_loading" in ex
            assert "load_input_mode" in ex
            assert "is_bodyweight" in ex
            assert "recent_rpe_sets" in ex
            assert "has_curve_fit" in ex
            assert "group" in ex
            assert "confidence" in ex
            return
    pytest.fail("No exercise found in any group")


def test_cooldown_days_configured_for_all_groups():
    """Every group should have a cooldown configured."""
    for group in ALL_GROUPS:
        assert group in GROUP_COOLDOWN_DAYS, f"Group {group} missing cooldown"

