from datetime import date

from sqlmodel import Session, select

from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    RegionSorenessCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TrackedTissue,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)
from app.seed_tissues import seed_tissue_region_links, seed_tissue_regions


def test_recovery_checkin_targets_split_pain_and_soreness_workflows(client, session: Session):
    forearm = Tissue(
        name="common_extensor_tendon",
        display_name="Common Extensor Tendon",
        type="tendon",
        region="forearms",
        tracking_mode="paired",
    )
    chest = Tissue(
        name="pectoralis_major",
        display_name="Pectoralis Major",
        type="muscle",
        region="chest",
        tracking_mode="paired",
    )
    shoulder = Tissue(
        name="anterior_deltoid",
        display_name="Anterior Deltoid",
        type="muscle",
        region="shoulders",
        tracking_mode="paired",
    )
    session.add(forearm)
    session.add(chest)
    session.add(shoulder)
    session.commit()
    session.refresh(forearm)
    session.refresh(chest)
    session.refresh(shoulder)

    tracked = TrackedTissue(
        tissue_id=forearm.id,
        side="right",
        display_name="Right Common Extensor Tendon",
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    session.add(
        TissueCondition(
            tissue_id=forearm.id,
            tracked_tissue_id=tracked.id,
            status="rehabbing",
            severity=2,
        )
    )
    session.add(
        RehabPlan(
            tracked_tissue_id=tracked.id,
            protocol_id="lateral-elbow-brachioradialis",
            stage_id="tolerance-building",
            status="active",
        )
    )

    fly = Exercise(name="Cable Fly", equipment="cable")
    session.add(fly)
    session.commit()
    session.refresh(fly)

    session.add(
        ExerciseTissue(
            exercise_id=fly.id,
            tissue_id=chest.id,
            role="primary",
            routing_factor=1.0,
            loading_factor=1.0,
        )
    )
    workout_session = WorkoutSession(date=date(2026, 4, 2))
    session.add(workout_session)
    session.commit()
    session.refresh(workout_session)
    session.add(
        WorkoutSet(
            session_id=workout_session.id,
            exercise_id=fly.id,
            set_order=1,
            reps=12,
            weight=40,
        )
    )
    session.add(
        RecoveryCheckIn(
            date=date(2026, 4, 2),
            region="forearms",
            tracked_tissue_id=tracked.id,
            pain_0_10=4,
        )
    )
    session.add(
        RegionSorenessCheckIn(
            date=date(2026, 4, 2),
            region="shoulders",
            soreness_0_10=5,
        )
    )
    session.commit()

    response = client.get("/api/training-model/check-in-targets?date=2026-04-03")

    assert response.status_code == 200
    payload = response.json()
    pain_targets = {item["target_key"]: item for item in payload["pain_targets"]}
    soreness_targets = {item["target_key"]: item for item in payload["soreness_targets"]}

    tracked_target = pain_targets[f"tracked_tissue:{tracked.id}"]
    assert tracked_target["check_in_kind"] == "pain"
    tracked_reasons = [reason["code"] for reason in tracked_target["reasons"]]
    assert tracked_reasons == ["active_rehab", "symptomatic_yesterday"]
    assert tracked_target["target_label"] == "Right Common Extensor Tendon"

    chest_target = soreness_targets["region:chest"]
    assert chest_target["check_in_kind"] == "soreness"
    assert [reason["code"] for reason in chest_target["reasons"]] == ["worked_last_workout"]

    shoulder_target = soreness_targets["region:shoulders"]
    assert [reason["code"] for reason in shoulder_target["reasons"]] == ["symptomatic_yesterday"]


def test_create_recovery_checkin_upserts_pain_and_soreness_entries(client, session: Session):
    tissue = Tissue(
        name="common_extensor_tendon",
        display_name="Common Extensor Tendon",
        type="tendon",
        region="forearms",
        tracking_mode="paired",
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(
        tissue_id=tissue.id,
        side="right",
        display_name="Right Common Extensor Tendon",
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    first = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "tracked_tissue_id": tracked.id,
            "pain_0_10": 1,
        },
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["region"] == "forearms"
    assert first_payload["target_kind"] == "tracked_tissue"
    assert first_payload["check_in_kind"] == "pain"
    assert first_payload["tracked_tissue_id"] == tracked.id
    assert first_payload["pain_0_10"] == 1

    second = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "tracked_tissue_id": tracked.id,
            "pain_0_10": 3,
        },
    )
    assert second.status_code == 201
    second_payload = second.json()
    assert second_payload["id"] == first_payload["id"]
    assert second_payload["pain_0_10"] == 3

    region_row = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "region": "forearms",
            "soreness_0_10": 1,
        },
    )
    assert region_row.status_code == 201
    region_payload = region_row.json()
    assert region_payload["check_in_kind"] == "soreness"
    assert region_payload["target_kind"] == "region"
    assert region_payload["soreness_0_10"] == 1

    listing = client.get("/api/training-model/check-ins?date=2026-04-03")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 2
    check_in_kinds = {row["check_in_kind"] for row in rows}
    assert check_in_kinds == {"pain", "soreness"}


def test_recovery_checkin_targets_skip_neural_sarcopenia_style_rehab_plans(client, session: Session):
    tissue = Tissue(
        name="anterior_deltoid",
        display_name="Anterior Deltoid",
        type="muscle",
        region="shoulders",
        tracking_mode="paired",
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(
        tissue_id=tissue.id,
        side="right",
        display_name="Right Anterior Deltoid",
    )
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    session.add(
        TissueCondition(
            tissue_id=tissue.id,
            tracked_tissue_id=tracked.id,
            status="healthy",
            severity=3,
            notes="Sarcopenia / weakness",
        )
    )
    session.add(
        RehabPlan(
            tracked_tissue_id=tracked.id,
            protocol_id="cervical-radiculopathy-deltoid",
            stage_id="activation-and-control",
            status="active",
        )
    )
    session.commit()

    response = client.get("/api/training-model/check-in-targets?date=2026-04-03")

    assert response.status_code == 200
    target_keys = {item["target_key"] for item in response.json()["targets"]}
    assert f"tracked_tissue:{tracked.id}" not in target_keys


def test_recovery_checkin_targets_do_not_include_required_tendon_companion_for_symptomatic_chain(client, session: Session):
    muscle = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        region="forearms",
        tracking_mode="paired",
    )
    tendon = Tissue(
        name="common_extensor_tendon",
        display_name="Common Extensor Tendon",
        type="tendon",
        region="forearms",
        tracking_mode="paired",
    )
    session.add(muscle)
    session.add(tendon)
    session.commit()
    session.refresh(muscle)
    session.refresh(tendon)

    muscle_tracked = TrackedTissue(
        tissue_id=muscle.id,
        side="right",
        display_name="Right Brachioradialis",
    )
    tendon_tracked = TrackedTissue(
        tissue_id=tendon.id,
        side="right",
        display_name="Right Common Extensor Tendon",
    )
    session.add(muscle_tracked)
    session.add(tendon_tracked)
    session.commit()
    session.refresh(muscle_tracked)
    session.refresh(tendon_tracked)

    session.add(
        TissueCondition(
            tissue_id=muscle.id,
            tracked_tissue_id=muscle_tracked.id,
            status="tender",
            severity=2,
        )
    )
    session.commit()

    response = client.get("/api/training-model/check-in-targets?date=2026-04-03")

    assert response.status_code == 200
    targets = {item["target_key"]: item for item in response.json()["targets"]}
    assert f"tracked_tissue:{muscle_tracked.id}" in targets
    assert f"tracked_tissue:{tendon_tracked.id}" not in targets


def test_create_recovery_checkin_invalidates_unstarted_same_day_plan(client, session: Session):
    exercise = Exercise(name="Bench Press", equipment="barbell")
    session.add(exercise)
    session.commit()
    session.refresh(exercise)

    program = TrainingProgram(name="Check-in Invalidation Program")
    session.add(program)
    session.commit()
    session.refresh(program)

    day = ProgramDay(
        program_id=program.id,
        day_label="Push",
        target_regions='["chest"]',
    )
    session.add(day)
    session.commit()
    session.refresh(day)

    session.add(
        ProgramDayExercise(
            program_day_id=day.id,
            exercise_id=exercise.id,
            target_sets=3,
            target_rep_min=8,
            target_rep_max=12,
            sort_order=0,
        )
    )
    planned = PlannedSession(
        program_day_id=day.id,
        date=date(2026, 4, 3),
        status="planned",
    )
    session.add(planned)
    session.commit()

    response = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "region": "chest",
            "soreness_0_10": 5,
        },
    )

    assert response.status_code == 201
    remaining_plan = session.exec(
        select(PlannedSession).where(PlannedSession.date == date(2026, 4, 3))
    ).first()
    assert remaining_plan is None


def test_get_regions_returns_primary_and_overlap_associations(client, session: Session):
    glute = Tissue(
        name="gluteus_medius",
        display_name="Gluteus Medius",
        type="muscle",
        region="other",
        tracking_mode="paired",
    )
    wrist = Tissue(
        name="wrist_joint",
        display_name="Wrist Joint",
        type="joint",
        region="other",
        tracking_mode="paired",
    )
    unmapped = Tissue(
        name="mystery_structure",
        display_name="Mystery Structure",
        type="muscle",
        region="other",
        tracking_mode="paired",
    )
    session.add(glute)
    session.add(wrist)
    session.add(unmapped)
    session.commit()

    seed_tissue_regions(session)
    seed_tissue_region_links(session)

    response = client.get("/api/training-model/regions")

    assert response.status_code == 200
    payload = {item["region"]: item for item in response.json()}

    glutes = {item["name"]: item for item in payload["glutes"]["tissues"]}
    outer_leg = {item["name"]: item for item in payload["outer_leg_abductor"]["tissues"]}
    forearms = {item["name"]: item for item in payload["forearms"]["tissues"]}
    hands = {item["name"]: item for item in payload["hands"]["tissues"]}
    unmapped_group = {item["name"]: item for item in payload["unmapped"]["tissues"]}

    assert glutes["gluteus_medius"]["is_primary"] is True
    assert outer_leg["gluteus_medius"]["is_primary"] is False
    assert forearms["wrist_joint"]["is_primary"] is True
    assert hands["wrist_joint"]["is_primary"] is False
    assert unmapped_group["mystery_structure"]["regions"] == []
