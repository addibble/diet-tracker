from datetime import UTC, date, datetime

from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TrainingExclusionWindow,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.seed_tissues import seed_exercise_tissue_model_defaults


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
