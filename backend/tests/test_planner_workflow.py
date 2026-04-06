from datetime import date, timedelta

from app.models import (
    Exercise,
    ExerciseTissue,
    RecoveryCheckIn,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
)
from app.planner import _prescribe_all, suggest_today
from app.planner_groups import build_similarity_groups
from app.planner_workflow import _exercise_ready_tomorrow


def _add_tissue(session, *, name: str, display_name: str, region: str) -> Tissue:
    tissue = Tissue(
        name=name,
        display_name=display_name,
        type="muscle",
        region=region,
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)
    session.add(TissueModelConfig(tissue_id=tissue.id, capacity_prior=1000.0))
    session.commit()
    return tissue


def _add_exercise(session, *, name: str, mappings: list[tuple[Tissue, str, float]]) -> Exercise:
    exercise = Exercise(name=name, equipment="machine", load_input_mode="external_weight")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)
    for tissue, role, loading_factor in mappings:
        session.add(
            ExerciseTissue(
                exercise_id=exercise.id,
                tissue_id=tissue.id,
                role=role,
                loading_factor=loading_factor,
                routing_factor=loading_factor,
                fatigue_factor=loading_factor,
                joint_strain_factor=loading_factor,
                tendon_strain_factor=loading_factor,
            )
        )
    session.commit()
    return exercise


def _log_session(
    session,
    *,
    workout_date: date,
    exercises: list[tuple[Exercise, int, float]],
) -> None:
    workout_session = WorkoutSession(date=workout_date)
    session.add(workout_session)
    session.commit()
    session.refresh(workout_session)
    for index, (exercise, reps, weight) in enumerate(exercises):
        session.add(
            WorkoutSet(
                session_id=workout_session.id,
                exercise_id=exercise.id,
                set_order=index,
                reps=reps,
                weight=weight,
                rep_completion="full",
            )
        )
    session.commit()


def test_build_similarity_groups_pairs_identical_exercises():
    exercises = []
    for pair_index in range(8):
        for exercise_offset in range(2):
            exercises.append({
                "exercise_id": pair_index * 2 + exercise_offset + 1,
                "name": f"Exercise {pair_index + 1}-{exercise_offset + 1}",
                "tissues": [
                    {
                        "tissue_id": pair_index + 1,
                        "loading_factor": 1.0,
                        "routing_factor": 1.0,
                    }
                ],
            })

    groups = build_similarity_groups(exercises, priorities=[1.0] * len(exercises))

    assert len(groups) == 8
    assert all(2 <= len(group["exercises"]) <= 6 for group in groups)
    grouped_tissues = sorted(
        sorted({exercise["tissues"][0]["tissue_id"] for exercise in group["exercises"]})
        for group in groups
    )
    assert grouped_tissues == [[index] for index in range(1, 9)]


def test_prescribe_all_limits_heavy_exercises_on_shared_tissue(session):
    glute = _add_tissue(
        session,
        name="gluteus_maximus",
        display_name="Gluteus Maximus",
        region="glutes",
    )
    quad = _add_tissue(
        session,
        name="vastus_lateralis",
        display_name="Vastus Lateralis",
        region="quads",
    )
    hip_thrust = Exercise(name="Hip Thrust", equipment="barbell")
    step_up = Exercise(name="Step Up", equipment="dumbbell")
    session.add(hip_thrust)
    session.add(step_up)
    session.commit()
    session.refresh(hip_thrust)
    session.refresh(step_up)

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": hip_thrust.id,
                "name": hip_thrust.name,
                "suitability_score": 95,
                "recommendation": "good",
                "weighted_risk_7d": 8.0,
                "primary_regions": {"glutes"},
                "tissues": [
                    {
                        "tissue_id": glute.id,
                        "tissue_display_name": glute.display_name,
                        "loading_factor": 1.0,
                        "routing_factor": 1.0,
                        "recovery_state": 0.95,
                    }
                ],
            },
            {
                "id": step_up.id,
                "name": step_up.name,
                "suitability_score": 91,
                "recommendation": "good",
                "weighted_risk_7d": 10.0,
                "primary_regions": {"quads"},
                "tissues": [
                    {
                        "tissue_id": glute.id,
                        "tissue_display_name": glute.display_name,
                        "loading_factor": 0.5,
                        "routing_factor": 0.5,
                        "recovery_state": 0.95,
                    },
                    {
                        "tissue_id": quad.id,
                        "tissue_display_name": quad.display_name,
                        "loading_factor": 0.9,
                        "routing_factor": 0.9,
                        "recovery_state": 0.95,
                    },
                ],
            },
        ],
        [
            {
                "tissue": {"id": glute.id, "region": "glutes"},
                "recovery_estimate": 0.95,
                "risk_7d": 5,
                "current_condition": None,
            },
            {
                "tissue": {"id": quad.id, "region": "quads"},
                "recovery_estimate": 0.95,
                "risk_7d": 5,
                "current_condition": None,
            },
        ],
    )

    assert prescribed[0]["rep_scheme"] == "heavy"
    assert prescribed[1]["rep_scheme"] == "medium"


def test_workflow_groups_rank_exercises_by_category_and_status(session):
    today = date(2026, 4, 5)
    quad = _add_tissue(session, name="rectus_femoris", display_name="Rectus Femoris", region="quads")
    adductor = _add_tissue(session, name="adductor_longus", display_name="Adductor Longus", region="hips")
    hamstring = _add_tissue(session, name="biceps_femoris", display_name="Biceps Femoris", region="hamstrings")
    erector = _add_tissue(session, name="erector_spinae", display_name="Erector Spinae", region="lower_back")
    tib = _add_tissue(session, name="tibialis_anterior", display_name="Tibialis Anterior", region="tibs")
    calf = _add_tissue(session, name="soleus", display_name="Soleus", region="calves")

    leg_press = _add_exercise(
        session,
        name="Leg Press",
        mappings=[
            (quad, "primary", 1.0),
            (adductor, "secondary", 0.55),
        ],
    )
    split_squat = _add_exercise(
        session,
        name="Bulgarian Split Squat",
        mappings=[
            (quad, "primary", 0.95),
            (adductor, "secondary", 0.6),
        ],
    )
    leg_curl = _add_exercise(
        session,
        name="Seated Leg Curl",
        mappings=[(hamstring, "primary", 1.0)],
    )
    back_extension = _add_exercise(
        session,
        name="Back Extension",
        mappings=[
            (hamstring, "secondary", 0.7),
            (erector, "primary", 0.85),
        ],
    )
    tib_raise = _add_exercise(
        session,
        name="Tib Raise",
        mappings=[(tib, "primary", 1.0)],
    )
    calf_raise = _add_exercise(
        session,
        name="Standing Calf Raise",
        mappings=[(calf, "primary", 1.0)],
    )

    tracked_adductor = TrackedTissue(
        tissue_id=adductor.id,
        side="left",
        display_name="Left Adductor Longus",
    )
    session.add(tracked_adductor)
    session.commit()
    session.refresh(tracked_adductor)
    session.add(
        TissueCondition(
            tissue_id=adductor.id,
            tracked_tissue_id=tracked_adductor.id,
            status="rehabbing",
            severity=2,
        )
    )
    session.add(
        RecoveryCheckIn(
            date=today,
            region="hips",
            tracked_tissue_id=tracked_adductor.id,
            soreness_0_10=5,
            pain_0_10=1,
            stiffness_0_10=1,
            readiness_0_10=4,
        )
    )
    session.commit()

    _log_session(
        session,
        workout_date=today - timedelta(days=5),
        exercises=[
            (leg_curl, 10, 90.0),
            (back_extension, 12, 60.0),
        ],
    )
    _log_session(
        session,
        workout_date=today - timedelta(days=2),
        exercises=[
            (leg_press, 10, 315.0),
            (split_squat, 10, 80.0),
        ],
    )
    _log_session(
        session,
        workout_date=today - timedelta(days=1),
        exercises=[
            (tib_raise, 15, 25.0),
            (calf_raise, 15, 90.0),
        ],
    )

    result = suggest_today(session, as_of=today)

    assert result["today_plan"] is None
    assert result["tomorrow_plan"] is None
    assert result["filtered_tissues"][0]["target_label"] == "Left Adductor Longus"

    assert result["groups"]
    assert result["groups"][0]["day_label"] == "Leg Pull"

    groups_by_label = {group["day_label"]: group for group in result["groups"]}
    leg_pull = groups_by_label["Leg Pull"]
    assert leg_pull["available_count"] >= 2
    assert any(
        exercise["exercise_name"] == "Seated Leg Curl" and exercise["planner_status"] == "ready"
        for exercise in leg_pull["exercises"]
    )
    assert any(
        exercise["exercise_name"] == "Back Extension" and exercise["planner_status"] == "ready"
        for exercise in leg_pull["exercises"]
    )

    leg_push = groups_by_label["Leg Push"]
    blocked_by_name = {
        exercise["exercise_name"]: exercise
        for exercise in leg_push["exercises"]
        if exercise["planner_status"] == "blocked"
    }
    assert "Leg Press" in blocked_by_name
    assert "Bulgarian Split Squat" in blocked_by_name
    assert all(not exercise["selectable"] for exercise in blocked_by_name.values())


def test_ready_tomorrow_requires_overworked_improvement():
    assert _exercise_ready_tomorrow(
        status="overworked",
        selectable=True,
        today_metrics={"score": 0.5},
        tomorrow_metrics={"score": 0.65},
    )
    assert not _exercise_ready_tomorrow(
        status="ready",
        selectable=True,
        today_metrics={"score": 0.5},
        tomorrow_metrics={"score": 0.7},
    )
    assert not _exercise_ready_tomorrow(
        status="overworked",
        selectable=False,
        today_metrics={"score": 0.5},
        tomorrow_metrics={"score": 0.7},
    )
