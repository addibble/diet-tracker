from sqlmodel import Session

from app.database import _backfill_heavy_loading_defaults
from app.models import Exercise, ExerciseTissue, Tissue


def test_backfill_heavy_loading_defaults_disables_direct_shoulder_work(session: Session):
    shoulder = Tissue(name="anterior_deltoid", display_name="Anterior Deltoid", type="muscle", region="shoulders", recovery_hours=48)
    chest = Tissue(name="pectoralis_major", display_name="Pectoralis Major", type="muscle", region="chest", recovery_hours=48)
    shoulder_joint = Tissue(name="shoulder_joint", display_name="Shoulder Joint", type="joint", region="shoulders", recovery_hours=72)
    session.add(shoulder)
    session.add(chest)
    session.add(shoulder_joint)
    session.flush()

    landmine_press = Exercise(name="Landmine Press")
    curl = Exercise(name="Barbell Curl")
    bench_press = Exercise(name="Bench Press")
    incline_press = Exercise(name="Incline Barbell Press")
    session.add(landmine_press)
    session.add(curl)
    session.add(bench_press)
    session.add(incline_press)
    session.flush()

    session.add(
        ExerciseTissue(
            exercise_id=landmine_press.id,
            tissue_id=shoulder.id,
            role="primary",
            loading_factor=0.85,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=curl.id,
            tissue_id=shoulder_joint.id,
            role="stabilizer",
            loading_factor=0.1,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=bench_press.id,
            tissue_id=chest.id,
            role="primary",
            loading_factor=1.0,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=bench_press.id,
            tissue_id=shoulder.id,
            role="secondary",
            loading_factor=0.6,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=incline_press.id,
            tissue_id=chest.id,
            role="primary",
            loading_factor=1.0,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=incline_press.id,
            tissue_id=shoulder.id,
            role="primary",
            loading_factor=0.9,
        )
    )
    session.commit()

    _backfill_heavy_loading_defaults(session)
    session.refresh(landmine_press)
    session.refresh(curl)
    session.refresh(bench_press)
    session.refresh(incline_press)

    assert landmine_press.allow_heavy_loading is False
    assert curl.allow_heavy_loading is True
    assert bench_press.allow_heavy_loading is True
    assert incline_press.allow_heavy_loading is True


def test_backfill_heavy_loading_defaults_disables_direct_ab_work_only(session: Session):
    rectus = Tissue(name="rectus_abdominis", display_name="Rectus Abdominis", type="muscle", region="core", recovery_hours=24)
    transverse = Tissue(name="transverse_abdominis", display_name="Transverse Abdominis", type="muscle", region="core", recovery_hours=24)
    session.add(rectus)
    session.add(transverse)
    session.flush()

    weighted_plank = Exercise(name="Weighted Plank")
    farmers_carry = Exercise(name="Farmers Carry")
    session.add(weighted_plank)
    session.add(farmers_carry)
    session.flush()

    session.add(
        ExerciseTissue(
            exercise_id=weighted_plank.id,
            tissue_id=transverse.id,
            role="primary",
            loading_factor=0.9,
        )
    )
    session.add(
        ExerciseTissue(
            exercise_id=farmers_carry.id,
            tissue_id=rectus.id,
            role="stabilizer",
            loading_factor=0.35,
        )
    )
    session.commit()

    _backfill_heavy_loading_defaults(session)
    session.refresh(weighted_plank)
    session.refresh(farmers_carry)

    assert weighted_plank.allow_heavy_loading is False
    assert farmers_carry.allow_heavy_loading is True


def test_backfill_heavy_loading_defaults_uses_obvious_name_fallback(session: Session):
    cable_crunch = Exercise(name="Cable Crunch")
    session.add(cable_crunch)
    session.commit()
    session.refresh(cable_crunch)

    _backfill_heavy_loading_defaults(session)
    session.refresh(cable_crunch)

    assert cable_crunch.allow_heavy_loading is False
