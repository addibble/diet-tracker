from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, select

from app.models import (
    Exercise,
    ExerciseTissue,
    RehabCheckIn,
    Tissue,
    TrackedTissue,
    WorkoutSession,
    WorkoutSet,
    WorkoutSetTissueFeedback,
)
from app.tracked_tissues import (
    default_performed_side,
    get_active_rehab_plans_by_tracked_tissue,
    get_all_current_tracked_conditions,
    get_tracked_tissue_lookup,
    tracked_tissue_side_weights,
)

EARLY_REHAB_STAGES = {
    "calm-and-isometric",
    "protected-range",
    "tolerance-building",
    "neural-calming",
}
MID_REHAB_STAGES = {
    "rebuild-capacity",
    "controlled-dynamic",
    "activation-and-control",
    "eccentric-concentric",
}
LATE_REHAB_STAGES = {
    "return-to-heavy-slow",
    "return-to-overhead",
    "return-to-grip-load",
    "strength-rebuild",
}

_SUPPORTED_SUPPORT_STYLES = {
    "bench_supported",
    "cable_stabilized",
    "chest_supported",
    "machine",
}
_NEUTRAL_GRIP_STYLES = {"neutral"}
_PROVOCATIVE_GRIP_STYLES = {"mixed", "pronated", "supinated"}
_SYMPTOM_DIRECT_CEILINGS = {
    "none": 1.0,
    "mild": 0.50,
    "moderate": 0.10,
    "severe": 0.00,
}
_SYMPTOM_SESSION_BUDGETS = {
    "none": 999.0,
    "mild": 2.40,
    "moderate": 0.90,
    "severe": 0.15,
}
_REASON_PRIORITY = {
    "session_budget_exhausted": 5,
    "during_workout_pain_threshold": 4,
    "symptom_ceiling": 3,
    "loading_cap": 2,
    "protected_variant_required": 1,
}


@dataclass
class TrackedProtectionProfile:
    tracked_id: int
    tissue_id: int
    side: str
    display_name: str
    tissue_type: str
    tracking_mode: str
    status: str | None
    stage_id: str | None
    protocol_id: str | None
    pain_monitoring_threshold: int
    max_next_day_flare: int
    symptom_score: int
    symptom_band: str
    direct_loading_ceiling: float
    session_budget_total: float
    session_budget_used: float
    session_budget_remaining: float
    max_loading_factor: float | None
    protected_variant_only: bool
    neural_irritability: bool
    next_day_reactivity: bool
    latest_feedback_pain: int


def build_tracked_protection_profiles(
    session: Session,
    *,
    as_of: date | None = None,
) -> dict[int, list[TrackedProtectionProfile]]:
    day = as_of or date.today()
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    tracked_lookup = get_tracked_tissue_lookup(session)
    tracked_by_id = {tracked.id: tracked for tracked in tracked_lookup.values()}
    tracked_conditions = get_all_current_tracked_conditions(session)
    active_rehab_plans = get_active_rehab_plans_by_tracked_tissue(session)
    latest_check_ins = _latest_rehab_check_ins_by_tracked_tissue(session)
    budget_used, latest_feedback_pain = _today_direct_exposure_and_feedback(
        session=session,
        tracked_lookup=tracked_lookup,
        as_of=day,
    )

    profiles_by_tissue: dict[int, list[TrackedProtectionProfile]] = defaultdict(list)
    for tracked_id, tracked in tracked_by_id.items():
        tissue = tissues.get(tracked.tissue_id)
        if tissue is None:
            continue
        condition = tracked_conditions.get(tracked_id)
        rehab_plan = active_rehab_plans.get(tracked_id)
        latest_check_in = latest_check_ins.get(tracked_id)
        feedback_pain = latest_feedback_pain.get(tracked_id, 0)

        if not _needs_protection_profile(
            condition=condition,
            rehab_plan=rehab_plan,
            latest_check_in=latest_check_in,
            feedback_pain=feedback_pain,
        ):
            continue

        symptom_score = _tracked_symptom_score(
            condition=condition,
            rehab_plan=rehab_plan,
            latest_check_in=latest_check_in,
            feedback_pain=feedback_pain,
        )
        symptom_band = _symptom_band(symptom_score)
        stage_id = rehab_plan.stage_id if rehab_plan is not None else None
        symptom_band = _promote_band_for_stage(symptom_band, stage_id)
        direct_loading_ceiling = _SYMPTOM_DIRECT_CEILINGS[symptom_band]
        session_budget_total = _SYMPTOM_SESSION_BUDGETS[symptom_band]
        if symptom_band != "none" and stage_id in EARLY_REHAB_STAGES:
            direct_loading_ceiling = min(direct_loading_ceiling, 0.10)
            session_budget_total = min(session_budget_total, 0.75)
        elif symptom_band != "none" and stage_id in MID_REHAB_STAGES:
            session_budget_total = min(session_budget_total, 1.50)
        if rehab_plan is not None and rehab_plan.protocol_id == "contralateral-cross-education":
            direct_loading_ceiling = 0.0
            session_budget_total = 0.0
        if condition is not None and condition.max_loading_factor is not None:
            direct_loading_ceiling = min(direct_loading_ceiling, condition.max_loading_factor)

        pain_threshold = rehab_plan.pain_monitoring_threshold if rehab_plan is not None else 3
        next_day_limit = rehab_plan.max_next_day_flare if rehab_plan is not None else 2
        if feedback_pain >= pain_threshold > 0:
            session_budget_total = min(session_budget_total, 0.20)

        session_used = budget_used.get(tracked_id, 0.0)
        session_remaining = max(0.0, session_budget_total - session_used)
        profile = TrackedProtectionProfile(
            tracked_id=tracked_id,
            tissue_id=tracked.tissue_id,
            side=tracked.side,
            display_name=tracked.display_name,
            tissue_type=tissue.type,
            tracking_mode=tissue.tracking_mode,
            status=condition.status if condition is not None else None,
            stage_id=stage_id,
            protocol_id=rehab_plan.protocol_id if rehab_plan is not None else None,
            pain_monitoring_threshold=pain_threshold,
            max_next_day_flare=next_day_limit,
            symptom_score=symptom_score,
            symptom_band=symptom_band,
            direct_loading_ceiling=direct_loading_ceiling,
            session_budget_total=session_budget_total,
            session_budget_used=session_used,
            session_budget_remaining=session_remaining,
            max_loading_factor=condition.max_loading_factor if condition is not None else None,
            protected_variant_only=(
                symptom_band == "severe"
                or (
                    stage_id in EARLY_REHAB_STAGES
                    and symptom_band in {"moderate", "severe"}
                )
                or feedback_pain >= pain_threshold > 0
                or (
                    rehab_plan is not None
                    and rehab_plan.protocol_id == "contralateral-cross-education"
                )
            ),
            neural_irritability=_has_neural_irritability(
                rehab_plan=rehab_plan,
                latest_check_in=latest_check_in,
            ),
            next_day_reactivity=_has_next_day_reactivity(
                rehab_plan=rehab_plan,
                latest_check_in=latest_check_in,
            ),
            latest_feedback_pain=feedback_pain,
        )
        profiles_by_tissue[tracked.tissue_id].append(profile)
    return dict(profiles_by_tissue)


def evaluate_exercise_protection(
    exercise: Exercise | dict[str, Any],
    exercise_summary: dict[str, Any],
    profiles_by_tissue: dict[int, list[TrackedProtectionProfile]] | None,
    *,
    preferred_side: str | None = None,
    estimated_sets: int = 3,
) -> dict[str, Any]:
    if not profiles_by_tissue:
        return {
            "blocked": False,
            "gating_code": None,
            "gating_reason": None,
            "protected_tissues": [],
            "score_bonus": 0.0,
            "preferred_side": preferred_side,
            "side_explanation": None,
        }

    explicit_side = preferred_side
    if explicit_side is None:
        explicit_side = default_performed_side(
            exercise_name=_exercise_attr(exercise, "name", "") or "",
            exercise_laterality=_exercise_attr(exercise, "laterality", "bilateral") or "bilateral",
            provided_side=None,
        )
    exercise_laterality = _exercise_attr(exercise, "laterality", "bilateral") or "bilateral"
    candidate_sides = [explicit_side] if explicit_side in {"left", "right", "center", "bilateral"} else [None]
    if (
        preferred_side is not None
        and exercise_laterality in {"unilateral", "either"}
        and explicit_side not in {"left", "right"}
    ):
        candidate_sides = ["left", "right"]

    evaluations = [
        _evaluate_side(
            exercise=exercise,
            exercise_summary=exercise_summary,
            profiles_by_tissue=profiles_by_tissue,
            performed_side=candidate_side,
            estimated_sets=estimated_sets,
        )
        for candidate_side in candidate_sides
    ]
    if not evaluations:
        return {
            "blocked": False,
            "gating_code": None,
            "gating_reason": None,
            "protected_tissues": [],
            "score_bonus": 0.0,
            "preferred_side": preferred_side,
            "side_explanation": None,
        }
    evaluations.sort(
        key=lambda item: (
            item["blocked"],
            item["reason_priority"],
            item["worst_excess_ratio"],
            -item["score_bonus"],
        )
    )
    best = evaluations[0]
    if best["preferred_side"] in {"left", "right"} and preferred_side is None:
        protected_names = ", ".join(best["protected_tissues"][:2])
        if protected_names:
            best["side_explanation"] = (
                f"favours the {best['preferred_side']} side to reduce direct load on {protected_names}"
            )
    return best


def _evaluate_side(
    *,
    exercise: Exercise | dict[str, Any],
    exercise_summary: dict[str, Any],
    profiles_by_tissue: dict[int, list[TrackedProtectionProfile]],
    performed_side: str | None,
    estimated_sets: int,
) -> dict[str, Any]:
    blocked_findings: list[dict[str, Any]] = []
    protected_tissues: list[str] = []
    score_bonus = 0.0
    exercise_laterality = _exercise_attr(exercise, "laterality", "bilateral") or "bilateral"
    protected_variant = _is_protected_variant(exercise)
    grip_style = (_exercise_attr(exercise, "grip_style", "none") or "none").lower()
    support_style = (_exercise_attr(exercise, "support_style", "none") or "none").lower()

    for mapping in exercise_summary.get("tissues", []):
        tissue_id = mapping.get("tissue_id")
        if not tissue_id:
            continue
        profiles = profiles_by_tissue.get(int(tissue_id), [])
        if not profiles:
            continue
        laterality_mode = mapping.get("laterality_mode") or "bilateral_equal"
        for profile in profiles:
            load_weights, _cross_weights = tracked_tissue_side_weights(
                exercise_laterality=exercise_laterality,
                laterality_mode=laterality_mode,
                performed_side=performed_side,
                tissue_tracking_mode=profile.tracking_mode,
            )
            direct_weight = float(load_weights.get(profile.side, 0.0))
            if direct_weight <= 0:
                continue
            direct_loading = _mapping_channel_load(mapping, tissue_type=profile.tissue_type) * direct_weight
            candidate_cost = direct_loading * max(estimated_sets, 1)
            loading_factor = float(
                mapping.get("loading_factor")
                or mapping.get("routing_factor")
                or direct_loading
            )
            if profile.display_name not in protected_tissues:
                protected_tissues.append(profile.display_name)

            if profile.symptom_band in {"mild", "moderate", "severe"} or profile.stage_id:
                score_bonus += _protective_variant_score(
                    grip_style=grip_style,
                    support_style=support_style,
                    protected_variant=protected_variant,
                    profile=profile,
                )

            if (
                profile.latest_feedback_pain >= profile.pain_monitoring_threshold > 0
                and not protected_variant
            ):
                blocked_findings.append({
                    "code": "during_workout_pain_threshold",
                    "message": (
                        f"during-workout pain threshold already exceeded for {profile.display_name}"
                    ),
                    "protected_tissue": profile.display_name,
                })
                continue

            if (
                profile.max_loading_factor is not None
                and loading_factor > profile.max_loading_factor + 1e-6
            ):
                blocked_findings.append({
                    "code": "loading_cap",
                    "message": (
                        f"loading cap exceeded for {profile.display_name}"
                    ),
                    "protected_tissue": profile.display_name,
                })
                continue

            if profile.protected_variant_only and not protected_variant and direct_loading > 0:
                blocked_findings.append({
                    "code": "protected_variant_required",
                    "message": (
                        f"protected rehab stage requires a lower-threat variant for {profile.display_name}"
                    ),
                    "protected_tissue": profile.display_name,
                })
                continue

            allowed_ceiling = profile.direct_loading_ceiling
            if protected_variant and profile.protected_variant_only:
                allowed_ceiling = max(allowed_ceiling, 0.10)
            if direct_loading > allowed_ceiling + 1e-6:
                reason_label = "neural irritability" if profile.neural_irritability else (
                    "tendon reactivity" if profile.tissue_type == "tendon" else "symptom ceiling"
                )
                blocked_findings.append({
                    "code": "symptom_ceiling",
                    "message": (
                        f"{reason_label} exceeded direct-loading ceiling for {profile.display_name}"
                    ),
                    "protected_tissue": profile.display_name,
                    "excess_ratio": direct_loading / max(allowed_ceiling, 0.01),
                })
                continue

            if candidate_cost > profile.session_budget_remaining + 1e-6:
                blocked_findings.append({
                    "code": "session_budget_exhausted",
                    "message": (
                        f"session budget exhausted for {profile.display_name}"
                    ),
                    "protected_tissue": profile.display_name,
                    "excess_ratio": candidate_cost / max(profile.session_budget_remaining, 0.05),
                })
            elif profile.latest_feedback_pain >= profile.pain_monitoring_threshold > 0:
                score_bonus -= 0.04

    worst_finding = _worst_blocking_finding(blocked_findings)
    return {
        "blocked": worst_finding is not None,
        "gating_code": worst_finding["code"] if worst_finding else None,
        "gating_reason": worst_finding["message"] if worst_finding else None,
        "protected_tissues": protected_tissues[:5],
        "score_bonus": max(min(score_bonus, 0.20), -0.30),
        "preferred_side": performed_side,
        "side_explanation": None,
        "reason_priority": _REASON_PRIORITY.get(
            worst_finding["code"], 0
        ) if worst_finding else 0,
        "worst_excess_ratio": worst_finding.get("excess_ratio", 0.0) if worst_finding else 0.0,
    }


def _latest_rehab_check_ins_by_tracked_tissue(
    session: Session,
) -> dict[int, RehabCheckIn]:
    rows = session.exec(
        select(RehabCheckIn).order_by(RehabCheckIn.recorded_at.desc())
    ).all()
    result: dict[int, RehabCheckIn] = {}
    for row in rows:
        result.setdefault(row.tracked_tissue_id, row)
    return result


def _today_direct_exposure_and_feedback(
    *,
    session: Session,
    tracked_lookup: dict[tuple[int, str], TrackedTissue],
    as_of: date,
) -> tuple[dict[int, float], dict[int, int]]:
    exercises = {
        exercise.id: exercise
        for exercise in session.exec(select(Exercise)).all()
    }
    tissues = {
        tissue.id: tissue
        for tissue in session.exec(select(Tissue)).all()
    }
    mappings_by_exercise: dict[int, list[ExerciseTissue]] = defaultdict(list)
    for mapping in session.exec(select(ExerciseTissue)).all():
        mappings_by_exercise[mapping.exercise_id].append(mapping)

    set_rows = session.exec(
        select(WorkoutSession, WorkoutSet)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date == as_of)
    ).all()
    feedback_rows = session.exec(select(WorkoutSetTissueFeedback)).all()
    feedback_by_set: dict[int, list[WorkoutSetTissueFeedback]] = defaultdict(list)
    for row in feedback_rows:
        feedback_by_set[row.workout_set_id].append(row)

    direct_exposure: dict[int, float] = defaultdict(float)
    latest_feedback_pain: dict[int, int] = defaultdict(int)
    ordered_rows = sorted(
        set_rows,
        key=lambda item: (
            item[1].completed_at or item[1].started_at or datetime.min,
            item[1].set_order,
        ),
    )
    for workout_session, workout_set in ordered_rows:
        del workout_session
        if not _is_informative_workout_set(workout_set):
            continue
        exercise = exercises.get(workout_set.exercise_id)
        if exercise is None:
            continue
        for mapping in mappings_by_exercise.get(workout_set.exercise_id, []):
            tissue = tissues.get(mapping.tissue_id)
            if tissue is None:
                continue
            load_weights, _cross_weights = tracked_tissue_side_weights(
                exercise_laterality=exercise.laterality,
                laterality_mode=mapping.laterality_mode,
                performed_side=workout_set.performed_side,
                tissue_tracking_mode=tissue.tracking_mode,
            )
            unit_factor = _mapping_object_channel_load(mapping, tissue_type=tissue.type)
            for side, weight in load_weights.items():
                tracked = tracked_lookup.get((mapping.tissue_id, side))
                if tracked is None:
                    continue
                direct_exposure[tracked.id] += unit_factor * float(weight)
        for feedback in feedback_by_set.get(workout_set.id or 0, []):
            latest_feedback_pain[feedback.tracked_tissue_id] = max(
                latest_feedback_pain.get(feedback.tracked_tissue_id, 0),
                feedback.pain_0_10,
            )
    return dict(direct_exposure), dict(latest_feedback_pain)


def _needs_protection_profile(
    *,
    condition: Any,
    rehab_plan: Any,
    latest_check_in: RehabCheckIn | None,
    feedback_pain: int,
) -> bool:
    if rehab_plan is not None:
        return True
    if condition is not None and condition.status in {"injured", "rehabbing", "tender"}:
        return True
    if latest_check_in is not None:
        return max(
            latest_check_in.pain_0_10,
            latest_check_in.stiffness_0_10,
            latest_check_in.during_load_pain_0_10,
            latest_check_in.neural_symptoms_0_10,
            latest_check_in.next_day_flare,
        ) > 0
    return feedback_pain > 0


def _tracked_symptom_score(
    *,
    condition: Any,
    rehab_plan: Any,
    latest_check_in: RehabCheckIn | None,
    feedback_pain: int,
) -> int:
    score = _condition_anchor_score(condition)
    if latest_check_in is not None:
        score = max(
            score,
            latest_check_in.pain_0_10,
            latest_check_in.stiffness_0_10,
            latest_check_in.during_load_pain_0_10,
            latest_check_in.neural_symptoms_0_10,
        )
        if rehab_plan is not None:
            if latest_check_in.during_load_pain_0_10 > rehab_plan.pain_monitoring_threshold:
                score = max(score, max(rehab_plan.pain_monitoring_threshold + 2, 5))
            if latest_check_in.next_day_flare > rehab_plan.max_next_day_flare:
                score = max(score, max(rehab_plan.pain_monitoring_threshold + 2, 5))
    score = max(score, feedback_pain)
    return min(score, 10)


def _condition_anchor_score(condition: Any) -> int:
    if condition is None:
        return 0
    status = getattr(condition, "status", None)
    severity = int(getattr(condition, "severity", 0) or 0)
    if status == "injured":
        return 7 if severity >= 1 else 5
    if status == "tender":
        if severity >= 3:
            return 7
        if severity >= 2:
            return 5
        return 2
    return 0


def _symptom_band(score: int) -> str:
    if score <= 0:
        return "none"
    if score <= 3:
        return "mild"
    if score <= 6:
        return "moderate"
    return "severe"


def _promote_band_for_stage(band: str, stage_id: str | None) -> str:
    if band == "none":
        return band
    order = {"none": 0, "mild": 1, "moderate": 2, "severe": 3}
    floor = 0
    if stage_id in EARLY_REHAB_STAGES:
        floor = 2
    elif stage_id in MID_REHAB_STAGES:
        floor = 1
    promoted = max(order.get(band, 0), floor)
    for label, value in order.items():
        if value == promoted:
            return label
    return band


def _has_neural_irritability(*, rehab_plan: Any, latest_check_in: RehabCheckIn | None) -> bool:
    if latest_check_in is None:
        return False
    protocol_id = getattr(rehab_plan, "protocol_id", None)
    return bool(
        protocol_id == "cervical-radiculopathy-deltoid"
        and latest_check_in.neural_symptoms_0_10 >= 4
    )


def _has_next_day_reactivity(*, rehab_plan: Any, latest_check_in: RehabCheckIn | None) -> bool:
    if rehab_plan is None or latest_check_in is None:
        return False
    return latest_check_in.next_day_flare > rehab_plan.max_next_day_flare


def _is_protected_variant(exercise: Exercise | dict[str, Any]) -> bool:
    grip_style = (_exercise_attr(exercise, "grip_style", "none") or "none").lower()
    support_style = (_exercise_attr(exercise, "support_style", "none") or "none").lower()
    return (
        grip_style in _NEUTRAL_GRIP_STYLES
        or support_style in _SUPPORTED_SUPPORT_STYLES
    )


def _protective_variant_score(
    *,
    grip_style: str,
    support_style: str,
    protected_variant: bool,
    profile: TrackedProtectionProfile,
) -> float:
    score = 0.0
    if protected_variant:
        score += 0.08
    if grip_style in _PROVOCATIVE_GRIP_STYLES and profile.symptom_band in {"moderate", "severe"}:
        score -= 0.12
    elif grip_style in _PROVOCATIVE_GRIP_STYLES and profile.symptom_band == "mild":
        score -= 0.05
    if support_style in _SUPPORTED_SUPPORT_STYLES and profile.symptom_band in {"moderate", "severe"}:
        score += 0.05
    elif support_style == "unsupported" and profile.symptom_band in {"moderate", "severe"}:
        score -= 0.05
    if profile.neural_irritability and support_style == "unsupported":
        score -= 0.08
    if profile.next_day_reactivity and grip_style in _PROVOCATIVE_GRIP_STYLES:
        score -= 0.08
    return score


def _mapping_channel_load(mapping: dict[str, Any], *, tissue_type: str) -> float:
    routing = float(mapping.get("routing_factor") or mapping.get("loading_factor") or 0.0)
    if tissue_type == "tendon":
        return max(routing, float(mapping.get("tendon_strain_factor") or routing))
    if tissue_type == "joint":
        return max(routing, float(mapping.get("joint_strain_factor") or routing))
    if tissue_type in {"nerve", "neural"}:
        return max(routing, float(mapping.get("fatigue_factor") or routing))
    return routing


def _mapping_object_channel_load(mapping: ExerciseTissue, *, tissue_type: str) -> float:
    routing = float(mapping.routing_factor or mapping.loading_factor or 0.0)
    if tissue_type == "tendon":
        return max(routing, float(mapping.tendon_strain_factor or routing))
    if tissue_type == "joint":
        return max(routing, float(mapping.joint_strain_factor or routing))
    if tissue_type in {"nerve", "neural"}:
        return max(routing, float(mapping.fatigue_factor or routing))
    return routing


def _worst_blocking_finding(blocked_findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not blocked_findings:
        return None
    return max(
        blocked_findings,
        key=lambda item: (
            _REASON_PRIORITY.get(item["code"], 0),
            item.get("excess_ratio", 0.0),
        ),
    )


def _exercise_attr(exercise: Exercise | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(exercise, dict):
        return exercise.get(key, default)
    return getattr(exercise, key, default)


def _is_informative_workout_set(workout_set: WorkoutSet) -> bool:
    return any(
        value is not None
        for value in (
            workout_set.reps,
            workout_set.weight,
            workout_set.duration_secs,
            workout_set.distance_steps,
            workout_set.completed_at,
            workout_set.started_at,
            workout_set.rpe,
            workout_set.rep_completion,
        )
    )
