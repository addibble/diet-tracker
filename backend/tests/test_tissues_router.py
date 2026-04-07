from sqlmodel import Session

from app.models import Tissue, TrackedTissue


def test_list_tissues_preserves_existing_shape_and_exposes_tracking_fields(client, session: Session):
    tissue = Tissue(
        name="lateral_deltoid",
        display_name="Lateral Deltoid",
        type="muscle",
        tracking_mode="paired",
        recovery_hours=48.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked_left = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Lateral Deltoid")
    tracked_right = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Lateral Deltoid")
    session.add(tracked_left)
    session.add(tracked_right)
    session.commit()

    resp = client.get("/api/tissues")
    assert resp.status_code == 200

    payload = resp.json()
    row = next(item for item in payload if item["id"] == tissue.id)
    assert row["name"] == "lateral_deltoid"
    assert row["tracking_mode"] == "paired"
    assert row["region"] == "shoulders"
    assert row["regions"] == ["shoulders"]
    assert {tracked["side"] for tracked in row["tracked_tissues"]} == {"left", "right"}


def test_tissue_tracked_routes_are_not_shadowed_by_canonical_tissue_detail(client, session: Session):
    tissue = Tissue(
        name="achilles_tendon",
        display_name="Achilles Tendon",
        type="tendon",
        tracking_mode="paired",
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Achilles Tendon")
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    resp = client.get("/api/tissues/tracked")
    assert resp.status_code == 200
    payload = resp.json()
    assert any(item["id"] == tracked.id for item in payload)

    detail = client.get(f"/api/tissues/tracked/{tracked.id}")
    assert detail.status_code == 200
    assert detail.json()["side"] == "right"


def test_create_condition_accepts_tracked_tissue_target(client, session: Session):
    tissue = Tissue(
        name="supraspinatus_tendon",
        display_name="Supraspinatus Tendon",
        type="tendon",
        tracking_mode="paired",
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Supraspinatus Tendon")
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    resp = client.post(
        "/api/tissues/conditions",
        json={
            "tracked_tissue_id": tracked.id,
            "status": "rehabbing",
            "severity": 2,
            "notes": "Protected tendon work only",
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["tissue_id"] == tissue.id
    assert payload["tracked_tissue_id"] == tracked.id
    assert payload["status"] == "rehabbing"


def test_rehab_plan_and_check_in_flow(client, session: Session):
    tissue = Tissue(
        name="common_extensor_tendon",
        display_name="Common Extensor Tendon",
        type="tendon",
        tracking_mode="paired",
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(tissue_id=tissue.id, side="right", display_name="Right Common Extensor Tendon")
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    protocols = client.get("/api/tissues/rehab-protocols")
    assert protocols.status_code == 200
    assert any(item["id"] == "lateral-elbow-brachioradialis" for item in protocols.json())

    create_plan = client.post(
        "/api/tissues/rehab-plans",
        json={
            "tracked_tissue_id": tracked.id,
            "protocol_id": "lateral-elbow-brachioradialis",
            "stage_id": "tolerance-building",
            "notes": "Start conservatively",
        },
    )
    assert create_plan.status_code == 201
    plan = create_plan.json()
    assert plan["tracked_tissue_id"] == tracked.id
    assert plan["protocol_id"] == "lateral-elbow-brachioradialis"
    assert plan["stage_id"] == "tolerance-building"
    assert plan["pain_monitoring_threshold"] == 3

    create_check_in = client.post(
        "/api/tissues/rehab-check-ins",
        json={
            "tracked_tissue_id": tracked.id,
            "rehab_plan_id": plan["id"],
            "pain_0_10": 2,
            "during_load_pain_0_10": 3,
            "next_day_flare": 1,
            "confidence_0_10": 6,
        },
    )
    assert create_check_in.status_code == 201
    check_in = create_check_in.json()
    assert check_in["tracked_tissue_id"] == tracked.id
    assert check_in["rehab_plan_id"] == plan["id"]

    tracked_detail = client.get(f"/api/tissues/tracked/{tracked.id}")
    assert tracked_detail.status_code == 200
    active_plan = tracked_detail.json()["active_rehab_plan"]
    assert active_plan is not None
    assert active_plan["protocol_id"] == "lateral-elbow-brachioradialis"


def test_unknown_rehab_protocol_stage_is_rejected(client, session: Session):
    tissue = Tissue(
        name="achilles_tendon",
        display_name="Achilles Tendon",
        type="tendon",
        tracking_mode="paired",
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    tracked = TrackedTissue(tissue_id=tissue.id, side="left", display_name="Left Achilles Tendon")
    session.add(tracked)
    session.commit()
    session.refresh(tracked)

    resp = client.post(
        "/api/tissues/rehab-plans",
        json={
            "tracked_tissue_id": tracked.id,
            "protocol_id": "achilles-tendinopathy",
            "stage_id": "not-a-stage",
        },
    )
    assert resp.status_code == 400


def test_create_tissue_honors_manual_tracking_mode(client):
    resp = client.post(
        "/api/tissues",
        json={
            "name": "manual_center_test",
            "display_name": "Manual Center Test",
            "tracking_mode": "center",
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["tracking_mode"] == "center"
    assert [item["side"] for item in payload["tracked_tissues"]] == ["center"]
