from sqlmodel import Session

from app.models import Tissue


def test_list_tissues_returns_basic_fields(client, session: Session):
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

    resp = client.get("/api/tissues")
    assert resp.status_code == 200

    payload = resp.json()
    row = next(item for item in payload if item["id"] == tissue.id)
    assert row["name"] == "lateral_deltoid"
    assert row["tracking_mode"] == "paired"
    assert row["recovery_hours"] == 48.0


def test_create_tissue(client):
    resp = client.post(
        "/api/tissues",
        json={
            "name": "custom_muscle",
            "display_name": "Custom Muscle",
            "type": "muscle",
            "recovery_hours": 48.0,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "custom_muscle"
    assert body["type"] == "muscle"
