from datetime import UTC, date, datetime

from sqlmodel import select

from app.models import (
    Exercise,
    ExerciseTissue,
    RehabCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TrackedTissue,
    TrainingExclusionWindow,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.seed_tissues import seed_exercise_tissue_model_defaults, seed_reference_exercises


def _seed_leg_press_fixture(session):
    quad = Tissue(name="vastus_lateralis", display_name="Vastus Lateralis", type="muscle", recovery_hours=72.0)
    tendon = Tissue(name="patellar_tendon", display_name="Patellar Tendon", type="tendon", recovery_hours=72.0)
    exercise = Exercise(name="Leg Press", estimated_minutes_per_set=2.5)
    session.add(quad)
    session.add(tendon)
    session.add(exercise)
    session.commit()

    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=quad.id,
            role="primary",
            loading_factor=1.0,
            routing_factor=1.0,
            fatigue_factor=1.0,
            joint_strain_factor=0.7,
            tendon_strain_factor=0.8,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=tendon.id,
            role="secondary",
            loading_factor=0.7,
            routing_factor=0.7,
            fatigue_factor=0.8,
            joint_strain_factor=0.6,
            tendon_strain_factor=1.0,
        )
    )
    session.add(TissueModelConfig(tissue_id=quad.id, capacity_prior=1000.0))
    session.add(TissueModelConfig(tissue_id=tendon.id, capacity_prior=700.0))
    session.add(
        TrainingExclusionWindow(
            start_date=date(2025, 12, 16),
            end_date=date(2025, 12, 31),
            kind="surgery",
            notes="Ignore surgery deload",
            exclude_from_model=True,
        )
    )
    session.commit()

    heavy_dates = [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    for index, day in enumerate(heavy_dates, start=1):
        workout_session = WorkoutSession(date=day)
        session.add(workout_session)
        session.commit()
        session.add(
            WorkoutSet(
                session_id=workout_session.id,
                exercise_id=exercise.id,
                set_order=1,
                reps=10,
                weight=220.0 + index * 15.0,
                rep_completion="full",
            )
        )
        session.commit()

    # A mild deload after overload followed by a future condition note.
    deload_session = WorkoutSession(date=date(2026, 1, 7))
    session.add(deload_session)
    session.commit()
    session.add(
        WorkoutSet(
            session_id=deload_session.id,
            exercise_id=exercise.id,
            set_order=1,
            reps=8,
            weight=120.0,
            rep_completion="partial",
        )
    )
    session.add(
        TissueCondition(
            tissue_id=tendon.id,
            status="tender",
            severity=2,
            notes="Knee tendon irritation",
            updated_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        )
    )
    session.commit()
    return quad, tendon, exercise


def test_training_model_summary_and_history_include_exclusion_windows(client, session):
    quad, tendon, _exercise = _seed_leg_press_fixture(session)

    summary = client.get("/api/training-model/summary?as_of=2026-01-12")
    assert summary.status_code == 200
    payload = summary.json()

    assert payload["overview"]["tracked_tissues"] >= 2
    assert payload["overview"]["excluded_windows"][0]["start_date"] == "2025-12-16"
    assert payload["exercises"] == []
    tendon_row = next(row for row in payload["tissues"] if row["tissue"]["id"] == tendon.id)
    assert tendon_row["current_capacity"] > 0
    assert tendon_row["learned_recovery_days"] > 0

    # Verify new model transparency fields are present
    for tissue_row in payload["tissues"]:
        assert "fatigue_input" in tissue_row
        assert "current_soreness" in tissue_row
        assert "volume_rebound" in tissue_row
        assert "subjective_days" in tissue_row  # may be null
        assert tissue_row["overworked"] in ("good", "caution", "avoid")
        assert "tissue_region" in tissue_row
        assert isinstance(tissue_row["tissue_regions"], list)
        assert "last_trained_date" in tissue_row

    summary_with_exercises = client.get(
        "/api/training-model/summary?as_of=2026-01-12&include_exercises=true"
    )
    assert summary_with_exercises.status_code == 200
    assert summary_with_exercises.json()["exercises"]

    history = client.get(f"/api/training-model/tissues/{quad.id}/history?days=30&as_of=2026-01-12")
    assert history.status_code == 200
    history_payload = history.json()
    assert history_payload["tissue"]["id"] == quad.id
    assert all(not point["date"].startswith("2025-12-2") for point in history_payload["history"] if point["collapse_flag"])
    # Verify new history point fields
    for point in history_payload["history"]:
        assert "fatigue_input" in point
        assert "current_soreness" in point


def test_training_model_marks_precollapse_risk_before_trouble(client, session):
    _quad, tendon, _exercise = _seed_leg_press_fixture(session)

    history = client.get(f"/api/training-model/tissues/{tendon.id}/history?days=30&as_of=2026-01-12")
    assert history.status_code == 200
    points = history.json()["history"]

    pre_event = [point for point in points if point["date"] <= "2026-01-09"]
    assert any(point["risk_7d"] >= 50 for point in pre_event)
    assert any(point["collapse_flag"] for point in points)


def test_training_model_ignores_future_weight_logs_for_bodyweight_exposure(client, session):
    tissue = Tissue(name="rectus_abdominis", display_name="Rectus Abdominis", type="muscle", recovery_hours=24.0)
    exercise = Exercise(
        name="Crunch",
        load_input_mode="bodyweight",
        bodyweight_fraction=1.0,
    )
    session.add(tissue)
    session.add(exercise)
    session.commit()

    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=tissue.id,
            role="primary",
            loading_factor=1.0,
            routing_factor=1.0,
            fatigue_factor=1.0,
            joint_strain_factor=0.5,
            tendon_strain_factor=0.5,
        )
    )
    session.add(TissueModelConfig(tissue_id=tissue.id, capacity_prior=1000.0))
    session.add(WeightLog(weight_lb=200.0, logged_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    session.add(WeightLog(weight_lb=500.0, logged_at=datetime(2027, 1, 1, 12, 0, tzinfo=UTC)))
    session.commit()

    workout_session = WorkoutSession(date=date(2026, 1, 2))
    session.add(workout_session)
    session.commit()
    session.add(
        WorkoutSet(
            session_id=workout_session.id,
            exercise_id=exercise.id,
            set_order=1,
            reps=10,
            rep_completion="full",
        )
    )
    session.commit()

    history = client.get(f"/api/training-model/tissues/{tissue.id}/history?days=10&as_of=2026-01-05")
    assert history.status_code == 200
    points = history.json()["history"]
    jan2 = next(point for point in points if point["date"] == "2026-01-02")
    assert jan2["raw_load"] == 2000.0


def test_training_model_uses_assistance_as_negative_bodyweight_load(client, session):
    tissue = Tissue(name="latissimus_dorsi", display_name="Latissimus Dorsi", type="muscle", recovery_hours=72.0)
    exercise = Exercise(
        name="Assisted Pull-Ups",
        load_input_mode="assisted_bodyweight",
        bodyweight_fraction=1.0,
    )
    session.add(tissue)
    session.add(exercise)
    session.commit()

    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=tissue.id,
            role="primary",
            loading_factor=1.0,
            routing_factor=1.0,
            fatigue_factor=1.0,
            joint_strain_factor=0.5,
            tendon_strain_factor=0.5,
        )
    )
    session.add(TissueModelConfig(tissue_id=tissue.id, capacity_prior=1000.0))
    session.add(WeightLog(weight_lb=200.0, logged_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC)))
    session.commit()

    workout_session = WorkoutSession(date=date(2026, 1, 2))
    session.add(workout_session)
    session.commit()
    session.add(
        WorkoutSet(
            session_id=workout_session.id,
            exercise_id=exercise.id,
            set_order=1,
            reps=10,
            weight=40.0,
            rep_completion="full",
        )
    )
    session.commit()

    history = client.get(f"/api/training-model/tissues/{tissue.id}/history?days=10&as_of=2026-01-05")
    assert history.status_code == 200
    points = history.json()["history"]
    jan2 = next(point for point in points if point["date"] == "2026-01-02")
    assert jan2["raw_load"] == 1600.0


def test_training_model_exclusion_window_crud(client):
    listed = client.get("/api/training-model/exclusion-windows")
    assert listed.status_code == 200

    created = client.post(
        "/api/training-model/exclusion-windows",
        json={
            "start_date": "2026-02-01",
            "end_date": "2026-02-05",
            "kind": "travel",
            "notes": "Planned travel",
            "exclude_from_model": True,
        },
    )
    assert created.status_code == 201

    listed_after = client.get("/api/training-model/exclusion-windows")
    assert any(row["kind"] == "travel" for row in listed_after.json())


def test_training_model_exercise_risk_ranking_query(client, session):
    _quad, tendon, exercise = _seed_leg_press_fixture(session)

    response = client.get(
        "/api/training-model/exercises?as_of=2026-01-12&sort_by=risk_7d&direction=desc&limit=10"
    )
    assert response.status_code == 200
    rows = response.json()
    assert rows
    leg_press = next(row for row in rows if row["id"] == exercise.id)
    assert leg_press["weighted_risk_7d"] >= 0
    assert leg_press["recommendation"] in {"avoid", "caution", "good"}
    assert leg_press["recommendation_reason"]
    assert isinstance(leg_press["recommendation_details"], list)
    assert any(tissue["tissue_id"] == tendon.id for tissue in leg_press["tissues"])

    avoid_only = client.get(
        "/api/training-model/exercises?as_of=2026-01-12&recommendation=avoid"
    )
    assert avoid_only.status_code == 200
    assert all(row["recommendation"] == "avoid" for row in avoid_only.json())


def test_seed_exercise_tissue_model_defaults_repairs_legacy_defaulted_factors(session):
    muscle = Tissue(name="test_secondary_muscle", display_name="Test Secondary Muscle", type="muscle")
    session.add(muscle)
    session.commit()

    exercise = Exercise(name="Legacy Factor Exercise")
    session.add(exercise)
    session.commit()

    mapping = ExerciseTissue(
        exercise_id=exercise.id,
        tissue_id=muscle.id,
        role="secondary",
        loading_factor=0.7,
        routing_factor=1.0,
        fatigue_factor=1.0,
        joint_strain_factor=1.0,
        tendon_strain_factor=1.0,
    )
    session.add(mapping)
    session.commit()

    seed_exercise_tissue_model_defaults(session)
    session.refresh(mapping)

    assert mapping.routing_factor == 0.455
    assert mapping.fatigue_factor == 0.4095
    assert mapping.joint_strain_factor == 0.455
    assert mapping.tendon_strain_factor == 0.455


def test_seed_reference_exercises_repairs_single_leg_extension_mapping(session):
    tissue_specs = [
        ("rectus_femoris", "Rectus Femoris", "muscle"),
        ("vastus_lateralis", "Vastus Lateralis", "muscle"),
        ("vastus_medialis", "Vastus Medialis", "muscle"),
        ("vastus_intermedius", "Vastus Intermedius", "muscle"),
        ("patellar_tendon", "Patellar Tendon", "tendon"),
        ("knee_joint", "Knee Joint", "joint"),
        ("hip_joint", "Hip Joint", "joint"),
    ]
    tissues = []
    for name, display_name, tissue_type in tissue_specs:
        tissue = Tissue(name=name, display_name=display_name, type=tissue_type)
        session.add(tissue)
        tissues.append(tissue)
    exercise = Exercise(name="Single Leg Extension")
    session.add(exercise)
    session.commit()

    legacy = ExerciseTissue(
        exercise_id=exercise.id,
        tissue_id=tissues[0].id,
        role="primary",
        loading_factor=0.75,
    )
    session.add(legacy)
    session.commit()

    seed_reference_exercises(session)

    mappings = session.exec(
        select(ExerciseTissue).where(ExerciseTissue.exercise_id == exercise.id)
    ).all()
    by_tissue = {session.get(Tissue, row.tissue_id).name: row for row in mappings}

    assert by_tissue["rectus_femoris"].role == "secondary"
    assert by_tissue["rectus_femoris"].loading_factor == 0.4
    assert by_tissue["vastus_lateralis"].role == "primary"
    assert by_tissue["vastus_lateralis"].loading_factor == 0.8
    assert by_tissue["patellar_tendon"].role == "secondary"
    assert by_tissue["patellar_tendon"].loading_factor == 0.65


def test_training_model_respects_active_tissue_condition_in_exercise_ranking(client, session):
    supraspinatus = Tissue(
        name="supraspinatus",
        display_name="Supraspinatus",
        type="muscle",
        recovery_hours=72.0,
    )
    quad = Tissue(
        name="vastus_medialis",
        display_name="Vastus Medialis",
        type="muscle",
        recovery_hours=72.0,
    )
    shoulder_press = Exercise(name="Shoulder Press")
    leg_extension = Exercise(name="Leg Extension")
    session.add(supraspinatus)
    session.add(quad)
    session.add(shoulder_press)
    session.add(leg_extension)
    session.commit()

    session.add(
        ExerciseTissue(
            exercise_id=shoulder_press.id,
            tissue_id=supraspinatus.id,
            role="primary",
            loading_factor=0.8,
            routing_factor=0.8,
            fatigue_factor=0.72,
            joint_strain_factor=0.8,
            tendon_strain_factor=0.8,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=leg_extension.id,
            tissue_id=quad.id,
            role="primary",
            loading_factor=0.8,
            routing_factor=0.8,
            fatigue_factor=0.72,
            joint_strain_factor=0.8,
            tendon_strain_factor=0.8,
        )
    )
    session.add(
        TissueCondition(
            tissue_id=supraspinatus.id,
            status="injured",
            severity=3,
            max_loading_factor=0.2,
            notes="Shoulder tendon pain",
            updated_at=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
        )
    )
    session.commit()

    response = client.get("/api/training-model/exercises?as_of=2026-03-13&sort_by=suitability&direction=desc")
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}

    press = rows["Shoulder Press"]
    leg = rows["Leg Extension"]

    assert press["recommendation"] == "avoid"
    assert "Supraspinatus" in press["blocked_tissues"]
    assert leg["recommendation"] == "good"


def test_training_model_prefers_supported_neutral_variant_for_symptomatic_tracked_tissue(client, session):
    brachioradialis = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        recovery_hours=72.0,
        tracking_mode="paired",
    )
    session.add(brachioradialis)
    session.commit()
    session.refresh(brachioradialis)

    neutral = Exercise(
        name="Neutral Grip Cable Curl",
        laterality="unilateral",
        variant_group="curl_family",
        grip_style="neutral",
        support_style="cable_stabilized",
    )
    pronated = Exercise(
        name="Pronated Cable Curl",
        laterality="unilateral",
        variant_group="curl_family",
        grip_style="pronated",
        support_style="unsupported",
    )
    session.add(neutral)
    session.add(pronated)
    session.commit()
    session.refresh(neutral)
    session.refresh(pronated)

    session.add(
        ExerciseTissue(
            exercise_id=neutral.id,
            tissue_id=brachioradialis.id,
            role="secondary",
            loading_factor=0.18,
            routing_factor=0.18,
            fatigue_factor=0.18,
            joint_strain_factor=0.18,
            tendon_strain_factor=0.18,
            laterality_mode="selected_side_only",
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=pronated.id,
            tissue_id=brachioradialis.id,
            role="primary",
            loading_factor=0.6,
            routing_factor=0.6,
            fatigue_factor=0.6,
            joint_strain_factor=0.6,
            tendon_strain_factor=0.6,
            laterality_mode="selected_side_only",
        )
    )
    session.commit()

    left = TrackedTissue(
        tissue_id=brachioradialis.id,
        side="left",
        display_name="Left Brachioradialis",
    )
    right = TrackedTissue(
        tissue_id=brachioradialis.id,
        side="right",
        display_name="Right Brachioradialis",
    )
    session.add(left)
    session.add(right)
    session.commit()
    session.refresh(left)

    session.add(
        TissueCondition(
            tissue_id=brachioradialis.id,
            tracked_tissue_id=left.id,
            status="tender",
            severity=2,
            max_loading_factor=0.25,
            updated_at=datetime(2026, 4, 1, 8, 0, tzinfo=UTC),
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
            next_day_flare=2,
            recorded_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        )
    )
    session.commit()

    response = client.get(
        "/api/training-model/exercises?as_of=2026-04-01&sort_by=suitability&direction=desc"
    )
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}

    neutral_row = rows["Neutral Grip Cable Curl"]
    pronated_row = rows["Pronated Cable Curl"]

    assert neutral_row["recommendation"] != "avoid"
    assert pronated_row["recommendation"] == "avoid"
    assert neutral_row["suitability_score"] > pronated_row["suitability_score"]
    assert any(
        "safer variant" in detail.lower() or "ceiling" in detail.lower()
        for detail in neutral_row["recommendation_details"] + pronated_row["recommendation_details"]
    )
