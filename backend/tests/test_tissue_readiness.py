from datetime import UTC, date, datetime

from app.models import Exercise, ExerciseTissue, Tissue, WeightLog, WorkoutSession, WorkoutSet


def test_tissue_readiness_uses_session_time_not_session_id(client, session):
    tf = Tissue(
        name="tensor_fasciae_latae",
        display_name="Tensor Fasciae Latae",
        recovery_hours=48.0,
    )
    sartorius = Tissue(
        name="sartorius",
        display_name="Sartorius",
        recovery_hours=48.0,
    )
    exercise = Exercise(name="Walking Lunges")
    session.add(tf)
    session.add(sartorius)
    session.add(exercise)
    session.commit()

    session.add(ExerciseTissue(exercise_id=exercise.id, tissue_id=tf.id, role="stabilizer", loading_factor=0.2))
    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=sartorius.id,
            role="secondary",
            loading_factor=0.2,
        )
    )
    session.commit()

    recent_session = WorkoutSession(
        date=date(2026, 3, 12),
        started_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        notes="Recent session inserted first",
    )
    session.add(recent_session)
    session.commit()

    old_session = WorkoutSession(
        date=date(2025, 9, 28),
        started_at=datetime(2025, 9, 28, 12, 0, tzinfo=UTC),
        notes="Older historical session inserted second",
    )
    session.add(old_session)
    session.commit()

    assert recent_session.id < old_session.id

    session.add(
        WorkoutSet(
            session_id=recent_session.id,
            exercise_id=exercise.id,
            set_order=1,
            reps=10,
            weight=40.0,
        )
    )
    session.add(
        WorkoutSet(
            session_id=old_session.id,
            exercise_id=exercise.id,
            set_order=1,
            reps=10,
            weight=40.0,
        )
    )
    session.commit()

    resp = client.get("/api/tissue-readiness")

    assert resp.status_code == 200
    readiness = {row["tissue"]["name"]: row for row in resp.json()}

    assert readiness["tensor_fasciae_latae"]["last_trained"].startswith("2026-03-12T12:00:00")
    assert readiness["sartorius"]["last_trained"].startswith("2026-03-12T12:00:00")


def test_tissue_readiness_counts_timed_mixed_bodyweight_volume(client, session):
    core = Tissue(
        name="transverse_abdominis",
        display_name="Transverse Abdominis",
        recovery_hours=36.0,
    )
    exercise = Exercise(
        name="Weighted Plank",
        load_input_mode="mixed",
        bodyweight_fraction=0.6,
    )
    session.add(core)
    session.add(exercise)
    session.commit()

    session.add(
        ExerciseTissue(
            exercise_id=exercise.id,
            tissue_id=core.id,
            role="primary",
            loading_factor=1.0,
            routing_factor=1.0,
        )
    )
    session.add(WeightLog(weight_lb=200.0, logged_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC)))
    session.commit()

    workout_session = WorkoutSession(
        date=date.today(),
        started_at=datetime.now(UTC),
    )
    session.add(workout_session)
    session.commit()
    session.add(
        WorkoutSet(
            session_id=workout_session.id,
            exercise_id=exercise.id,
            set_order=1,
            weight=25.0,
            duration_secs=60,
        )
    )
    session.commit()

    resp = client.get("/api/tissue-readiness")
    assert resp.status_code == 200
    readiness = {row["tissue"]["name"]: row for row in resp.json()}
    assert readiness["transverse_abdominis"]["volume_7d"] == 1740
