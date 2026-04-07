from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.models import RecoveryCheckIn, RegionSorenessCheckIn
from app.tissue_regions import canonicalize_region


def recovery_checkin_target_key(*, region: str, tracked_tissue_id: int | None) -> str:
    if tracked_tissue_id is not None:
        return f"tracked_tissue:{tracked_tissue_id}"
    return f"region:{region}"


def recovery_checkin_has_symptoms(row: RecoveryCheckIn) -> bool:
    return row.pain_0_10 > 0


def aggregate_recovery_checkins(
    rows: Iterable[RecoveryCheckIn | RegionSorenessCheckIn],
) -> dict[tuple[date, str], dict[str, int]]:
    aggregated: dict[tuple[date, str], dict[str, int]] = {}
    def normalized_region(row: RecoveryCheckIn | RegionSorenessCheckIn) -> str:
        return canonicalize_region(row.region) or row.region

    for row in sorted(rows, key=lambda item: (item.date, normalized_region(item), item.id or 0)):
        soreness = int(getattr(row, "soreness_0_10", 0) or 0)
        key = (row.date, normalized_region(row))
        current = aggregated.get(key)
        if current is None:
            aggregated[key] = {
                "soreness_0_10": soreness,
            }
            continue
        current["soreness_0_10"] = max(current["soreness_0_10"], soreness)
    return aggregated


def aggregate_recovery_checkins_for_day(
    rows: Iterable[RecoveryCheckIn | RegionSorenessCheckIn],
    target_date: date,
) -> dict[str, dict[str, int]]:
    aggregated = aggregate_recovery_checkins(
        row for row in rows if row.date == target_date
    )
    return {
        region: values
        for (row_date, region), values in aggregated.items()
        if row_date == target_date
    }
