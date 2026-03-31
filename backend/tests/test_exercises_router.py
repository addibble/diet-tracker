from sqlmodel import Session

from app.models import Tissue


def test_create_exercise_exposes_laterality_and_mapping_laterality_mode(client, session: Session):
    tissue = Tissue(
        name="biceps_long_head",
        display_name="Biceps Long Head",
        type="muscle",
        recovery_hours=48.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    resp = client.post(
        "/api/exercises",
        json={
            "name": "Single-Arm Dumbbell Curl",
            "load_input_mode": "external_weight",
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "role": "primary",
                    "loading_factor": 1.0,
                }
            ],
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["laterality"] == "unilateral"
    assert payload["tissues"][0]["laterality_mode"] == "contralateral_carryover"


def test_update_exercise_accepts_explicit_laterality_mode(client, session: Session):
    tissue = Tissue(
        name="shoulder_joint",
        display_name="Shoulder Joint",
        type="joint",
        recovery_hours=72.0,
    )
    session.add(tissue)
    session.commit()
    session.refresh(tissue)

    created = client.post(
        "/api/exercises",
        json={
            "name": "Landmine Press",
            "load_input_mode": "external_weight",
            "laterality": "either",
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "role": "primary",
                    "loading_factor": 0.8,
                    "laterality_mode": "selected_side_only",
                }
            ],
        },
    )
    assert created.status_code == 201
    exercise_id = created.json()["id"]

    updated = client.put(
        f"/api/exercises/{exercise_id}",
        json={
            "laterality": "unilateral",
            "tissues": [
                {
                    "tissue_id": tissue.id,
                    "role": "primary",
                    "loading_factor": 0.7,
                    "laterality_mode": "selected_side_primary",
                }
            ],
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["laterality"] == "unilateral"
    assert payload["tissues"][0]["laterality_mode"] == "selected_side_primary"
