from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from app.models import RecoveryCheckIn


def recovery_checkin_target_key(*, region: str, tracked_tissue_id: int | None) -> str:
    if tracked_tissue_id is not None:
        return f"tracked_tissue:{tracked_tissue_id}"
    return f"region:{region}"


def recovery_checkin_has_symptoms(row: RecoveryCheckIn) -> bool:
    return (
        row.soreness_0_10 > 0
        or row.pain_0_10 > 0
        or row.stiffness_0_10 > 0
    )


def aggregate_recovery_checkins(
    rows: Iterable[RecoveryCheckIn],
) -> dict[tuple[date, str], dict[str, int]]:
    aggregated: dict[tuple[date, str], dict[str, int]] = {}
    for row in sorted(rows, key=lambda item: (item.date, item.region, item.id or 0)):
        key = (row.date, row.region)
        current = aggregated.get(key)
        if current is None:
            aggregated[key] = {
                "soreness_0_10": row.soreness_0_10,
                "pain_0_10": row.pain_0_10,
                "stiffness_0_10": row.stiffness_0_10,
                "readiness_0_10": row.readiness_0_10,
            }
            continue
        current["soreness_0_10"] = max(current["soreness_0_10"], row.soreness_0_10)
        current["pain_0_10"] = max(current["pain_0_10"], row.pain_0_10)
        current["stiffness_0_10"] = max(current["stiffness_0_10"], row.stiffness_0_10)
        current["readiness_0_10"] = min(current["readiness_0_10"], row.readiness_0_10)
    return aggregated


def aggregate_recovery_checkins_for_day(
    rows: Iterable[RecoveryCheckIn],
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
