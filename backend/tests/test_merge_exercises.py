"""Tests for the set_exercises merge operation."""

import datetime as dt

import pytest
from sqlmodel import select

from app.llm_tools.workout import SET_EXERCISES_DEF, handle_get_exercises, handle_set_exercises
from app.models import (
    Exercise,
    ExerciseTissue,
    ProgramDay,
    ProgramDayExercise,
    Tissue,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)


@pytest.fixture()
def two_exercises(session):
    """Source exercise (to remove) and target exercise (to keep)."""
    src = Exercise(name="DB Bench Press", equipment="dumbbell")
    tgt = Exercise(name="Dumbbell Bench Press", equipment="dumbbell")
    session.add_all([src, tgt])
    session.commit()
    session.refresh(src)
    session.refresh(tgt)
    return src, tgt


@pytest.fixture()
def tissues(session):
    t1 = Tissue(name="pec_sternal_head", display_name="Pec Sternal Head")
    t2 = Tissue(name="anterior_deltoid", display_name="Anterior Deltoid")
    t3 = Tissue(name="triceps_lateral_head", display_name="Triceps Lateral")
    session.add_all([t1, t2, t3])
    session.commit()
    for t in [t1, t2, t3]:
        session.refresh(t)
    return t1, t2, t3


def _merge(session, source_name, target_name):
    return handle_set_exercises(
        {
            "changes": [
                {
                    "operation": "merge",
                    "match": {"name": {"eq": source_name}},
                    "merge_into": {"name": {"eq": target_name}},
                }
            ]
        },
        session,
    )


def test_merge_moves_workout_sets(two_exercises, session):
    src, tgt = two_exercises
    ws = WorkoutSession(date=dt.date(2026, 3, 1))
    session.add(ws)
    session.flush()
    for i in range(3):
        session.add(
            WorkoutSet(
                session_id=ws.id, exercise_id=src.id,
                set_order=i, reps=10, weight=50,
            )
        )
    session.commit()

    result = _merge(session, src.name, tgt.name)

    assert result["changed_count"] == 1
    info = result["matches"][0]
    assert info["sets_moved"] == 3
    sets = session.exec(
        select(WorkoutSet).where(WorkoutSet.exercise_id == tgt.id)
    ).all()
    assert len(sets) == 3
    assert not session.exec(
        select(Exercise).where(Exercise.id == src.id)
    ).first()


def test_merge_moves_tissues_and_dedupes(two_exercises, tissues, session):
    src, tgt = two_exercises
    t_pec, t_delt, t_tri = tissues
    # Both exercises map to pec (overlap); source also has triceps (unique)
    session.add(ExerciseTissue(exercise_id=tgt.id, tissue_id=t_pec.id, role="primary"))
    session.add(ExerciseTissue(exercise_id=src.id, tissue_id=t_pec.id, role="primary"))
    session.add(ExerciseTissue(exercise_id=src.id, tissue_id=t_tri.id, role="secondary"))
    session.commit()

    result = _merge(session, src.name, tgt.name)

    info = result["matches"][0]
    assert info["tissues_moved"] == 1  # triceps moved
    assert info["tissues_dupes_removed"] == 1  # pec was a dupe

    target_tissues = session.exec(
        select(ExerciseTissue).where(ExerciseTissue.exercise_id == tgt.id)
    ).all()
    tissue_ids = {et.tissue_id for et in target_tissues}
    assert tissue_ids == {t_pec.id, t_tri.id}


def test_merge_moves_program_day_exercises_and_dedupes(
    two_exercises, session
):
    src, tgt = two_exercises
    prog = TrainingProgram(name="Test Program")
    session.add(prog)
    session.flush()
    day_a = ProgramDay(program_id=prog.id, day_label="A")
    day_b = ProgramDay(program_id=prog.id, day_label="B")
    session.add_all([day_a, day_b])
    session.flush()

    # day_a has both (overlap); day_b has only source (unique)
    session.add(ProgramDayExercise(
        program_day_id=day_a.id, exercise_id=tgt.id, target_sets=3,
    ))
    session.add(ProgramDayExercise(
        program_day_id=day_a.id, exercise_id=src.id, target_sets=4,
    ))
    session.add(ProgramDayExercise(
        program_day_id=day_b.id, exercise_id=src.id, target_sets=3,
    ))
    session.commit()

    result = _merge(session, src.name, tgt.name)

    info = result["matches"][0]
    assert info["program_days_moved"] == 1  # day_b moved
    assert info["program_days_dupes_removed"] == 1  # day_a was a dupe

    remaining = session.exec(
        select(ProgramDayExercise).where(
            ProgramDayExercise.exercise_id == tgt.id
        )
    ).all()
    assert len(remaining) == 2
    day_ids = {r.program_day_id for r in remaining}
    assert day_ids == {day_a.id, day_b.id}


def test_merge_same_exercise_errors(two_exercises, session):
    _, tgt = two_exercises
    result = _merge(session, tgt.name, tgt.name)
    assert "error" in result


def test_merge_source_not_found(two_exercises, session):
    _, tgt = two_exercises
    result = _merge(session, "Nonexistent Exercise", tgt.name)
    assert "error" in result


def test_get_exercises_omits_load_model_fields(session):
    exercise = Exercise(
        name="Push-ups",
        load_input_mode="bodyweight",
        bodyweight_fraction=0.64,
        external_load_multiplier=2.0,
    )
    session.add(exercise)
    session.commit()

    payload = handle_get_exercises({"match": {"name": {"eq": "Push-ups"}}}, session)
    match = payload["matches"][0]

    assert "load_input_mode" not in match
    assert "bodyweight_fraction" not in match
    assert "external_load_multiplier" not in match
    set_fields = (
        SET_EXERCISES_DEF["function"]["parameters"]["properties"]["changes"]["items"]["properties"]["set"][
            "properties"
        ]
    )
    assert "load_input_mode" not in set_fields
    assert "bodyweight_fraction" not in set_fields
    assert "external_load_multiplier" not in set_fields


def test_set_exercises_ignores_load_model_fields(session):
    exercise = Exercise(
        name="Push-ups",
        load_input_mode="external_weight",
        bodyweight_fraction=0.0,
        external_load_multiplier=1.0,
        notes="Original",
    )
    session.add(exercise)
    session.commit()

    result = handle_set_exercises(
        {
            "changes": [
                {
                    "operation": "update",
                    "match": {"name": {"eq": "Push-ups"}},
                    "set": {
                        "notes": "Updated note",
                        "load_input_mode": "bodyweight",
                        "bodyweight_fraction": 0.64,
                        "external_load_multiplier": 2.0,
                    },
                }
            ]
        },
        session,
    )
    session.refresh(exercise)

    assert result["warnings"] == ["Exercise load-model fields are not writable through chat tools."]
    assert exercise.notes == "Updated note"
    assert exercise.load_input_mode == "external_weight"
    assert exercise.bodyweight_fraction == 0.0
    assert exercise.external_load_multiplier == 1.0
