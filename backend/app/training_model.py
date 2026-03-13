from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TrainingExclusionWindow,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)

_FAILURE_FACTOR = {
    "full": 1.0,
    "partial": 0.9,
    "failed": 1.05,
}
_DEFAULT_WINDOW_DAYS = 90


@dataclass
class ExposureRecord:
    date: date
    raw_load: float = 0.0
    fatigue_load: float = 0.0
    strain_load: float = 0.0
    failures: int = 0
    exercise_loads: dict[int, float] | None = None

    def __post_init__(self) -> None:
        if self.exercise_loads is None:
            self.exercise_loads = {}


@dataclass
class TissueState:
    date: date
    raw_load: float
    normalized_load: float
    capacity_state: float
    acute_fatigue: float
    chronic_load: float
    recovery_state: float
    ramp_ratio: float
    risk_7d: int
    risk_14d: int
    collapse_flag: bool
    failure_count: int
    contributors: list[str]


def build_training_model_summary(
    session: Session,
    *,
    as_of: date | None = None,
    include_exercises: bool = False,
) -> dict:
    context = _build_context(session, as_of=as_of)
    tissues = []
    for tissue in context["tissues"]:
        state = context["states_by_tissue"][tissue.id][-1] if context["states_by_tissue"][tissue.id] else None
        if state is None:
            continue
        collapse_count = sum(1 for item in context["states_by_tissue"][tissue.id] if item.collapse_flag)
        tissues.append(
            {
                "tissue": _serialize_tissue(tissue, context["configs"][tissue.id]),
                "current_capacity": round(state.capacity_state, 3),
                "normalized_load": round(state.normalized_load, 3),
                "acute_fatigue": round(state.acute_fatigue, 3),
                "chronic_load": round(state.chronic_load, 3),
                "recovery_estimate": round(state.recovery_state, 3),
                "learned_recovery_days": round(context["recovery_days"][tissue.id], 2),
                "ramp_ratio": round(state.ramp_ratio, 3),
                "risk_7d": state.risk_7d,
                "risk_14d": state.risk_14d,
                "collapse_count": collapse_count,
                "contributors": state.contributors,
                "current_condition": context["conditions"].get(tissue.id),
                "recent_collapses": [
                    item.date.isoformat()
                    for item in context["states_by_tissue"][tissue.id]
                    if item.collapse_flag
                ][-5:],
            }
        )
    tissues.sort(key=lambda item: (item["risk_7d"], item["risk_14d"]), reverse=True)

    exercise_insights = (
        build_exercise_risk_ranking(
            session,
            as_of=as_of,
            context=context,
        )
        if include_exercises
        else []
    )

    at_risk = [t for t in tissues if t["risk_7d"] >= 60]
    recovering = [t for t in tissues if t["normalized_load"] < 0.8 and t["recovery_estimate"] >= 0.75]

    return {
        "as_of": context["as_of"].isoformat(),
        "overview": {
            "at_risk_count": len(at_risk),
            "recovering_count": len(recovering),
            "tracked_tissues": len(tissues),
            "excluded_windows": [
                _serialize_exclusion_window(window) for window in context["exclusion_windows"]
            ],
        },
        "tissues": tissues,
        "exercises": exercise_insights,
    }


def build_exercise_risk_ranking(
    session: Session,
    *,
    as_of: date | None = None,
    sort_by: str = "risk_7d",
    descending: bool = True,
    limit: int | None = None,
    context: dict | None = None,
) -> list[dict]:
    context = context or _build_context(session, as_of=as_of)
    tissue_current = {
        tissue_id: states[-1]
        for tissue_id, states in context["states_by_tissue"].items()
        if states
    }
    exercise_rows: list[dict] = []
    for exercise in context["exercises"]:
        mappings = []
        risk_numerator = 0.0
        risk_denominator = 0.0
        risk14_numerator = 0.0
        normalized_numerator = 0.0
        recovering_bonus = 0.0
        max_tissue_risk = 0
        significant_max_tissue_risk = 0
        blocked_tissues: list[str] = []
        favored_tissues: list[str] = []
        for mapping in context["exercise_mappings"].get(exercise.id, []):
            tissue = context["tissue_by_id"][mapping.tissue_id]
            state = tissue_current.get(mapping.tissue_id)
            stats = context["exercise_tissue_stats"].get((exercise.id, mapping.tissue_id), {})
            factors = _mapping_factors(mapping, tissue.type)
            routing = factors["routing"]
            current_condition = context["conditions"].get(mapping.tissue_id)
            condition_status = current_condition["status"] if current_condition else None
            condition_floor_7d = _condition_risk_floor(condition_status, horizon_days=7)
            condition_floor_14d = _condition_risk_floor(condition_status, horizon_days=14)
            tissue_risk_7d = max(state.risk_7d if state else 0, condition_floor_7d)
            tissue_risk_14d = max(state.risk_14d if state else 0, condition_floor_14d)
            tissue_norm = state.normalized_load if state else 0.0
            is_significant_mapping = routing >= 0.5
            if state and state.recovery_state >= 0.75 and tissue_risk_14d < 45:
                recovering_bonus += routing
                favored_tissues.append(tissue.display_name)
            exceeds_condition_loading_limit = bool(
                current_condition
                and current_condition.get("max_loading_factor") is not None
                and (mapping.loading_factor or routing) > current_condition["max_loading_factor"]
            )
            if is_significant_mapping and (
                tissue_risk_7d >= 60
                or condition_status in {"injured", "tender"}
                or exceeds_condition_loading_limit
            ):
                blocked_tissues.append(tissue.display_name)
            max_tissue_risk = max(max_tissue_risk, tissue_risk_7d)
            if is_significant_mapping:
                significant_max_tissue_risk = max(significant_max_tissue_risk, tissue_risk_7d)
            risk_numerator += routing * tissue_risk_7d
            risk14_numerator += routing * tissue_risk_14d
            normalized_numerator += routing * tissue_norm
            risk_denominator += routing
            mappings.append(
                {
                    "tissue_id": mapping.tissue_id,
                    "tissue_name": tissue.name,
                    "tissue_display_name": tissue.display_name,
                    "tissue_type": tissue.type,
                    "routing_factor": round(routing, 4),
                    "tissue_risk_7d": tissue_risk_7d,
                    "tissue_risk_14d": tissue_risk_14d,
                    "tissue_normalized_load": round(tissue_norm, 3),
                    "recovery_state": round(state.recovery_state, 3) if state else 0.0,
                    "confidence": round(stats.get("confidence", 0.0), 3),
                    "trouble_association": round(stats.get("association", 0.0), 3),
                }
            )
        if not mappings or risk_denominator <= 0:
            continue
        weighted_risk_7d = risk_numerator / risk_denominator
        weighted_risk_14d = risk14_numerator / risk_denominator
        weighted_normalized_load = normalized_numerator / risk_denominator
        suitability = _clamp(
            100.0 - weighted_risk_7d - (max_tissue_risk * 0.2) + (recovering_bonus * 10.0),
            0.0,
            100.0,
        )
        recommendation = _recommend_exercise(
            weighted_risk_7d,
            significant_max_tissue_risk,
            blocked_tissues,
        )
        recommendation_reason, recommendation_details = _build_recommendation_reason(
            recommendation=recommendation,
            weighted_risk_7d=weighted_risk_7d,
            max_tissue_risk=significant_max_tissue_risk,
            blocked_tissues=blocked_tissues,
            favored_tissues=favored_tissues,
            weighted_normalized_load=weighted_normalized_load,
        )
        exercise_rows.append(
            {
                "id": exercise.id,
                "name": exercise.name,
                "equipment": exercise.equipment,
                "load_input_mode": exercise.load_input_mode,
                "estimated_minutes_per_set": exercise.estimated_minutes_per_set,
                "weighted_risk_7d": round(weighted_risk_7d, 2),
                "weighted_risk_14d": round(weighted_risk_14d, 2),
                "max_tissue_risk_7d": max_tissue_risk,
                "weighted_normalized_load": round(weighted_normalized_load, 3),
                "suitability_score": round(suitability, 2),
                "recommendation": recommendation,
                "recommendation_reason": recommendation_reason,
                "recommendation_details": recommendation_details,
                "blocked_tissues": blocked_tissues[:5],
                "favored_tissues": favored_tissues[:5],
                "tissues": mappings,
            }
        )
    sort_key = {
        "risk_7d": "weighted_risk_7d",
        "risk_14d": "weighted_risk_14d",
        "suitability": "suitability_score",
        "normalized_load": "weighted_normalized_load",
    }.get(sort_by, "weighted_risk_7d")
    exercise_rows.sort(key=lambda item: item[sort_key], reverse=descending)
    if limit is not None:
        exercise_rows = exercise_rows[:limit]
    return exercise_rows


def build_tissue_history(
    session: Session,
    tissue_id: int,
    *,
    as_of: date | None = None,
    days: int = _DEFAULT_WINDOW_DAYS,
) -> dict:
    context = _build_context(session, as_of=as_of)
    tissue = context["tissue_by_id"].get(tissue_id)
    if tissue is None:
        raise KeyError(f"Unknown tissue_id {tissue_id}")

    series = context["states_by_tissue"][tissue_id]
    cutoff = context["as_of"] - timedelta(days=max(1, days - 1))
    history = [
        {
            "date": state.date.isoformat(),
            "raw_load": round(state.raw_load, 3),
            "normalized_load": round(state.normalized_load, 3),
            "capacity_state": round(state.capacity_state, 3),
            "acute_fatigue": round(state.acute_fatigue, 3),
            "chronic_load": round(state.chronic_load, 3),
            "recovery_state": round(state.recovery_state, 3),
            "ramp_ratio": round(state.ramp_ratio, 3),
            "risk_7d": state.risk_7d,
            "risk_14d": state.risk_14d,
            "collapse_flag": state.collapse_flag,
            "contributors": state.contributors,
        }
        for state in series
        if state.date >= cutoff
    ]

    return {
        "tissue": _serialize_tissue(tissue, context["configs"][tissue.id]),
        "as_of": context["as_of"].isoformat(),
        "learned_recovery_days": round(context["recovery_days"][tissue.id], 2),
        "collapse_dates": [item["date"] for item in history if item["collapse_flag"]],
        "history": history,
    }


def list_exclusion_windows(session: Session) -> list[TrainingExclusionWindow]:
    return list(
        session.exec(
            select(TrainingExclusionWindow).order_by(TrainingExclusionWindow.start_date)
        ).all()
    )


def _build_context(session: Session, *, as_of: date | None) -> dict:
    tissues = list(session.exec(select(Tissue).order_by(Tissue.name)).all())
    tissue_by_id = {tissue.id: tissue for tissue in tissues}
    configs = _load_configs(session, tissues)
    exclusion_windows = list_exclusion_windows(session)
    excluded_days = _expand_excluded_days(exclusion_windows)
    exercises = list(session.exec(select(Exercise).order_by(Exercise.name)).all())
    exercise_by_id = {exercise.id: exercise for exercise in exercises}
    mappings = list(session.exec(select(ExerciseTissue)).all())
    exercise_mappings = defaultdict(list)
    for mapping in mappings:
        exercise_mappings[mapping.exercise_id].append(mapping)

    condition_rows = list(
        session.exec(
            select(TissueCondition).order_by(col(TissueCondition.updated_at).asc())
        ).all()
    )
    condition_events, current_conditions = _load_condition_events(condition_rows, as_of=as_of)
    all_dates, exposures_by_tissue, sets_by_date = _collect_daily_exposure(
        session,
        exercise_by_id,
        exercise_mappings,
        tissue_by_id,
        as_of=as_of,
        excluded_days=excluded_days,
    )
    as_of_date = as_of or (max(all_dates) if all_dates else date.today())
    if not all_dates:
        all_dates = [as_of_date]
    else:
        all_dates = _date_range(min(all_dates), as_of_date)

    recovery_days = {
        tissue.id: _learn_recovery_days(
            all_dates,
            exposures_by_tissue[tissue.id],
            excluded_days,
            configs[tissue.id].recovery_tau_days,
        )
        for tissue in tissues
    }
    collapse_dates = {
        tissue.id: _detect_collapse_dates(
            tissue.id,
            all_dates,
            exposures_by_tissue[tissue.id],
            excluded_days,
            configs[tissue.id].collapse_drop_threshold,
        )
        for tissue in tissues
    }
    states_by_tissue = {}
    exercise_tissue_stats = _build_exercise_stats(
        all_dates,
        exercises,
        mappings,
        sets_by_date,
        collapse_dates,
    )
    for tissue in tissues:
        states_by_tissue[tissue.id] = _compute_tissue_states(
            tissue=tissue,
            config=configs[tissue.id],
            all_dates=all_dates,
            exposure_by_date=exposures_by_tissue[tissue.id],
            excluded_days=excluded_days,
            condition_events=condition_events.get(tissue.id, []),
            collapse_dates=collapse_dates[tissue.id],
            recovery_days=recovery_days[tissue.id],
        )

    return {
        "as_of": as_of_date,
        "tissues": tissues,
        "tissue_by_id": tissue_by_id,
        "configs": configs,
        "conditions": current_conditions,
        "exercises": exercises,
        "exercise_mappings": exercise_mappings,
        "exercise_tissue_stats": exercise_tissue_stats,
        "states_by_tissue": states_by_tissue,
        "recovery_days": recovery_days,
        "exclusion_windows": exclusion_windows,
    }


def _collect_daily_exposure(
    session: Session,
    exercise_by_id: dict[int, Exercise],
    exercise_mappings: dict[int, list[ExerciseTissue]],
    tissue_by_id: dict[int, Tissue],
    *,
    as_of: date | None,
    excluded_days: set[date],
) -> tuple[list[date], dict[int, dict[date, ExposureRecord]], dict[date, list[dict]]]:
    weight_rows = list(
        session.exec(select(WeightLog).order_by(col(WeightLog.logged_at).asc())).all()
    )
    weights = [
        row
        for row in weight_rows
        if row.logged_at.date() <= (as_of or date.max)
    ]
    bodyweight_by_date = _bodyweight_by_date(weights)

    session_rows = list(
        session.exec(select(WorkoutSession).order_by(WorkoutSession.date, WorkoutSession.id)).all()
    )
    set_rows = list(session.exec(select(WorkoutSet)).all())
    sets_by_session: dict[int, list[WorkoutSet]] = defaultdict(list)
    for workout_set in set_rows:
        sets_by_session[workout_set.session_id].append(workout_set)

    exposures_by_tissue: dict[int, dict[date, ExposureRecord]] = defaultdict(dict)
    sets_by_date: dict[date, list[dict]] = defaultdict(list)
    seen_signatures: set[tuple] = set()
    all_dates: set[date] = set()
    for workout_session in session_rows:
        if as_of and workout_session.date > as_of:
            continue
        session_sets = sets_by_session.get(workout_session.id, [])
        informative_sets = [
            workout_set
            for workout_set in session_sets
            if workout_set.reps is not None
            or workout_set.weight is not None
            or workout_set.duration_secs is not None
            or workout_set.rpe is not None
            or workout_set.rep_completion is not None
        ]
        if not informative_sets:
            continue
        for workout_set in informative_sets:
            signature = (
                workout_session.date,
                workout_set.exercise_id,
                workout_set.set_order,
                workout_set.reps,
                workout_set.weight,
                workout_set.duration_secs,
                workout_set.rpe,
                workout_set.rep_completion,
                workout_set.notes,
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            exercise = exercise_by_id.get(workout_set.exercise_id)
            if exercise is None:
                continue
            effective_weight = _effective_weight(
                exercise,
                workout_set,
                bodyweight_by_date,
                workout_session.date,
            )
            effective_load = _effective_set_load(workout_set, effective_weight)
            if effective_load <= 0:
                continue
            all_dates.add(workout_session.date)
            failure_flag = 1 if workout_set.rep_completion == "failed" else 0
            sets_by_date[workout_session.date].append(
                {
                    "exercise_id": workout_set.exercise_id,
                    "effective_load": effective_load,
                    "failure": failure_flag,
                }
            )
            for mapping in exercise_mappings.get(workout_set.exercise_id, []):
                tissue = tissue_by_id.get(mapping.tissue_id)
                factors = _mapping_factors(mapping, tissue.type if tissue else None)
                record = exposures_by_tissue[mapping.tissue_id].setdefault(
                    workout_session.date,
                    ExposureRecord(date=workout_session.date),
                )
                routing_factor = factors["routing"]
                record.raw_load += effective_load * routing_factor
                record.fatigue_load += effective_load * factors["fatigue"]
                if exercise_by_id[workout_set.exercise_id].load_input_mode == "timed":
                    record.strain_load += effective_load * 0.5
                else:
                    record.strain_load += effective_load * max(
                        factors["joint_strain"],
                        factors["tendon_strain"],
                    )
                record.failures += failure_flag
                record.exercise_loads[workout_set.exercise_id] = (
                    record.exercise_loads.get(workout_set.exercise_id, 0.0)
                    + effective_load * routing_factor
                )

    return sorted(all_dates), exposures_by_tissue, sets_by_date


def _compute_tissue_states(
    *,
    tissue: Tissue,
    config: TissueModelConfig,
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    condition_events: list[tuple[date, int, str]],
    collapse_dates: set[date],
    recovery_days: float,
) -> list[TissueState]:
    baseline_capacity = _baseline_capacity(exposure_by_date, excluded_days, config.capacity_prior)
    fatigue_tau = max(1.0, config.fatigue_tau_days)
    chronic_tau = max(7.0, recovery_days * 6.0)
    current_capacity = baseline_capacity
    acute_fatigue = 0.0
    chronic_load = 0.0
    raw_series = [exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates]
    prefix_raw = _build_prefix_sums(raw_series)
    recent7_series = [_window_average_from_prefix(prefix_raw, index, 7) for index in range(len(all_dates))]
    recent28_series = [_window_average_from_prefix(prefix_raw, index, 28) for index in range(len(all_dates))]
    event_dates = {item[0]: max(item[1], 1) for item in condition_events}
    event_coeffs_7 = _learn_event_coefficients(
        all_dates,
        recent7_series,
        recent28_series,
        baseline_capacity,
        collapse_dates,
        event_dates,
        horizon_days=7,
    )
    event_coeffs_14 = _learn_event_coefficients(
        all_dates,
        recent7_series,
        recent28_series,
        baseline_capacity,
        collapse_dates,
        event_dates,
        horizon_days=14,
    )
    states: list[TissueState] = []
    prior_collapse_loads: list[float] = []
    active_condition_status = "healthy"
    active_condition_severity = 0
    next_condition_index = 0
    for index, current_date in enumerate(all_dates):
        while next_condition_index < len(condition_events):
            event_date, severity, status = condition_events[next_condition_index]
            if event_date > current_date:
                break
            active_condition_status = status
            active_condition_severity = severity
            next_condition_index += 1
        record = exposure_by_date.get(current_date, ExposureRecord(current_date))
        raw_load = record.raw_load
        normalized_load = raw_load / max(current_capacity, 1.0)
        acute_fatigue = _decay(acute_fatigue, fatigue_tau) + (
            record.fatigue_load / max(current_capacity, 1.0)
        )
        chronic_load = _decay(chronic_load, chronic_tau) + normalized_load
        recovery_state = 1.0 / (1.0 + acute_fatigue)
        if current_date not in excluded_days:
            current_capacity = _update_capacity_state(
                current_capacity,
                baseline_capacity,
                normalized_load,
                recovery_state,
            )
        recent_7 = recent7_series[index]
        recent_28 = recent28_series[index]
        ramp_ratio = recent_7 / max(recent_28 / 4.0, baseline_capacity * 0.15, 1.0)
        condition_severity = _condition_feature_severity(
            active_condition_status,
            active_condition_severity,
        )
        prior_event_signal = _prior_event_similarity(
            recent_7 / max(baseline_capacity, 1.0),
            baseline_capacity,
            prior_collapse_loads,
        )
        risk_7d, contributors_7d = _score_risk(
            normalized_load=recent_7 / max(current_capacity, 1.0),
            acute_fatigue=acute_fatigue,
            ramp_ratio=ramp_ratio,
            failures=record.failures,
            condition_severity=condition_severity,
            prior_event_signal=prior_event_signal,
            learned_coefficients=event_coeffs_7,
        )
        risk_14d, contributors_14d = _score_risk(
            normalized_load=(recent_28 / 4.0) / max(current_capacity, 1.0),
            acute_fatigue=acute_fatigue,
            ramp_ratio=ramp_ratio * 0.9,
            failures=record.failures,
            condition_severity=condition_severity,
            prior_event_signal=prior_event_signal,
            learned_coefficients=event_coeffs_14,
        )
        risk_7d, contributors_7d = _apply_condition_floor(
            risk=risk_7d,
            contributors=contributors_7d,
            status=active_condition_status,
            horizon_days=7,
        )
        risk_14d, contributors_14d = _apply_condition_floor(
            risk=risk_14d,
            contributors=contributors_14d,
            status=active_condition_status,
            horizon_days=14,
        )
        states.append(
            TissueState(
                date=current_date,
                raw_load=raw_load,
                normalized_load=normalized_load,
                capacity_state=current_capacity,
                acute_fatigue=acute_fatigue,
                chronic_load=chronic_load,
                recovery_state=recovery_state,
                ramp_ratio=ramp_ratio,
                risk_7d=risk_7d,
                risk_14d=risk_14d,
                collapse_flag=current_date in collapse_dates,
                failure_count=record.failures,
                contributors=_merge_contributors(contributors_7d, contributors_14d),
            )
        )
        if current_date in collapse_dates:
            prior_collapse_loads.append(recent_7)
    return states


def _load_configs(session: Session, tissues: list[Tissue]) -> dict[int, TissueModelConfig]:
    configs = {config.tissue_id: config for config in session.exec(select(TissueModelConfig)).all()}
    result = {}
    for tissue in tissues:
        result[tissue.id] = configs.get(
            tissue.id,
            TissueModelConfig(tissue_id=tissue.id),
        )
    return result


def _load_condition_events(
    rows: list[TissueCondition],
    *,
    as_of: date | None = None,
) -> tuple[dict[int, list[tuple[date, int, str]]], dict[int, dict]]:
    events: dict[int, list[tuple[date, int, str]]] = defaultdict(list)
    current: dict[int, dict] = {}
    for row in rows:
        event_date = row.updated_at.date()
        if as_of and event_date > as_of:
            continue
        events[row.tissue_id].append((event_date, row.severity, row.status))
        current[row.tissue_id] = {
            "status": row.status,
            "severity": row.severity,
            "notes": row.notes,
            "updated_at": row.updated_at.isoformat(),
            "max_loading_factor": row.max_loading_factor,
            "recovery_hours_override": row.recovery_hours_override,
        }
    return events, current


def _expand_excluded_days(
    windows: list[TrainingExclusionWindow],
) -> set[date]:
    excluded_days: set[date] = set()
    for window in windows:
        if not window.exclude_from_model:
            continue
        excluded_days.update(_date_range(window.start_date, window.end_date))
    return excluded_days


def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _effective_set_load(workout_set: WorkoutSet, effective_weight: float) -> float:
    if workout_set.reps is None:
        return 0.0
    effort_factor = 1.0
    if workout_set.rpe is not None:
        effort_factor = _clamp(1.0 + 0.05 * (workout_set.rpe - 7.0), 0.85, 1.15)
    completion_factor = _FAILURE_FACTOR.get(workout_set.rep_completion or "full", 1.0)
    return max(0.0, workout_set.reps * effective_weight * effort_factor * completion_factor)


def _effective_weight(
    exercise: Exercise,
    workout_set: WorkoutSet,
    bodyweight_by_date: dict[date, float],
    workout_date: date,
) -> float:
    external = workout_set.weight or 0.0
    bodyweight = _latest_bodyweight(bodyweight_by_date, workout_date)
    if exercise.load_input_mode == "bodyweight":
        return bodyweight * exercise.bodyweight_fraction
    if exercise.load_input_mode == "mixed":
        return external + (bodyweight * exercise.bodyweight_fraction)
    return external


def _bodyweight_by_date(weights: list[WeightLog]) -> dict[date, float]:
    result: dict[date, float] = {}
    for row in weights:
        result[row.logged_at.date()] = row.weight_lb
    return result


def _latest_bodyweight(bodyweight_by_date: dict[date, float], workout_date: date) -> float:
    available = [day for day in bodyweight_by_date if day <= workout_date]
    if not available:
        return 0.0
    return bodyweight_by_date[max(available)]


def _baseline_capacity(
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    prior: float,
) -> float:
    values = sorted(
        record.raw_load
        for day, record in exposure_by_date.items()
        if day not in excluded_days and record.raw_load > 0
    )
    if not values:
        return max(1.0, prior)
    return max(1.0, _percentile(values, 0.75))


def _learn_recovery_days(
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    seed: float,
) -> float:
    exposures = [exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates]
    prefix = _build_prefix_sums(exposures)
    rebound_days: list[int] = []
    for index, current_date in enumerate(all_dates):
        if current_date in excluded_days or index < 7 or index + 21 >= len(all_dates):
            continue
        prev_avg = _window_average_from_prefix(prefix, index - 1, 7)
        next_avg = _window_average_from_prefix(prefix, index + 7, 7)
        if prev_avg <= 0:
            continue
        if not (prev_avg * 0.35 <= next_avg <= prev_avg * 0.8):
            continue
        for rebound_index in range(index + 7, min(len(all_dates), index + 22)):
            rebound_date = all_dates[rebound_index]
            if rebound_date in excluded_days:
                continue
            rebound_avg = _window_average_from_prefix(prefix, rebound_index, 5)
            if rebound_avg >= prev_avg * 0.8:
                rebound_days.append((rebound_date - current_date).days)
                break
    if not rebound_days:
        return seed
    rebound_days.sort()
    midpoint = rebound_days[len(rebound_days) // 2]
    return round((seed + midpoint) / 2.0, 3)


def _detect_collapse_dates(
    tissue_id: int,
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    threshold: float,
) -> set[date]:
    del tissue_id
    exposures = [exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates]
    prefix = _build_prefix_sums(exposures)
    collapse_dates: set[date] = set()
    for index, current_date in enumerate(all_dates):
        history_days = min(7, index)
        future_days = min(7, len(all_dates) - index - 1)
        if current_date in excluded_days or history_days < 4 or future_days < 3:
            continue
        baseline = _window_average_from_prefix(prefix, index - 1, history_days)
        future = _window_average_from_prefix(prefix, index + future_days, future_days)
        if baseline <= 0:
            continue
        if future <= baseline * max(0.05, 1.0 - threshold):
            collapse_dates.add(current_date)
    return collapse_dates


def _learn_event_coefficients(
    all_dates: list[date],
    recent7_series: list[float],
    recent28_series: list[float],
    baseline_capacity: float,
    collapse_dates: set[date],
    event_dates: dict[date, int],
    *,
    horizon_days: int,
) -> dict[str, float]:
    event_samples = defaultdict(list)
    nonevent_samples = defaultdict(list)
    collapse_or_notes = set(collapse_dates) | set(event_dates.keys())
    prior_event_signal = 0.0
    for index, current_date in enumerate(all_dates):
        features = _feature_snapshot(
            baseline_capacity,
            recent_7=recent7_series[index],
            recent_28=recent28_series[index],
            failures=0,
            condition_severity=event_dates.get(current_date, 0),
            prior_event_signal=prior_event_signal,
        )
        event = any(
            current_date < candidate <= current_date + timedelta(days=horizon_days)
            for candidate in collapse_or_notes
        )
        target = event_samples if event else nonevent_samples
        for name, value in features.items():
            target[name].append(value)
        if current_date in collapse_or_notes:
            prior_event_signal = max(prior_event_signal, 0.35)
        else:
            prior_event_signal *= 0.98
    coefficients: dict[str, float] = {}
    for name in ("normalized_load", "acute_ratio", "ramp_ratio", "condition", "prior"):
        event_mean = _mean(event_samples[name]) if event_samples[name] else 1.0
        nonevent_mean = _mean(nonevent_samples[name]) if nonevent_samples[name] else 1.0
        coefficients[name] = round(_clamp(event_mean / max(nonevent_mean, 0.25), 0.75, 2.5), 3)
    return coefficients


def _feature_snapshot(
    baseline_capacity: float,
    *,
    recent_7: float,
    recent_28: float,
    failures: int,
    condition_severity: int,
    prior_event_signal: float,
) -> dict[str, float]:
    acute_ratio = recent_7 / max(baseline_capacity, 1.0)
    ramp_ratio = recent_7 / max(recent_28 / 4.0, baseline_capacity * 0.15, 1.0)
    return {
        "normalized_load": acute_ratio,
        "acute_ratio": acute_ratio,
        "ramp_ratio": ramp_ratio,
        "condition": float(condition_severity),
        "prior": prior_event_signal,
        "failures": float(failures),
    }


def _score_risk(
    *,
    normalized_load: float,
    acute_fatigue: float,
    ramp_ratio: float,
    failures: int,
    condition_severity: int,
    prior_event_signal: float,
    learned_coefficients: dict[str, float],
) -> tuple[int, list[str]]:
    features = {
        "normalized_load": max(0.0, normalized_load - 0.7),
        "acute_ratio": max(0.0, acute_fatigue - 0.8),
        "ramp_ratio": max(0.0, ramp_ratio - 1.0),
        "condition": condition_severity / 4.0,
        "prior": prior_event_signal,
        "failures": min(failures, 2) / 2.0,
    }
    weights = {
        "normalized_load": 0.32,
        "acute_ratio": 0.25,
        "ramp_ratio": 0.22,
        "condition": 0.12,
        "prior": 0.09,
        "failures": 0.08,
    }
    score = 0.0
    contributions = []
    for name, base_weight in weights.items():
        learned = learned_coefficients.get(name, 1.0)
        contribution = base_weight * learned * features[name]
        score += contribution
        if contribution > 0.08:
            label = {
                "normalized_load": "sustained normalized load",
                "acute_ratio": "acute fatigue",
                "ramp_ratio": "aggressive ramp",
                "condition": "recent tissue condition",
                "prior": "historical collapse proximity",
                "failures": "recent failed reps",
            }[name]
            contributions.append((contribution, label))
    risk = int(round(_clamp(100.0 / (1.0 + math.exp(-4.0 * (score - 0.45))), 0.0, 100.0)))
    contributors = [label for _, label in sorted(contributions, reverse=True)[:3]]
    return risk, contributors


def _prior_event_similarity(
    current_normalized_load: float,
    baseline_capacity: float,
    prior_collapse_loads: list[float],
) -> float:
    if not prior_collapse_loads:
        return 0.0
    target = _mean(prior_collapse_loads) / max(baseline_capacity, 1.0)
    closest = abs(current_normalized_load - target)
    return round(_clamp(1.0 - closest, 0.0, 1.0), 3)


def _condition_feature_severity(status: str, severity: int) -> int:
    if status == "injured":
        return max(severity, 4)
    if status == "tender":
        return max(severity, 2)
    if status == "rehabbing":
        return max(severity, 1)
    return 0


def _condition_risk_floor(status: str | None, *, horizon_days: int) -> int:
    if status == "injured":
        return 95 if horizon_days <= 7 else 90
    if status == "tender":
        return 78 if horizon_days <= 7 else 68
    if status == "rehabbing":
        return 58 if horizon_days <= 7 else 48
    return 0


def _apply_condition_floor(
    *,
    risk: int,
    contributors: list[str],
    status: str,
    horizon_days: int,
) -> tuple[int, list[str]]:
    floor = _condition_risk_floor(status, horizon_days=horizon_days)
    if not floor:
        return risk, contributors
    labels = contributors
    if "active tissue condition" not in labels:
        labels = ["active tissue condition", *contributors]
    return max(risk, floor), labels[:3]


def _update_capacity_state(
    current_capacity: float,
    baseline_capacity: float,
    normalized_load: float,
    recovery_state: float,
) -> float:
    drift = current_capacity + (baseline_capacity - current_capacity) * 0.04
    adaptation = baseline_capacity * max(0.0, min(normalized_load, 1.15) - 0.45) * 0.03 * recovery_state
    penalty = baseline_capacity * max(0.0, normalized_load - 1.25) * 0.035
    return max(baseline_capacity * 0.55, drift + adaptation - penalty)


def _window_average(
    values_by_date: dict[date, float],
    all_dates: list[date],
    current_date: date,
    window_days: int,
) -> float:
    start = current_date - timedelta(days=window_days - 1)
    values = [values_by_date.get(day, 0.0) for day in all_dates if start <= day <= current_date]
    if not values:
        return 0.0
    return _mean(values)


def _build_prefix_sums(values: list[float]) -> list[float]:
    prefix = [0.0]
    running = 0.0
    for value in values:
        running += value
        prefix.append(running)
    return prefix


def _window_average_from_prefix(prefix: list[float], end_index: int, window_days: int) -> float:
    if end_index < 0:
        return 0.0
    last = min(end_index + 1, len(prefix) - 1)
    first = max(0, last - window_days)
    count = last - first
    if count <= 0:
        return 0.0
    return (prefix[last] - prefix[first]) / count


def _mapping_factors(mapping: ExerciseTissue, tissue_type: str | None) -> dict[str, float]:
    defaults = _mapping_default_factors(mapping, tissue_type)
    if _looks_like_legacy_defaulted_mapping(mapping, tissue_type):
        return defaults
    return {
        "routing": mapping.routing_factor or defaults["routing"],
        "fatigue": mapping.fatigue_factor or defaults["fatigue"],
        "joint_strain": mapping.joint_strain_factor or defaults["joint_strain"],
        "tendon_strain": mapping.tendon_strain_factor or defaults["tendon_strain"],
    }


def _mapping_default_factors(mapping: ExerciseTissue, tissue_type: str | None) -> dict[str, float]:
    base = mapping.loading_factor or 1.0
    role_scale = {"primary": 1.0, "secondary": 0.65, "stabilizer": 0.35}.get(mapping.role, 0.5)
    routing = max(0.05, round(base * role_scale, 4))
    fatigue = max(0.05, round(routing * 0.9, 4))
    joint_strain = max(0.05, round(routing * 1.25, 4)) if tissue_type == "joint" else routing
    tendon_strain = max(0.05, round(routing * 1.15, 4)) if tissue_type == "tendon" else routing
    return {
        "routing": routing,
        "fatigue": fatigue,
        "joint_strain": joint_strain,
        "tendon_strain": tendon_strain,
    }


def _looks_like_legacy_defaulted_mapping(mapping: ExerciseTissue, tissue_type: str | None) -> bool:
    if not (
        mapping.routing_factor == 1.0
        and mapping.fatigue_factor == 1.0
        and mapping.joint_strain_factor == 1.0
        and mapping.tendon_strain_factor == 1.0
    ):
        return False
    if mapping.loading_factor != 1.0 or mapping.role != "primary":
        return True
    return tissue_type in {"joint", "tendon"}


def _build_exercise_stats(
    all_dates: list[date],
    exercises: list[Exercise],
    mappings: list[ExerciseTissue],
    sets_by_date: dict[date, list[dict]],
    collapse_dates: dict[int, set[date]],
) -> dict[tuple[int, int], dict[str, float]]:
    del all_dates
    exercise_pairs = {(mapping.exercise_id, mapping.tissue_id): {"sessions": 0, "event_hits": 0} for mapping in mappings}
    tissue_events = {tissue_id: set(dates) for tissue_id, dates in collapse_dates.items()}
    for workout_date, set_items in sets_by_date.items():
        exercise_ids = {item["exercise_id"] for item in set_items}
        for mapping in mappings:
            if mapping.exercise_id not in exercise_ids:
                continue
            pair = exercise_pairs[(mapping.exercise_id, mapping.tissue_id)]
            pair["sessions"] += 1
            if any(
                workout_date < event_date <= workout_date + timedelta(days=14)
                for event_date in tissue_events.get(mapping.tissue_id, set())
            ):
                pair["event_hits"] += 1
    stats: dict[tuple[int, int], dict[str, float]] = {}
    for exercise in exercises:
        del exercise
    for key, pair in exercise_pairs.items():
        sessions = max(pair["sessions"], 1)
        association = pair["event_hits"] / sessions
        stats[key] = {
            "confidence": _clamp(pair["sessions"] / 12.0, 0.0, 1.0),
            "association": _clamp(association, 0.0, 1.0),
        }
    return stats


def _serialize_tissue(tissue: Tissue, config: TissueModelConfig) -> dict:
    return {
        "id": tissue.id,
        "name": tissue.name,
        "display_name": tissue.display_name,
        "type": tissue.type,
        "recovery_hours": tissue.recovery_hours,
        "capacity_prior": config.capacity_prior,
        "recovery_tau_days": config.recovery_tau_days,
        "fatigue_tau_days": config.fatigue_tau_days,
        "collapse_drop_threshold": config.collapse_drop_threshold,
        "ramp_sensitivity": config.ramp_sensitivity,
        "risk_sensitivity": config.risk_sensitivity,
    }


def _serialize_exclusion_window(window: TrainingExclusionWindow) -> dict:
    return {
        "id": window.id,
        "start_date": window.start_date.isoformat(),
        "end_date": window.end_date.isoformat(),
        "kind": window.kind,
        "notes": window.notes,
        "exclude_from_model": window.exclude_from_model,
    }


def _recommend_exercise(
    weighted_risk_7d: float,
    max_tissue_risk: int,
    blocked_tissues: list[str],
) -> str:
    if blocked_tissues and (max_tissue_risk >= 75 or weighted_risk_7d >= 60):
        return "avoid"
    if max_tissue_risk >= 55 or weighted_risk_7d >= 40:
        return "caution"
    return "good"


def _build_recommendation_reason(
    *,
    recommendation: str,
    weighted_risk_7d: float,
    max_tissue_risk: int,
    blocked_tissues: list[str],
    favored_tissues: list[str],
    weighted_normalized_load: float,
) -> tuple[str, list[str]]:
    details: list[str] = []
    if weighted_risk_7d >= 60:
        details.append("high 7d tissue risk")
    elif weighted_risk_7d >= 40:
        details.append("moderate 7d tissue risk")
    elif weighted_risk_7d <= 25:
        details.append("low 7d tissue risk")

    if max_tissue_risk >= 75:
        details.append(f"max tissue risk {max_tissue_risk}%")
    elif max_tissue_risk >= 55:
        details.append(f"some tissues are elevated ({max_tissue_risk}% max)")

    if not blocked_tissues and weighted_normalized_load >= 1.0:
        details.append(f"normalized demand {weighted_normalized_load:.2f}x capacity")

    if favored_tissues:
        details.append(f"favours recovering tissues: {', '.join(favored_tissues[:3])}")

    if recommendation == "avoid":
        reason = (
            "Avoid because it directly loads "
            f"{', '.join(blocked_tissues[:3]) or 'high-risk tissues'} while current tissue risk is elevated."
        )
    elif recommendation == "caution":
        tissue_phrase = (
            f" and this exercise still leans on {', '.join(blocked_tissues[:2])}"
            if blocked_tissues
            else ""
        )
        reason = (
            "Use caution because recent tissue risk is elevated"
            f"{tissue_phrase}."
        )
    else:
        if favored_tissues:
            reason = (
                "Good candidate because its main tissues are recovering well"
                " and current weighted risk is low."
            )
        else:
            reason = (
                "Good candidate because current weighted tissue risk is low"
                " and no mapped tissue is in the avoid band."
            )
    return reason, details[:4]


def _merge_contributors(primary: list[str], secondary: list[str]) -> list[str]:
    result: list[str] = []
    for value in primary + secondary:
        if value not in result:
            result.append(value)
    return result[:3]


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _decay(value: float, tau_days: float) -> float:
    return value * math.exp(-1.0 / max(tau_days, 1.0))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
