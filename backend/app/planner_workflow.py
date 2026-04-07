from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.exercise_history import empty_scheme_history
from app.exercise_protection import build_tracked_protection_profiles, evaluate_exercise_protection
from app.models import ExerciseTissue, RecoveryCheckIn, WorkoutSession, WorkoutSet
from app.planner import (
    _MAX_REHAB_PRIORITY_CANDIDATES,
    DEFAULT_SELECTED,
    _build_exercise_region_map,
    _build_rehab_priority_map,
    _build_selection_note,
    _prescribe_all,
)
from app.planner_groups import (
    combine_tissue_vectors,
    exercise_tissue_vector,
    similarity_to_group_profile,
)
from app.recovery_check_ins import recovery_checkin_has_symptoms
from app.tracked_tissues import (
    default_performed_side,
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_tracked_tissue_lookup,
    tracked_tissue_side_weights,
)
from app.training_model import build_training_model_summary

_DEFAULT_TISSUE_FRESHNESS_DAYS = 21
_GROUP_TODAY_MIN_EXERCISES = 2
_DAY_MIN_SELECTED_EXERCISES = 7
_DAY_TARGET_SELECTED_EXERCISES = 8
_DAY_MAX_SELECTED_EXERCISES = 10
_DAY_MAX_CANDIDATE_EXERCISES = 12
_TODAY_BLOCKED_LOAD = 0.3
_MILD_CHECKIN_ALLOWED_LOAD = 0.5


def suggest_today_workflow(session: Session, *, as_of: date | None = None) -> dict:
    today = as_of or date.today()
    summary = build_training_model_summary(session, as_of=as_of, include_exercises=True)
    tissues_data = summary.get("tissues", [])
    exercises_data = summary.get("exercises", [])
    if not tissues_data or not exercises_data:
        return {
            "as_of": today.isoformat(),
            "today_plan": None,
            "tomorrow_plan": None,
            "groups": [],
            "filtered_tissues": [],
            "message": "No training data yet. Log some workouts first.",
        }

    tissue_rows_by_id = {row["tissue"]["id"]: row for row in tissues_data}
    exercise_region_map = _build_exercise_region_map(session)
    tracked_lookup = {
        tracked.id: tracked
        for tracked in get_tracked_tissue_lookup(session).values()
    }
    tracked_conditions = get_all_current_tracked_conditions(session)
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    protection_profiles = build_tracked_protection_profiles(session, as_of=today)
    rehab_priorities = _build_rehab_priority_map(
        session=session,
        exercises_data=exercises_data,
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )

    filtered_tissues = _filtered_tissues_from_checkins(
        session=session,
        target_date=today,
        tracked_lookup=tracked_lookup,
        tracked_conditions=tracked_conditions,
        active_rehab_plans=active_rehab_plans,
    )
    tissue_last_trained = _tissue_last_trained(session, today)

    category_pool = _build_category_pool(
        exercises_data=exercises_data,
        exercise_region_map=exercise_region_map,
        tissue_last_trained=tissue_last_trained,
        tissue_rows_by_id=tissue_rows_by_id,
    )
    if not category_pool:
        return {
            "as_of": today.isoformat(),
            "today_plan": None,
            "tomorrow_plan": None,
            "groups": [],
            "filtered_tissues": filtered_tissues,
            "message": "No eligible exercises are available for planning right now.",
        }

    groups = _build_ranked_group_catalog(
        session=session,
        exercises=category_pool,
        exercise_region_map=exercise_region_map,
        tissue_last_trained=tissue_last_trained,
        tissue_rows_by_id=tissue_rows_by_id,
        filtered_tissues=filtered_tissues,
        protection_profiles=protection_profiles,
        rehab_priorities=rehab_priorities,
        tissues_data=tissues_data,
        plan_date=today,
    )

    return {
        "as_of": today.isoformat(),
        "today_plan": None,
        "tomorrow_plan": None,
        "groups": groups,
        "filtered_tissues": filtered_tissues,
        "message": None if groups else "Today's tissue check-in filtered out every general training group.",
    }


def _filtered_tissues_from_checkins(
    *,
    session: Session,
    target_date: date,
    tracked_lookup: dict[int, object],
    tracked_conditions: dict[int, object],
    active_rehab_plans: dict[int, object],
) -> list[dict]:
    rows = session.exec(
        select(RecoveryCheckIn)
        .where(RecoveryCheckIn.date == target_date)
        .order_by(col(RecoveryCheckIn.created_at).desc())
    ).all()
    filtered: list[dict] = []
    seen_tracked_ids: set[int] = set()
    for row in rows:
        tracked_tissue_id = row.tracked_tissue_id
        if tracked_tissue_id is None or tracked_tissue_id in seen_tracked_ids:
            continue
        tracked = tracked_lookup.get(tracked_tissue_id)
        if tracked is None:
            continue
        condition = tracked_conditions.get(tracked_tissue_id)
        if condition is None and tracked_tissue_id not in active_rehab_plans:
            continue
        status = getattr(condition, "status", None)
        if tracked_tissue_id in active_rehab_plans and status is None:
            status = "rehabbing"
        if status not in {"tender", "injured", "rehabbing"}:
            continue
        if not _checkin_blocks_general_loading(row):
            continue
        filtered.append({
            "tracked_tissue_id": tracked_tissue_id,
            "tissue_id": getattr(tracked, "tissue_id"),
            "tracked_side": getattr(tracked, "side", None),
            "target_label": getattr(tracked, "display_name", str(tracked_tissue_id)),
            "status": status,
            "reason": _checkin_reason_label(row),
            "max_loading_factor": _checkin_allowed_loading_factor(row),
        })
        seen_tracked_ids.add(tracked_tissue_id)
    return filtered


def _checkin_blocks_general_loading(row: RecoveryCheckIn) -> bool:
    return recovery_checkin_has_symptoms(row)


def _checkin_allowed_loading_factor(row: RecoveryCheckIn) -> float:
    symptom_peak = int(row.pain_0_10 or 0)
    if 0 < symptom_peak <= 2:
        return _MILD_CHECKIN_ALLOWED_LOAD
    return _TODAY_BLOCKED_LOAD


def _checkin_reason_label(row: RecoveryCheckIn) -> str:
    return f"pain {row.pain_0_10}/10"


def _tissue_last_trained(session: Session, today: date) -> dict[int, int]:
    cutoff = today - timedelta(days=60)
    rows = session.exec(
        select(
            ExerciseTissue.tissue_id,
            WorkoutSession.date,
            ExerciseTissue.loading_factor,
            ExerciseTissue.routing_factor,
            ExerciseTissue.joint_strain_factor,
            ExerciseTissue.tendon_strain_factor,
        )
        .join(WorkoutSet, WorkoutSet.exercise_id == ExerciseTissue.exercise_id)
        .join(WorkoutSession, WorkoutSession.id == WorkoutSet.session_id)
        .where(
            col(WorkoutSession.date) >= cutoff,
            col(WorkoutSession.date) <= today,
        )
    ).all()
    latest_by_tissue: dict[int, date] = {}
    for tissue_id, session_date, loading, routing, joint_strain, tendon_strain in rows:
        mapping_load = max(
            float(loading or 0.0),
            float(routing or 0.0),
            float(joint_strain or 0.0),
            float(tendon_strain or 0.0),
        )
        if mapping_load < _TODAY_BLOCKED_LOAD:
            continue
        if tissue_id not in latest_by_tissue or session_date > latest_by_tissue[tissue_id]:
            latest_by_tissue[tissue_id] = session_date
    return {
        tissue_id: (today - session_date).days
        for tissue_id, session_date in latest_by_tissue.items()
    }


def _build_category_pool(
    *,
    exercises_data: list[dict],
    exercise_region_map: dict[int, list[dict]],
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
) -> list[dict]:
    pool: list[dict] = []
    for exercise in exercises_data:
        exercise_id = exercise.get("exercise_id") or exercise.get("id")
        if not exercise_id:
            continue
        if not exercise_region_map.get(exercise_id):
            continue
        if not exercise_tissue_vector(exercise):
            continue
        primary_regions = {
            mapping["region"]
            for mapping in exercise_region_map[exercise_id]
            if mapping["role"] == "primary"
        } or {mapping["region"] for mapping in exercise_region_map[exercise_id]}
        dominant_regions = _exercise_dominant_regions(exercise_id, exercise_region_map)
        planning_priority = _exercise_planning_priority(
            exercise=exercise,
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
        )
        pool.append({
            **exercise,
            "exercise_id": exercise_id,
            "exercise_name": exercise.get("exercise_name") or exercise.get("name"),
            "primary_regions": primary_regions,
            "dominant_regions": dominant_regions,
            "planning_priority": planning_priority,
        })
    pool.sort(
        key=lambda exercise: (
            -float(exercise["planning_priority"]),
            -float(exercise.get("suitability_score") or 0.0),
            str(exercise.get("exercise_name") or ""),
        ),
    )
    return pool


def _exercise_projection_metrics(
    *,
    exercise: dict,
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
    days_ahead: int = 0,
) -> dict:
    vector = exercise_tissue_vector(exercise)
    if not vector:
        suitability = min(float(exercise.get("suitability_score") or 0.0) / 100.0, 1.0)
        return {
            "freshness_days": float(_DEFAULT_TISSUE_FRESHNESS_DAYS + days_ahead),
            "readiness": 0.75,
            "suitability": round(suitability, 3),
            "score": round(0.55 + suitability * 0.15, 3),
        }

    weighted_days = 0.0
    total_weight = 0.0
    day_values: list[float] = []
    weighted_recovery = 0.0
    recovery_values: list[float] = []
    for tissue_id, weight in vector.items():
        base_days = float(tissue_last_trained.get(tissue_id, _DEFAULT_TISSUE_FRESHNESS_DAYS))
        projected_days = base_days + days_ahead
        tissue_row = tissue_rows_by_id.get(tissue_id, {})
        recovery = float(tissue_row.get("recovery_estimate", 0.75))
        learned_recovery_days = max(float(tissue_row.get("learned_recovery_days", 3.0)), 1.0)
        if days_ahead > 0:
            recovery = min(1.0, recovery + (days_ahead / learned_recovery_days))
        weighted_days += projected_days * weight
        total_weight += weight
        day_values.append(projected_days)
        weighted_recovery += recovery * weight
        recovery_values.append(recovery)

    average_days = weighted_days / total_weight if total_weight > 0 else 0.0
    freshness_days = min(day_values) * 0.65 + average_days * 0.35 if day_values else 0.0
    average_recovery = weighted_recovery / total_weight if total_weight > 0 else 0.0
    readiness = min(recovery_values) * 0.6 + average_recovery * 0.4 if recovery_values else 0.0
    suitability = min(float(exercise.get("suitability_score") or 0.0) / 100.0, 1.0)
    score = min(freshness_days / 10.0, 1.0) * 0.55 + readiness * 0.3 + suitability * 0.15
    return {
        "freshness_days": round(freshness_days, 2),
        "readiness": round(readiness, 3),
        "suitability": round(suitability, 3),
        "score": round(score, 3),
    }


def _exercise_group_regions(exercise: dict, exercise_region_map: dict[int, list[dict]]) -> list[str]:
    dominant_regions = [str(region) for region in exercise.get("dominant_regions", []) if region]
    if dominant_regions:
        return dominant_regions[:3]

    exercise_id = exercise.get("exercise_id") or exercise.get("id")
    if not exercise_id:
        return []
    return _group_regions([exercise], exercise_region_map)


def _planner_status_rank(status: str) -> int:
    if status == "ready":
        return 0
    if status == "overworked":
        return 1
    return 2


def _exercise_planner_state(
    *,
    exercise: dict,
    filtered_tissues: list[dict],
    protection_eval: dict,
    rehab_priority: dict | None,
) -> tuple[str, str, bool]:
    checkin_eval = _checkin_blocking_eval(
        exercise=exercise,
        filtered_tissues=filtered_tissues,
        preferred_side=(
            str(rehab_priority.get("preferred_side"))
            if rehab_priority and rehab_priority.get("preferred_side")
            else None
        ),
    )
    if checkin_eval["blocked"]:
        blocked_labels = [str(label) for label in checkin_eval["blocked_labels"]]
        summary = ", ".join(blocked_labels[:2]) if blocked_labels else "today's check-in"
        return (
            "blocked",
            f"Today's tissue check-in is protecting {summary}.",
            False,
        )

    if protection_eval["blocked"]:
        return (
            "blocked",
            str(protection_eval.get("gating_reason") or "Protected by the current rehab setup."),
            False,
        )

    if rehab_priority is not None:
        if str(rehab_priority.get("mode") or "") == "direct_rehab":
            return ("ready", "Direct rehab support is prioritized in this category.", True)
        return ("ready", "Supportive rehab work is available here today.", True)

    recommendation = str(exercise.get("recommendation") or "good")
    details = [str(detail) for detail in exercise.get("recommendation_details", []) if detail]
    if recommendation == "good":
        return ("ready", details[0] if details else "Fresh enough to train today.", True)
    return (
        "overworked",
        details[0] if details else "Recent tissue load is still elevated today.",
        True,
    )


def _exercise_ready_tomorrow(
    *,
    status: str,
    selectable: bool,
    today_metrics: dict,
    tomorrow_metrics: dict,
) -> bool:
    if not selectable or status != "overworked":
        return False
    return (
        float(tomorrow_metrics["score"]) >= 0.62
        and float(tomorrow_metrics["score"]) >= float(today_metrics["score"]) + 0.07
    )


def _unselectable_planner_entry(candidate: dict) -> dict:
    return {
        "exercise_id": candidate["exercise_id"],
        "exercise_name": candidate["exercise_name"],
        "equipment": candidate.get("equipment"),
        "laterality": candidate.get("laterality"),
        "performed_side": candidate.get("performed_side"),
        "rep_scheme": "volume",
        "target_sets": 0,
        "target_reps": "Unavailable today",
        "target_weight": None,
        "rationale": candidate["planner_reason"],
        "overload_note": None,
        "weight_adjustment_note": None,
        "side_explanation": None,
        "selection_note": None,
        "blocked_variant": None,
        "protected_tissues": list(candidate.get("protected_tissues") or []),
        "workflow_role": candidate.get("workflow_role"),
        "group_label": candidate.get("group_label"),
        "selected": False,
        "last_performance": None,
        "scheme_history": empty_scheme_history(),
        "planner_status": candidate["planner_status"],
        "planner_reason": candidate["planner_reason"],
        "ready_tomorrow": candidate["ready_tomorrow"],
        "ready_tomorrow_reason": candidate.get("ready_tomorrow_reason"),
        "selectable": False,
        "readiness_score": candidate["today_metrics"]["readiness"],
        "days_since_last": candidate["today_metrics"]["freshness_days"],
        "recommendation": candidate.get("recommendation", "avoid"),
    }


def _planner_entry_sort_key(entry: dict) -> tuple:
    return (
        _planner_status_rank(str(entry.get("planner_status") or "blocked")),
        0 if entry.get("workflow_role") == "rehab" else 1,
        0 if entry.get("ready_tomorrow") else 1,
        -float(entry.get("readiness_score") or 0.0),
        -float(entry.get("days_since_last") or 0.0),
        str(entry.get("exercise_name") or ""),
    )


def _group_target_regions(entries: list[dict]) -> list[str]:
    region_scores: dict[str, float] = defaultdict(float)
    for entry in entries:
        for index, region in enumerate(entry.get("_group_regions", [])):
            region_scores[str(region)] += max(0.2, 1.0 - index * 0.2)
    return [
        region
        for region, _score in sorted(
            region_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[:3]
    ]


def _group_summary_metrics(entries: list[dict]) -> tuple[float, float]:
    available = [entry for entry in entries if entry.get("selectable")]
    source = available[:3] or entries[:3]
    if not source:
        return (0.0, 0.0)
    readiness = sum(float(entry.get("readiness_score") or 0.0) for entry in source) / len(source)
    freshness = sum(float(entry.get("days_since_last") or 0.0) for entry in source) / len(source)
    return (round(readiness, 3), round(freshness, 2))


def _group_rationale(*, label: str, entries: list[dict]) -> str:
    available_count = sum(1 for entry in entries if entry.get("selectable"))
    ready_tomorrow_count = sum(1 for entry in entries if entry.get("ready_tomorrow"))
    if available_count == 0:
        return f"{label} is blocked today by current symptoms or rehab protections."

    parts = [f"{available_count} selectable movement{'s' if available_count != 1 else ''} are ranked here today."]
    if entries and entries[0].get("workflow_role") == "rehab":
        parts.append("Rehab-supporting options rise to the top in this category.")
    if ready_tomorrow_count > 0:
        parts.append(
            f"{ready_tomorrow_count} other movement{'s' if ready_tomorrow_count != 1 else ''} may be ready tomorrow."
        )
    return " ".join(parts)


def _mark_default_selected_groups(groups: list[dict]) -> None:
    selected = 0
    for group in groups:
        for entry in group["exercises"]:
            should_select = bool(entry.get("selectable")) and selected < DEFAULT_SELECTED
            entry["selected"] = should_select
            if should_select:
                selected += 1


def _build_ranked_group_catalog(
    *,
    session: Session,
    exercises: list[dict],
    exercise_region_map: dict[int, list[dict]],
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
    filtered_tissues: list[dict],
    protection_profiles: dict[int, list[object]],
    rehab_priorities: dict[int, dict],
    tissues_data: list[dict],
    plan_date: date,
) -> list[dict]:
    candidates: list[dict] = []
    for exercise in exercises:
        exercise_id = int(exercise.get("exercise_id") or exercise.get("id") or 0)
        if exercise_id <= 0:
            continue

        rehab_priority = rehab_priorities.get(exercise_id)
        preferred_side = rehab_priority.get("preferred_side") if rehab_priority else None
        protection_eval = evaluate_exercise_protection(
            exercise,
            exercise,
            protection_profiles,
            preferred_side=preferred_side,
        )
        checkin_eval = _checkin_blocking_eval(
            exercise=exercise,
            filtered_tissues=filtered_tissues,
            preferred_side=preferred_side,
        )
        group_regions = _exercise_group_regions(exercise, exercise_region_map)
        group_label = _group_label(group_regions)
        today_metrics = _exercise_projection_metrics(
            exercise=exercise,
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
        )
        tomorrow_metrics = _exercise_projection_metrics(
            exercise=exercise,
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
            days_ahead=1,
        )
        planner_status, planner_reason, selectable = _exercise_planner_state(
            exercise=exercise,
            filtered_tissues=filtered_tissues,
            protection_eval=protection_eval,
            rehab_priority=rehab_priority,
        )
        ready_tomorrow = _exercise_ready_tomorrow(
            status=planner_status,
            selectable=selectable,
            today_metrics=today_metrics,
            tomorrow_metrics=tomorrow_metrics,
        )
        ready_tomorrow_reason = (
            "Projected recovery improves by tomorrow."
            if ready_tomorrow
            else None
        )
        candidates.append({
            **exercise,
            "exercise_id": exercise_id,
            "exercise_name": exercise.get("exercise_name") or exercise.get("name"),
            "performed_side": (
                preferred_side
                if preferred_side in {"left", "right", "center", "bilateral"}
                else checkin_eval.get("preferred_side") or exercise.get("performed_side")
            ),
            "workflow_role": "rehab" if rehab_priority else "group",
            "group_label": group_label,
            "_group_regions": group_regions,
            "protected_tissues": list(protection_eval.get("protected_tissues", [])),
            "planner_status": planner_status,
            "planner_reason": planner_reason,
            "ready_tomorrow": ready_tomorrow,
            "ready_tomorrow_reason": ready_tomorrow_reason,
            "selectable": selectable,
            "today_metrics": today_metrics,
            "tomorrow_metrics": tomorrow_metrics,
        })

    prescribed = _prescribe_all(
        session,
        [candidate for candidate in candidates if candidate.get("selectable")],
        tissues_data,
        as_of=plan_date,
    )
    prescribed_by_exercise_id = {
        int(entry["exercise_id"]): entry
        for entry in prescribed
    }

    grouped_entries: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        prescribed_row = prescribed_by_exercise_id.get(candidate["exercise_id"])
        if prescribed_row is None:
            entry = _unselectable_planner_entry(candidate)
        else:
            entry = {
                **prescribed_row,
                "planner_status": candidate["planner_status"],
                "planner_reason": candidate["planner_reason"],
                "ready_tomorrow": candidate["ready_tomorrow"],
                "ready_tomorrow_reason": candidate.get("ready_tomorrow_reason"),
                "selectable": bool(candidate["selectable"]),
                "readiness_score": candidate["today_metrics"]["readiness"],
                "days_since_last": candidate["today_metrics"]["freshness_days"],
                "recommendation": candidate.get("recommendation", "good"),
            }
        entry["_group_regions"] = candidate["_group_regions"]
        grouped_entries[str(candidate["group_label"])].append(entry)

    groups: list[dict] = []
    for index, (label, entries) in enumerate(grouped_entries.items(), start=1):
        entries.sort(key=_planner_entry_sort_key)
        readiness_score, freshness_days = _group_summary_metrics(entries)
        groups.append({
            "group_id": f"group-{index}",
            "day_label": label,
            "target_regions": _group_target_regions(entries),
            "exercise_count": len(entries),
            "available_count": sum(1 for entry in entries if entry.get("selectable")),
            "ready_tomorrow_count": sum(1 for entry in entries if entry.get("ready_tomorrow")),
            "readiness_score": readiness_score,
            "days_since_last": freshness_days,
            "rationale": _group_rationale(label=label, entries=entries),
            "exercises": entries,
        })

    groups.sort(
        key=lambda group: (
            _planner_status_rank(
                str(group["exercises"][0].get("planner_status") if group["exercises"] else "blocked")
            ),
            -float(group["readiness_score"]),
            -float(group["days_since_last"] or 0.0),
            -int(group["available_count"]),
            str(group["day_label"]),
        )
    )
    for group in groups:
        for entry in group["exercises"]:
            entry.pop("_group_regions", None)
    _mark_default_selected_groups(groups)
    return groups


def _exercise_dominant_regions(
    exercise_id: int,
    exercise_region_map: dict[int, list[dict]],
) -> list[str]:
    region_scores: dict[str, float] = defaultdict(float)
    for mapping in exercise_region_map.get(exercise_id, []):
        score = float(mapping.get("routing") or 0.0)
        if mapping.get("role") == "primary":
            score *= 1.0
        elif mapping.get("role") == "secondary":
            score *= 0.8
        else:
            score *= 0.5
        region_scores[mapping["region"]] += score
    return [
        region
        for region, _score in sorted(
            region_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[:3]
    ]


def _exercise_planning_priority(
    *,
    exercise: dict,
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
) -> float:
    freshness_days = _exercise_freshness_days(exercise, tissue_last_trained)
    readiness = _exercise_recovery_score(exercise, tissue_rows_by_id)
    suitability = min(float(exercise.get("suitability_score") or 0.0) / 100.0, 1.0)
    freshness_score = min(freshness_days / 10.0, 1.0)
    return round(freshness_score * 0.5 + readiness * 0.25 + suitability * 0.25, 4)


def _exercise_freshness_days(exercise: dict, tissue_last_trained: dict[int, int]) -> float:
    vector = exercise_tissue_vector(exercise)
    if not vector:
        return float(_DEFAULT_TISSUE_FRESHNESS_DAYS)
    weighted_days = sum(
        vector[tissue_id] * tissue_last_trained.get(tissue_id, _DEFAULT_TISSUE_FRESHNESS_DAYS)
        for tissue_id in vector
    )
    total_weight = sum(vector.values())
    min_days = min(tissue_last_trained.get(tissue_id, _DEFAULT_TISSUE_FRESHNESS_DAYS) for tissue_id in vector)
    average_days = weighted_days / total_weight if total_weight > 0 else _DEFAULT_TISSUE_FRESHNESS_DAYS
    return round(min_days * 0.65 + average_days * 0.35, 2)


def _exercise_recovery_score(exercise: dict, tissue_rows_by_id: dict[int, dict]) -> float:
    vector = exercise_tissue_vector(exercise)
    if not vector:
        return 0.75
    weighted_recovery = 0.0
    total_weight = 0.0
    recovery_values: list[float] = []
    for tissue_id, weight in vector.items():
        recovery = float(tissue_rows_by_id.get(tissue_id, {}).get("recovery_estimate", 0.75))
        weighted_recovery += recovery * weight
        total_weight += weight
        recovery_values.append(recovery)
    average_recovery = weighted_recovery / total_weight if total_weight > 0 else 0.75
    min_recovery = min(recovery_values) if recovery_values else 0.75
    return round(min_recovery * 0.6 + average_recovery * 0.4, 4)


def _build_group_catalog(
    *,
    groups: list[dict],
    exercise_region_map: dict[int, list[dict]],
    blocked_tissue_ids: set[int],
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
) -> list[dict]:
    catalog: list[dict] = []
    for group in groups:
        group_regions = _group_regions(group["exercises"], exercise_region_map)
        label = _group_label(group_regions)
        today_available_exercises = [
            exercise
            for exercise in group["exercises"]
            if not (_significant_tissue_ids(exercise) & blocked_tissue_ids)
        ]
        group_metrics = _group_metrics(
            group["exercises"],
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
        )
        today_metrics = _group_metrics(
            today_available_exercises,
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
        )
        catalog.append({
            **group,
            "label": label,
            "target_regions": group_regions,
            "group_metrics": group_metrics,
            "today_available_exercises": today_available_exercises,
            "today_metrics": today_metrics,
        })
    return catalog


def _group_regions(exercises: list[dict], exercise_region_map: dict[int, list[dict]]) -> list[str]:
    region_scores: dict[str, float] = defaultdict(float)
    for exercise in exercises:
        exercise_id = exercise.get("exercise_id") or exercise.get("id")
        for mapping in exercise_region_map.get(int(exercise_id), []):
            routing = float(mapping.get("routing") or 0.0)
            multiplier = 1.0 if mapping.get("role") == "primary" else 0.75
            region_scores[mapping["region"]] += routing * multiplier
    return [
        region
        for region, _score in sorted(
            region_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[:3]
    ]


def _group_label(regions: list[str]) -> str:
    region_set = set(regions)
    if len(region_set & {"chest", "shoulders", "triceps"}) >= 2:
        return "Upper Push"
    if len(region_set & {"upper_back", "biceps", "forearms"}) >= 2:
        return "Upper Pull"
    if region_set & {"quads", "glutes"} and "hamstrings" not in region_set:
        return "Leg Push"
    if region_set & {"hamstrings", "shins", "calves", "lower_back"}:
        return "Leg Pull"
    if region_set & {"core", "lower_back", "glutes"}:
        return "Core / Posterior"
    if not regions:
        return "General Training"
    return " / ".join(region.replace("_", " ").title() for region in regions[:2])


def _group_metrics(
    exercises: list[dict],
    *,
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
    days_ahead: int = 0,
    worked_today_tissues: set[int] | None = None,
) -> dict:
    profile = combine_tissue_vectors(exercises)
    if not profile:
        return {
            "freshness_days": 0.0,
            "readiness": 0.0,
            "suitability": 0.0,
            "score": 0.0,
        }
    worked_today_tissues = worked_today_tissues or set()
    weighted_days = 0.0
    total_weight = 0.0
    day_values: list[float] = []
    weighted_recovery = 0.0
    recovery_values: list[float] = []
    for tissue_id, weight in profile.items():
        base_days = float(tissue_last_trained.get(tissue_id, _DEFAULT_TISSUE_FRESHNESS_DAYS))
        projected_days = 0.0 if tissue_id in worked_today_tissues else base_days + days_ahead
        tissue_row = tissue_rows_by_id.get(tissue_id, {})
        recovery = float(tissue_row.get("recovery_estimate", 0.75))
        learned_recovery_days = max(float(tissue_row.get("learned_recovery_days", 3.0)), 1.0)
        if days_ahead > 0:
            if tissue_id in worked_today_tissues:
                recovery = max(0.2, recovery * 0.55)
            else:
                recovery = min(1.0, recovery + (days_ahead / learned_recovery_days))
        weighted_days += projected_days * weight
        total_weight += weight
        day_values.append(projected_days)
        weighted_recovery += recovery * weight
        recovery_values.append(recovery)
    average_days = weighted_days / total_weight if total_weight > 0 else 0.0
    freshness_days = min(day_values) * 0.65 + average_days * 0.35 if day_values else 0.0
    average_recovery = weighted_recovery / total_weight if total_weight > 0 else 0.0
    readiness = min(recovery_values) * 0.6 + average_recovery * 0.4 if recovery_values else 0.0
    suitability = (
        sum(min(float(exercise.get("suitability_score") or 0.0) / 100.0, 1.0) for exercise in exercises)
        / len(exercises)
        if exercises
        else 0.0
    )
    score = min(freshness_days / 10.0, 1.0) * 0.55 + readiness * 0.3 + suitability * 0.15
    return {
        "freshness_days": round(freshness_days, 2),
        "readiness": round(readiness, 3),
        "suitability": round(suitability, 3),
        "score": round(score, 3),
    }


def _select_today_group(group_catalog: list[dict]) -> dict | None:
    candidates = [
        group
        for group in group_catalog
        if len(group["today_available_exercises"]) >= _GROUP_TODAY_MIN_EXERCISES
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda group: (
            group["today_metrics"]["score"],
            group["today_metrics"]["freshness_days"],
            len(group["today_available_exercises"]),
        ),
    )


def _select_tomorrow_group(
    *,
    group_catalog: list[dict],
    exclude_group_id: str,
    worked_today_tissues: set[int],
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
) -> dict | None:
    best_group = None
    best_metrics = None
    for group in group_catalog:
        if group["group_id"] == exclude_group_id:
            continue
        metrics = _group_metrics(
            group["exercises"],
            tissue_last_trained=tissue_last_trained,
            tissue_rows_by_id=tissue_rows_by_id,
            days_ahead=1,
            worked_today_tissues=worked_today_tissues,
        )
        if len(group["exercises"]) < _GROUP_TODAY_MIN_EXERCISES:
            continue
        if best_group is None or (
            metrics["score"],
            metrics["freshness_days"],
            len(group["exercises"]),
        ) > (
            best_metrics["score"],
            best_metrics["freshness_days"],
            len(best_group["exercises"]),
        ):
            best_group = group
            best_metrics = metrics
    if best_group is None or best_metrics is None:
        return None
    return {
        **best_group,
        "tomorrow_metrics": best_metrics,
    }


def _select_rehab_inserts(
    *,
    exercises_data: list[dict],
    rehab_priorities: dict[int, dict],
    protection_profiles: dict[int, list[object]],
) -> list[dict]:
    candidates: list[dict] = []
    for exercise in exercises_data:
        exercise_id = exercise.get("exercise_id") or exercise.get("id")
        rehab_priority = rehab_priorities.get(int(exercise_id or 0))
        if not exercise_id or rehab_priority is None:
            continue
        preferred_side = rehab_priority.get("preferred_side")
        protection_eval = evaluate_exercise_protection(
            exercise,
            exercise,
            protection_profiles,
            preferred_side=preferred_side,
        )
        if protection_eval["blocked"]:
            continue
        suitability = min(float(exercise.get("suitability_score") or 0.0) / 100.0, 1.0)
        priority_load = min(float(rehab_priority.get("priority_load") or 0.0), 1.0)
        protection_bonus = float(protection_eval.get("score_bonus") or 0.0)
        mode = str(rehab_priority.get("mode") or "cross_support")
        selection_note = _build_selection_note(
            blocked_variant=None,
            substitute_variant=exercise.get("name"),
            gating_reason=str(protection_eval.get("gating_reason") or "") or None,
            protected_tissues=list(protection_eval.get("protected_tissues", [])),
        )
        score = (
            (0.62 if mode == "direct_rehab" else 0.45)
            + priority_load * 0.2
            + suitability * 0.12
            + protection_bonus
        )
        candidates.append({
            **exercise,
            "exercise_id": int(exercise_id),
            "exercise_name": exercise.get("exercise_name") or exercise.get("name"),
            "performed_side": preferred_side,
            "selection_mode": mode,
            "selection_score": round(score, 4),
            "protected_tissues": list(protection_eval.get("protected_tissues", [])),
            "gating_reason": protection_eval.get("gating_reason"),
            "selection_note": selection_note,
            "group_label": "Rehab",
            "workflow_role": "rehab",
        })
    candidates.sort(
        key=lambda exercise: (
            -float(exercise["selection_score"]),
            -float(exercise.get("suitability_score") or 0.0),
        ),
    )
    return candidates[:_MAX_REHAB_PRIORITY_CANDIDATES]


def _build_day_plan(
    *,
    session: Session,
    plan_date: date,
    group: dict,
    core_exercises: list[dict],
    accessory_source: list[dict],
    rehab_inserts: list[dict],
    tissues_data: list[dict],
    worked_today_tissues: set[int] | None = None,
    days_ahead: int = 0,
) -> dict:
    core_profile = combine_tissue_vectors(core_exercises) or group["profile"]
    seen_ids: set[int] = set()
    day_candidates: list[dict] = []

    def push(exercise: dict, *, selected: bool, workflow_role: str) -> None:
        exercise_id = int(exercise["exercise_id"])
        if exercise_id in seen_ids:
            return
        day_candidates.append({
            **exercise,
            "selected": selected,
            "workflow_role": workflow_role,
            "group_label": group["label"] if workflow_role != "rehab" else "Rehab",
        })
        seen_ids.add(exercise_id)

    ordered_core = sorted(
        core_exercises,
        key=lambda exercise: (
            -similarity_to_group_profile(core_profile, exercise),
            -float(exercise.get("planning_priority") or 0.0),
        ),
    )
    for exercise in ordered_core:
        push(exercise, selected=True, workflow_role="group")
    for exercise in rehab_inserts:
        push(exercise, selected=True, workflow_role="rehab")

    selected_count = len([exercise for exercise in day_candidates if exercise["selected"]])
    target_selected_count = min(
        max(selected_count, _DAY_MIN_SELECTED_EXERCISES),
        _DAY_MAX_SELECTED_EXERCISES,
    )
    accessory_pool = [
        exercise
        for exercise in accessory_source
        if int(exercise["exercise_id"]) not in seen_ids
    ]
    accessory_pool.sort(
        key=lambda exercise: (
            -similarity_to_group_profile(core_profile, exercise),
            -float(exercise.get("planning_priority") or 0.0),
            -float(exercise.get("suitability_score") or 0.0),
        ),
    )
    while selected_count < target_selected_count and accessory_pool:
        push(accessory_pool.pop(0), selected=True, workflow_role="accessory")
        selected_count += 1
    while len(day_candidates) < _DAY_MAX_CANDIDATE_EXERCISES and accessory_pool:
        push(accessory_pool.pop(0), selected=False, workflow_role="accessory")

    prescribed = _prescribe_all(
        session,
        day_candidates,
        tissues_data,
        as_of=plan_date,
    )
    metrics = (
        group.get("tomorrow_metrics")
        if days_ahead > 0
        else group.get("today_metrics") or group.get("group_metrics")
    )
    rationale = _build_day_rationale(
        group=group,
        selected_exercise_count=len([exercise for exercise in prescribed if exercise.get("selected", True)]),
        days_ahead=days_ahead,
        worked_today_tissues=worked_today_tissues or set(),
    )
    return {
        "group_id": group["group_id"],
        "day_label": group["label"],
        "readiness_score": metrics["readiness"],
        "days_since_last": metrics["freshness_days"],
        "target_regions": group["target_regions"],
        "exercise_count": len(group["exercises"]),
        "core_exercise_count": len(core_exercises),
        "exercises": prescribed,
        "selected_tissue_ids": sorted({
            tissue_id
            for exercise in day_candidates
            if exercise.get("selected", True)
            for tissue_id in _significant_tissue_ids(exercise)
        }),
        "rationale": rationale,
    }


def _build_day_rationale(
    *,
    group: dict,
    selected_exercise_count: int,
    days_ahead: int,
    worked_today_tissues: set[int],
) -> str:
    metrics = (
        group.get("tomorrow_metrics")
        if days_ahead > 0
        else group.get("today_metrics") or group.get("group_metrics")
    )
    parts = [
        f"{group['label']} is the freshest group for {'tomorrow' if days_ahead > 0 else 'today'}",
        f"({metrics['freshness_days']} weighted days since its tissues were last trained).",
        f"{selected_exercise_count} movements are preselected.",
    ]
    if days_ahead > 0 and worked_today_tissues:
        parts.append("Today's selected tissues are down-weighted in tomorrow's projection.")
    return " ".join(parts)


def _build_rehab_only_plan(
    *,
    session: Session,
    plan_date: date,
    rehab_inserts: list[dict],
    tissues_data: list[dict],
    filtered_tissues: list[dict],
) -> dict | None:
    if not rehab_inserts:
        return None
    candidates = [
        {
            **exercise,
            "selected": True,
            "workflow_role": "rehab",
            "group_label": "Rehab",
        }
        for exercise in rehab_inserts
    ]
    prescribed = _prescribe_all(session, candidates, tissues_data, as_of=plan_date)
    filtered_labels = ", ".join(row["target_label"] for row in filtered_tissues[:3])
    rationale = "Today's tissue check-in filtered out general loading."
    if filtered_labels:
        rationale += f" Rehab remains available for {filtered_labels}."
    return {
        "group_id": "rehab-only",
        "day_label": "Rehab / Recovery",
        "readiness_score": 0.0,
        "days_since_last": 0.0,
        "target_regions": [],
        "exercise_count": len(rehab_inserts),
        "core_exercise_count": 0,
        "exercises": prescribed,
        "selected_tissue_ids": sorted({
            tissue_id
            for exercise in candidates
            for tissue_id in _significant_tissue_ids(exercise)
        }),
        "rationale": rationale,
    }


def _checkin_blocking_eval(
    *,
    exercise: dict,
    filtered_tissues: list[dict],
    preferred_side: str | None = None,
) -> dict[str, object]:
    if not filtered_tissues:
        return {"blocked": False, "blocked_labels": [], "preferred_side": preferred_side}

    explicit_side = preferred_side
    if explicit_side is None:
        explicit_side = default_performed_side(
            exercise_name=str(exercise.get("exercise_name") or exercise.get("name") or ""),
            exercise_laterality=str(exercise.get("laterality") or "bilateral"),
            provided_side=None,
        )
    exercise_laterality = str(exercise.get("laterality") or "bilateral")
    candidate_sides = [explicit_side] if explicit_side in {"left", "right", "center", "bilateral"} else [None]
    if (
        preferred_side is None
        and exercise_laterality in {"unilateral", "either"}
        and explicit_side not in {"left", "right"}
    ):
        candidate_sides = ["left", "right"]

    evaluations = [
        {
            "preferred_side": candidate_side,
            "blocked_labels": _candidate_checkin_blocking_labels(
                exercise=exercise,
                filtered_tissues=filtered_tissues,
                performed_side=candidate_side,
            ),
        }
        for candidate_side in candidate_sides
    ]
    best = min(
        evaluations,
        key=lambda item: (
            1 if item["blocked_labels"] else 0,
            len(item["blocked_labels"]),
            str(item["preferred_side"] or ""),
        ),
    )
    return {
        "blocked": bool(best["blocked_labels"]),
        "blocked_labels": list(best["blocked_labels"]),
        "preferred_side": best["preferred_side"],
    }


def _candidate_checkin_blocking_labels(
    *,
    exercise: dict,
    filtered_tissues: list[dict],
    performed_side: str | None,
) -> list[str]:
    blocked_labels: list[str] = []
    for filtered in filtered_tissues:
        allowed_load = float(filtered.get("max_loading_factor") or _TODAY_BLOCKED_LOAD)
        direct_loading = _direct_loading_for_filtered_tissue(
            exercise=exercise,
            filtered_tissue=filtered,
            performed_side=performed_side,
        )
        if direct_loading > allowed_load + 1e-6:
            blocked_labels.append(str(filtered["target_label"]))
    return blocked_labels


def _direct_loading_for_filtered_tissue(
    *,
    exercise: dict,
    filtered_tissue: dict,
    performed_side: str | None,
) -> float:
    tissue_id = int(filtered_tissue.get("tissue_id") or 0)
    if tissue_id <= 0:
        return 0.0
    tracked_side = str(filtered_tissue.get("tracked_side") or "center")
    tissue_tracking_mode = "center" if tracked_side == "center" else "paired"
    exercise_laterality = str(exercise.get("laterality") or "bilateral")
    direct_loading = 0.0
    for mapping in exercise.get("tissues", []):
        if int(mapping.get("tissue_id") or 0) != tissue_id:
            continue
        load_weights, _cross_weights = tracked_tissue_side_weights(
            exercise_laterality=exercise_laterality,
            laterality_mode=str(mapping.get("laterality_mode") or "bilateral_equal"),
            performed_side=performed_side,
            tissue_tracking_mode=tissue_tracking_mode,
        )
        direct_weight = float(load_weights.get(tracked_side, 0.0))
        if direct_weight <= 0:
            continue
        mapping_load = max(
            float(mapping.get("loading_factor") or 0.0),
            float(mapping.get("routing_factor") or 0.0),
            float(mapping.get("fatigue_factor") or 0.0),
            float(mapping.get("joint_strain_factor") or 0.0),
            float(mapping.get("tendon_strain_factor") or 0.0),
        )
        direct_loading = max(direct_loading, mapping_load * direct_weight)
    return direct_loading


def _significant_tissue_ids(exercise: dict) -> set[int]:
    return set(exercise_tissue_vector(exercise).keys())


def _serialize_group_briefs(
    group_catalog: list[dict],
    *,
    today_group_id: str | None,
    tomorrow_group_id: str | None,
) -> list[dict]:
    briefs = []
    for group in group_catalog:
        planned_for = None
        if group["group_id"] == today_group_id:
            planned_for = "today"
        elif group["group_id"] == tomorrow_group_id:
            planned_for = "tomorrow"
        briefs.append({
            "group_id": group["group_id"],
            "day_label": group["label"],
            "target_regions": group["target_regions"],
            "exercise_count": len(group["exercises"]),
            "today_available_count": len(group["today_available_exercises"]),
            "days_since_last": group["today_metrics"]["freshness_days"],
            "readiness_score": group["today_metrics"]["readiness"],
            "planned_for": planned_for,
        })
    briefs.sort(
        key=lambda group: (
            0 if group["planned_for"] == "today" else 1 if group["planned_for"] == "tomorrow" else 2,
            -float(group["days_since_last"]),
            group["day_label"],
        ),
    )
    return briefs


def _strip_internal_day_fields(day_plan: dict | None) -> dict | None:
    if day_plan is None:
        return None
    public_plan = dict(day_plan)
    public_plan.pop("selected_tissue_ids", None)
    return public_plan
