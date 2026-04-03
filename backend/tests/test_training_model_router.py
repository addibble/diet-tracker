from datetime import date

from sqlmodel import Session

from app.models import (
    Exercise,
    ExerciseTissue,
    RecoveryCheckIn,
    RehabPlan,
    Tissue,
    TissueCondition,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
)


def test_recovery_checkin_targets_include_rehab_last_workout_and_yesterday_symptoms(client, session: Session):
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
            readiness_0_10=5,
        )
    )
    session.add(
        RecoveryCheckIn(
            date=date(2026, 4, 2),
            region="shoulders",
            soreness_0_10=5,
            readiness_0_10=4,
        )
    )
    session.commit()

    response = client.get("/api/training-model/check-in-targets?date=2026-04-03")

    assert response.status_code == 200
    payload = response.json()
    targets = {item["target_key"]: item for item in payload["targets"]}

    tracked_target = targets[f"tracked_tissue:{tracked.id}"]
    tracked_reasons = [reason["code"] for reason in tracked_target["reasons"]]
    assert tracked_reasons == ["active_rehab", "active_condition", "symptomatic_yesterday"]
    assert tracked_target["target_label"] == "Right Common Extensor Tendon"

    chest_target = targets["region:chest"]
    assert [reason["code"] for reason in chest_target["reasons"]] == ["worked_last_workout"]

    shoulder_target = targets["region:shoulders"]
    assert [reason["code"] for reason in shoulder_target["reasons"]] == ["symptomatic_yesterday"]


def test_create_recovery_checkin_upserts_by_target_and_keeps_region_entry(client, session: Session):
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
            "soreness_0_10": 2,
            "pain_0_10": 1,
            "stiffness_0_10": 0,
            "readiness_0_10": 7,
        },
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["region"] == "forearms"
    assert first_payload["target_kind"] == "tracked_tissue"
    assert first_payload["tracked_tissue_id"] == tracked.id

    second = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "tracked_tissue_id": tracked.id,
            "soreness_0_10": 5,
            "pain_0_10": 3,
            "stiffness_0_10": 2,
            "readiness_0_10": 4,
        },
    )
    assert second.status_code == 201
    second_payload = second.json()
    assert second_payload["id"] == first_payload["id"]
    assert second_payload["soreness_0_10"] == 5

    region_row = client.post(
        "/api/training-model/check-in",
        json={
            "date": "2026-04-03",
            "region": "forearms",
            "soreness_0_10": 1,
            "pain_0_10": 0,
            "stiffness_0_10": 1,
            "readiness_0_10": 8,
        },
    )
    assert region_row.status_code == 201
    region_payload = region_row.json()
    assert region_payload["target_kind"] == "region"

    listing = client.get("/api/training-model/check-ins?date=2026-04-03")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 2
    target_kinds = {row["target_kind"] for row in rows}
    assert target_kinds == {"region", "tracked_tissue"}
