from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.exercise_protection import build_tracked_protection_profiles, evaluate_exercise_protection
from app.models import ExerciseTissue, RecoveryCheckIn, WorkoutSession, WorkoutSet
from app.planner import (
    _MAX_REHAB_PRIORITY_CANDIDATES,
    _build_exercise_region_map,
    _build_rehab_priority_map,
    _build_selection_note,
    _prescribe_all,
)
from app.planner_groups import (
    build_similarity_groups,
    combine_tissue_vectors,
    exercise_tissue_vector,
    similarity_to_group_profile,
)
from app.recovery_check_ins import recovery_checkin_has_symptoms
from app.tracked_tissues import (
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_tracked_tissue_lookup,
)
from app.training_model import build_training_model_summary

_DEFAULT_TISSUE_FRESHNESS_DAYS = 21
_GROUP_TODAY_MIN_EXERCISES = 2
_DAY_MIN_SELECTED_EXERCISES = 7
_DAY_TARGET_SELECTED_EXERCISES = 8
_DAY_MAX_SELECTED_EXERCISES = 10
_DAY_MAX_CANDIDATE_EXERCISES = 12
_TODAY_BLOCKED_LOAD = 0.3


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
    blocked_tissue_ids = {row["tissue_id"] for row in filtered_tissues}
    tissue_last_trained = _tissue_last_trained(session, today)

    all_pool = _build_grouping_pool(
        exercises_data=exercises_data,
        exercise_region_map=exercise_region_map,
        tissue_last_trained=tissue_last_trained,
        tissue_rows_by_id=tissue_rows_by_id,
        protection_profiles=protection_profiles,
    )
    if not all_pool:
        return {
            "as_of": today.isoformat(),
            "today_plan": None,
            "tomorrow_plan": None,
            "groups": [],
            "filtered_tissues": filtered_tissues,
            "message": "No eligible exercises are available for planning right now.",
        }

    groups = build_similarity_groups(
        all_pool,
        priorities=[float(exercise["planning_priority"]) for exercise in all_pool],
    )
    group_catalog = _build_group_catalog(
        groups=groups,
        exercise_region_map=exercise_region_map,
        blocked_tissue_ids=blocked_tissue_ids,
        tissue_last_trained=tissue_last_trained,
        tissue_rows_by_id=tissue_rows_by_id,
    )
    today_group = _select_today_group(group_catalog)
    rehab_inserts = _select_rehab_inserts(
        exercises_data=exercises_data,
        rehab_priorities=rehab_priorities,
        protection_profiles=protection_profiles,
    )
    if today_group is None:
        rehab_only_plan = _build_rehab_only_plan(
            session=session,
            plan_date=today,
            rehab_inserts=rehab_inserts,
            tissues_data=tissues_data,
            filtered_tissues=filtered_tissues,
        )
        return {
            "as_of": today.isoformat(),
            "today_plan": _strip_internal_day_fields(rehab_only_plan),
            "tomorrow_plan": None,
            "groups": _serialize_group_briefs(group_catalog, today_group_id=None, tomorrow_group_id=None),
            "filtered_tissues": filtered_tissues,
            "message": (
                None
                if rehab_only_plan is not None
                else "Today's tissue check-in filtered out every general training group."
            ),
        }

    today_accessory_source = [
        exercise
        for exercise in all_pool
        if not (_significant_tissue_ids(exercise) & blocked_tissue_ids)
    ]
    today_plan = _build_day_plan(
        session=session,
        plan_date=today,
        group=today_group,
        core_exercises=today_group["today_available_exercises"],
        accessory_source=today_accessory_source,
        rehab_inserts=rehab_inserts,
        tissues_data=tissues_data,
    )
    worked_today_tissues = set(today_plan.get("selected_tissue_ids", [])) if today_plan else set()
    tomorrow_group = _select_tomorrow_group(
        group_catalog=group_catalog,
        exclude_group_id=today_group["group_id"],
        worked_today_tissues=worked_today_tissues,
        tissue_last_trained=tissue_last_trained,
        tissue_rows_by_id=tissue_rows_by_id,
    )
    tomorrow_plan = None
    if tomorrow_group is not None:
        tomorrow_plan = _build_day_plan(
            session=session,
            plan_date=today + timedelta(days=1),
            group=tomorrow_group,
            core_exercises=tomorrow_group["exercises"],
            accessory_source=all_pool,
            rehab_inserts=rehab_inserts,
            tissues_data=tissues_data,
            worked_today_tissues=worked_today_tissues,
            days_ahead=1,
        )

    return {
        "as_of": today.isoformat(),
        "today_plan": _strip_internal_day_fields(today_plan),
        "tomorrow_plan": _strip_internal_day_fields(tomorrow_plan),
        "groups": _serialize_group_briefs(
            group_catalog,
            today_group_id=today_group["group_id"],
            tomorrow_group_id=tomorrow_group["group_id"] if tomorrow_group else None,
        ),
        "filtered_tissues": filtered_tissues,
        "message": None,
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
            "target_label": getattr(tracked, "display_name", str(tracked_tissue_id)),
            "status": status,
            "reason": _checkin_reason_label(row),
        })
        seen_tracked_ids.add(tracked_tissue_id)
    return filtered


def _checkin_blocks_general_loading(row: RecoveryCheckIn) -> bool:
    return recovery_checkin_has_symptoms(row) or row.readiness_0_10 <= 7


def _checkin_reason_label(row: RecoveryCheckIn) -> str:
    if row.pain_0_10 > 0:
        return f"pain {row.pain_0_10}/10"
    if row.soreness_0_10 > 0:
        return f"soreness {row.soreness_0_10}/10"
    if row.stiffness_0_10 > 0:
        return f"stiffness {row.stiffness_0_10}/10"
    return f"readiness {row.readiness_0_10}/10"


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


def _build_grouping_pool(
    *,
    exercises_data: list[dict],
    exercise_region_map: dict[int, list[dict]],
    tissue_last_trained: dict[int, int],
    tissue_rows_by_id: dict[int, dict],
    protection_profiles: dict[int, list[object]],
) -> list[dict]:
    pool: list[dict] = []
    for exercise in exercises_data:
        exercise_id = exercise.get("exercise_id") or exercise.get("id")
        if not exercise_id or exercise.get("recommendation") == "avoid":
            continue
        if not exercise_region_map.get(exercise_id):
            continue
        if not exercise_tissue_vector(exercise):
            continue
        protection_eval = evaluate_exercise_protection(exercise, exercise, protection_profiles)
        if protection_eval["blocked"]:
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
    if region_set & {"quads", "glutes", "hips"} and "hamstrings" not in region_set:
        return "Leg Push"
    if region_set & {"hamstrings", "tibs", "calves", "lower_back"}:
        return "Leg Pull"
    if region_set & {"core", "lower_back", "hips"}:
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
