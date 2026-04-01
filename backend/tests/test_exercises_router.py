from sqlmodel import Session

from app.models import Tissue, TissueRelationship


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


def test_exercise_response_exposes_new_metadata_and_mapping_warning(client, session: Session):
    muscle = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        recovery_hours=72.0,
    )
    tendon = Tissue(
        name="common_extensor_tendon",
        display_name="Common Extensor Tendon",
        type="tendon",
        recovery_hours=96.0,
    )
    session.add(muscle)
    session.add(tendon)
    session.commit()
    session.refresh(muscle)
    session.refresh(tendon)

    session.add(
        TissueRelationship(
            source_tissue_id=muscle.id,
            target_tissue_id=tendon.id,
            relationship_type="muscle_to_tendon",
            required_for_mapping_warning=True,
        )
    )
    session.commit()

    resp = client.post(
        "/api/exercises",
        json={
            "name": "Neutral Grip Curl",
            "load_input_mode": "external_weight",
            "laterality": "unilateral",
            "external_load_multiplier": 2.0,
            "variant_group": "curl_family",
            "grip_style": "neutral",
            "grip_width": "shoulder_width",
            "support_style": "cable_stabilized",
            "set_metric_mode": "reps",
            "tissues": [
                {
                    "tissue_id": muscle.id,
                    "role": "primary",
                    "loading_factor": 0.8,
                }
            ],
        },
    )
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["external_load_multiplier"] == 2.0
    assert payload["variant_group"] == "curl_family"
    assert payload["grip_style"] == "neutral"
    assert payload["grip_width"] == "shoulder_width"
    assert payload["support_style"] == "cable_stabilized"
    assert payload["set_metric_mode"] == "reps"
    assert payload["load_preview"]["external_load_multiplier"] == 2.0
    assert payload["mapping_warnings"]
    assert "Common Extensor Tendon" in payload["mapping_warnings"][0]["message"]
    suggested = payload["mapping_warnings"][0]["suggested_mapping"]
    assert suggested["role"] == "primary"
    assert suggested["loading_factor"] == 0.8
    assert suggested["tendon_strain_factor"] > suggested["routing_factor"]
    assert suggested["laterality_mode"] == "selected_side_only"


def test_exercise_response_warns_when_variant_sibling_mappings_diverge(client, session: Session):
    biceps = Tissue(
        name="biceps_long_head",
        display_name="Biceps Long Head",
        type="muscle",
        recovery_hours=72.0,
    )
    brachioradialis = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        recovery_hours=72.0,
    )
    session.add(biceps)
    session.add(brachioradialis)
    session.commit()
    session.refresh(biceps)
    session.refresh(brachioradialis)

    first = client.post(
        "/api/exercises",
        json={
            "name": "Neutral Grip Cable Curl",
            "load_input_mode": "external_weight",
            "variant_group": "curl_family",
            "grip_style": "neutral",
            "support_style": "cable_stabilized",
            "tissues": [
                {"tissue_id": biceps.id, "role": "primary", "loading_factor": 0.8},
                {"tissue_id": brachioradialis.id, "role": "secondary", "loading_factor": 0.35},
            ],
        },
    )
    assert first.status_code == 201

    second = client.post(
        "/api/exercises",
        json={
            "name": "Straight Bar Cable Curl",
            "load_input_mode": "external_weight",
            "variant_group": "curl_family",
            "grip_style": "pronated",
            "support_style": "cable_stabilized",
            "tissues": [
                {"tissue_id": biceps.id, "role": "primary", "loading_factor": 0.8},
            ],
        },
    )
    assert second.status_code == 201
    payload = second.json()

    assert any(
        warning["code"] == "variant-mapping-divergence"
        for warning in payload["mapping_warnings"]
    )


def test_apply_missing_related_mapping_warning_adds_mapping_with_source_loading_factor(client, session: Session):
    muscle = Tissue(
        name="brachioradialis",
        display_name="Brachioradialis",
        type="muscle",
        recovery_hours=72.0,
    )
    tendon = Tissue(
        name="brachioradialis_tendon",
        display_name="Brachioradialis Tendon",
        type="tendon",
        recovery_hours=96.0,
    )
    session.add(muscle)
    session.add(tendon)
    session.commit()
    session.refresh(muscle)
    session.refresh(tendon)

    session.add(
        TissueRelationship(
            source_tissue_id=muscle.id,
            target_tissue_id=tendon.id,
            relationship_type="muscle_to_tendon",
            required_for_mapping_warning=True,
        )
    )
    session.commit()

    created = client.post(
        "/api/exercises",
        json={
            "name": "Single-Arm Cable Curl",
            "load_input_mode": "external_weight",
            "laterality": "unilateral",
            "tissues": [
                {
                    "tissue_id": muscle.id,
                    "role": "primary",
                    "loading_factor": 0.65,
                }
            ],
        },
    )
    assert created.status_code == 201
    payload = created.json()
    warning = next(
        item for item in payload["mapping_warnings"] if item["code"] == "missing-related-tissue"
    )

    applied = client.post(
        f"/api/exercises/{payload['id']}/mapping-warnings/apply",
        json={
            "code": warning["code"],
            "source_tissue_id": warning["source_tissue_id"],
            "target_tissue_id": warning["target_tissue_id"],
        },
    )
    assert applied.status_code == 200
    updated = applied.json()
    tendon_mapping = next(
        mapping for mapping in updated["tissues"] if mapping["tissue_id"] == tendon.id
    )
    assert tendon_mapping["loading_factor"] == 0.65
    assert tendon_mapping["role"] == "primary"
    assert tendon_mapping["laterality_mode"] == "selected_side_only"
    assert tendon_mapping["tendon_strain_factor"] > tendon_mapping["routing_factor"]
    assert not any(
        item["code"] == "missing-related-tissue" and item["target_tissue_id"] == tendon.id
        for item in updated["mapping_warnings"]
    )
