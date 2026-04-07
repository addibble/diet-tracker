from app.seed_tissues import TISSUES
from app.tissue_regions import canonical_region_names, regions_for_tissue

_PRODUCTION_ONLY_TISSUES = (
    "Lumbar Spine",
    "Thoracic Spine",
    "brachioradialis_tendon",
    "extensor_digitorum_longus",
    "extensor_hallucis_longus",
    "obturator_externus",
    "obturator_internus",
)


def test_seed_and_production_tissues_all_map_to_canonical_regions():
    canonical = set(canonical_region_names())
    missing: list[str] = []
    invalid: dict[str, tuple[str, ...]] = {}

    for tissue_name in [*(item["name"] for item in TISSUES), *_PRODUCTION_ONLY_TISSUES]:
        regions = regions_for_tissue(tissue_name)
        if not regions:
            missing.append(tissue_name)
            continue
        unexpected = tuple(region for region in regions if region not in canonical)
        if unexpected:
            invalid[tissue_name] = unexpected

    assert not missing, f"Missing canonical region associations: {missing}"
    assert not invalid, f"Unexpected region associations: {invalid}"


def test_normalized_production_display_names_reuse_canonical_mapping():
    assert regions_for_tissue("Lumbar Spine") == ("lower_back",)
    assert regions_for_tissue("Thoracic Spine") == ("upper_back",)
    assert regions_for_tissue("extensor_hallucis_longus") == ("tibs", "feet")
