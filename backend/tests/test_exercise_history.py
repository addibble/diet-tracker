import json
from datetime import date

from app.exercise_history import REP_SCHEME_VERSION, canonical_rep_scheme
from app.models import Exercise, PlannedSession, ProgramDay, ProgramDayExercise, TrainingProgram, WorkoutSession, WorkoutSet


def test_canonical_rep_scheme_handles_legacy_and_v2_values():
    assert canonical_rep_scheme("heavy") == "heavy"
    assert canonical_rep_scheme("volume") == "medium"
    assert canonical_rep_scheme("light") == "volume"
    assert canonical_rep_scheme("volume", version=REP_SCHEME_VERSION) == "volume"
    assert canonical_rep_scheme("5x5", version=REP_SCHEME_VERSION) == "heavy"
    assert canonical_rep_scheme("3x20") == "volume"


def test_exercise_history_route_returns_scheme_history(client, session):
    exercise = Exercise(name="Leg Press", equipment="machine")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    _add_planned_session(session, exercise, date(2026, 3, 1), rep_scheme="volume")
    _add_workout_session(session, exercise, date(2026, 3, 1), reps=10, weight=315)

    _add_planned_session(
        session,
        exercise,
        date(2026, 3, 8),
        rep_scheme="volume",
        rep_scheme_version=REP_SCHEME_VERSION,
    )
    _add_workout_session(session, exercise, date(2026, 3, 8), reps=18, weight=225)

    _add_planned_session(
        session,
        exercise,
        date(2026, 3, 15),
        rep_scheme="heavy",
        rep_scheme_version=REP_SCHEME_VERSION,
    )
    _add_workout_session(session, exercise, date(2026, 3, 15), reps=5, weight=405)

    response = client.get(f"/api/exercises/{exercise.id}/history?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sessions"][0]["rep_scheme"] == "heavy"
    assert payload["scheme_history"]["heavy"]["date"] == "2026-03-15"
    assert payload["scheme_history"]["medium"]["date"] == "2026-03-01"
    assert payload["scheme_history"]["volume"]["date"] == "2026-03-08"


def test_workout_session_detail_excludes_current_session_from_scheme_history(client, session):
    exercise = Exercise(name="Bench Press", equipment="barbell")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    _add_workout_session(session, exercise, date(2026, 3, 1), reps=5, weight=225)
    current_session = _add_workout_session(session, exercise, date(2026, 3, 15), reps=5, weight=235)

    response = client.get(f"/api/workout-sessions/{current_session.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sets"][0]["scheme_history"]["heavy"]["date"] == "2026-03-01"


def test_update_exercise_heavy_loading_flag(client, session):
    exercise = Exercise(name="Lateral Raise", equipment="dumbbell")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    response = client.put(
        f"/api/exercises/{exercise.id}",
        json={"allow_heavy_loading": False},
    )

    assert response.status_code == 200
    assert response.json()["allow_heavy_loading"] is False


def _add_planned_session(
    session,
    exercise: Exercise,
    session_date: date,
    *,
    rep_scheme: str,
    rep_scheme_version: int | None = None,
) -> None:
    program = TrainingProgram(name=f"Program {session_date.isoformat()} {rep_scheme}")
    session.add(program)
    session.flush()

    day = ProgramDay(program_id=program.id, day_label=session_date.isoformat())
    session.add(day)
    session.flush()

    meta = {"rep_scheme": rep_scheme}
    if rep_scheme_version is not None:
        meta["rep_scheme_version"] = rep_scheme_version
    session.add(
        ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=exercise.id,
            target_sets=3,
            target_rep_min=8,
            target_rep_max=12,
            sort_order=0,
            notes=json.dumps(meta),
        )
    )
    session.add(
        PlannedSession(
            program_day_id=day.id,
            date=session_date,
            status="planned",
        )
    )
    session.commit()


def _add_workout_session(
    session,
    exercise: Exercise,
    session_date: date,
    *,
    reps: int,
    weight: float,
    set_count: int = 3,
) -> WorkoutSession:
    workout_session = WorkoutSession(date=session_date)
    session.add(workout_session)
    session.flush()

    for set_order in range(set_count):
        session.add(
            WorkoutSet(
                session_id=workout_session.id,
                exercise_id=exercise.id,
                set_order=set_order,
                reps=reps,
                weight=weight,
                rep_completion="full",
            )
        )

    session.commit()
    session.refresh(workout_session)
    return workout_session
