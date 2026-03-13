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

    exercise_insights = []
    for exercise in context["exercises"]:
        mappings = []
        for mapping in context["exercise_mappings"].get(exercise.id, []):
            stats = context["exercise_tissue_stats"].get((exercise.id, mapping.tissue_id), {})
            mappings.append(
                {
                    "tissue_id": mapping.tissue_id,
                    "tissue_name": context["tissue_by_id"][mapping.tissue_id].name,
                    "tissue_display_name": context["tissue_by_id"][mapping.tissue_id].display_name,
                    "routing_factor": round(mapping.routing_factor or mapping.loading_factor or 0.0, 4),
                    "fatigue_factor": round(mapping.fatigue_factor or 0.0, 4),
                    "joint_strain_factor": round(mapping.joint_strain_factor or 0.0, 4),
                    "tendon_strain_factor": round(mapping.tendon_strain_factor or 0.0, 4),
                    "confidence": round(stats.get("confidence", 0.0), 3),
                    "trouble_association": round(stats.get("association", 0.0), 3),
                }
            )
        if mappings:
            exercise_insights.append(
                {
                    "id": exercise.id,
                    "name": exercise.name,
                    "load_input_mode": exercise.load_input_mode,
                    "bodyweight_fraction": exercise.bodyweight_fraction,
                    "estimated_minutes_per_set": exercise.estimated_minutes_per_set,
                    "tissues": mappings,
                }
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
    condition_events, current_conditions = _load_condition_events(condition_rows)
    all_dates, exposures_by_tissue, sets_by_date = _collect_daily_exposure(
        session,
        exercise_by_id,
        exercise_mappings,
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
                record = exposures_by_tissue[mapping.tissue_id].setdefault(
                    workout_session.date,
                    ExposureRecord(date=workout_session.date),
                )
                routing_factor = mapping.routing_factor or mapping.loading_factor or 1.0
                record.raw_load += effective_load * routing_factor
                record.fatigue_load += effective_load * (mapping.fatigue_factor or routing_factor)
                if exercise_by_id[workout_set.exercise_id].load_input_mode == "timed":
                    record.strain_load += effective_load * 0.5
                else:
                    record.strain_load += effective_load * max(
                        mapping.joint_strain_factor or 0.0,
                        mapping.tendon_strain_factor or 0.0,
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
    rolling_raw: dict[date, float] = {day: exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates}
    event_dates = {item[0]: max(item[1], 1) for item in condition_events}
    event_coeffs_7 = _learn_event_coefficients(
        all_dates,
        rolling_raw,
        baseline_capacity,
        collapse_dates,
        event_dates,
        horizon_days=7,
    )
    event_coeffs_14 = _learn_event_coefficients(
        all_dates,
        rolling_raw,
        baseline_capacity,
        collapse_dates,
        event_dates,
        horizon_days=14,
    )
    states: list[TissueState] = []
    for current_date in all_dates:
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
        recent_7 = _window_average(rolling_raw, all_dates, current_date, 7)
        recent_28 = _window_average(rolling_raw, all_dates, current_date, 28)
        ramp_ratio = recent_7 / max(recent_28 / 4.0, baseline_capacity * 0.15, 1.0)
        condition_severity = event_dates.get(current_date, 0)
        prior_event_signal = _prior_event_similarity(
            recent_7 / max(baseline_capacity, 1.0),
            baseline_capacity,
            rolling_raw,
            collapse_dates,
            all_dates,
            current_date,
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
) -> tuple[dict[int, list[tuple[date, int, str]]], dict[int, dict]]:
    events: dict[int, list[tuple[date, int, str]]] = defaultdict(list)
    current: dict[int, dict] = {}
    for row in rows:
        event_date = row.updated_at.date()
        events[row.tissue_id].append((event_date, row.severity, row.status))
        current[row.tissue_id] = {
            "status": row.status,
            "severity": row.severity,
            "notes": row.notes,
            "updated_at": row.updated_at.isoformat(),
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
    exposures = {day: exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates}
    rebound_days: list[int] = []
    for index, current_date in enumerate(all_dates):
        if current_date in excluded_days or index < 7 or index + 21 >= len(all_dates):
            continue
        prev_avg = _window_average(exposures, all_dates, current_date - timedelta(days=1), 7)
        next_avg = _window_average(exposures, all_dates, current_date + timedelta(days=7), 7)
        if prev_avg <= 0:
            continue
        if not (prev_avg * 0.35 <= next_avg <= prev_avg * 0.8):
            continue
        for rebound_index in range(index + 7, min(len(all_dates), index + 22)):
            rebound_date = all_dates[rebound_index]
            if rebound_date in excluded_days:
                continue
            rebound_avg = _window_average(exposures, all_dates, rebound_date, 5)
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
    exposures = {day: exposure_by_date.get(day, ExposureRecord(day)).raw_load for day in all_dates}
    collapse_dates: set[date] = set()
    for index, current_date in enumerate(all_dates):
        history_days = min(7, index)
        future_days = min(7, len(all_dates) - index - 1)
        if current_date in excluded_days or history_days < 4 or future_days < 3:
            continue
        baseline = _window_average(
            exposures,
            all_dates,
            current_date - timedelta(days=1),
            history_days,
        )
        future = _window_average(
            exposures,
            all_dates,
            current_date + timedelta(days=future_days),
            future_days,
        )
        if baseline <= 0:
            continue
        if future <= baseline * max(0.05, 1.0 - threshold):
            collapse_dates.add(current_date)
    return collapse_dates


def _learn_event_coefficients(
    all_dates: list[date],
    raw_exposures: dict[date, float],
    baseline_capacity: float,
    collapse_dates: set[date],
    event_dates: dict[date, int],
    *,
    horizon_days: int,
) -> dict[str, float]:
    event_samples = defaultdict(list)
    nonevent_samples = defaultdict(list)
    collapse_or_notes = set(collapse_dates) | set(event_dates.keys())
    for current_date in all_dates:
        features = _feature_snapshot(
            current_date,
            raw_exposures,
            all_dates,
            baseline_capacity,
            failures=0,
            condition_severity=event_dates.get(current_date, 0),
            prior_event_signal=0.0,
        )
        event = any(
            current_date < candidate <= current_date + timedelta(days=horizon_days)
            for candidate in collapse_or_notes
        )
        target = event_samples if event else nonevent_samples
        for name, value in features.items():
            target[name].append(value)
    coefficients: dict[str, float] = {}
    for name in ("normalized_load", "acute_ratio", "ramp_ratio", "condition", "prior"):
        event_mean = _mean(event_samples[name]) if event_samples[name] else 1.0
        nonevent_mean = _mean(nonevent_samples[name]) if nonevent_samples[name] else 1.0
        coefficients[name] = round(_clamp(event_mean / max(nonevent_mean, 0.25), 0.75, 2.5), 3)
    return coefficients


def _feature_snapshot(
    current_date: date,
    raw_exposures: dict[date, float],
    all_dates: list[date],
    baseline_capacity: float,
    *,
    failures: int,
    condition_severity: int,
    prior_event_signal: float,
) -> dict[str, float]:
    recent_7 = _window_average(raw_exposures, all_dates, current_date, 7)
    recent_28 = _window_average(raw_exposures, all_dates, current_date, 28)
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
    raw_exposures: dict[date, float],
    collapse_dates: set[date],
    all_dates: list[date],
    current_date: date,
) -> float:
    del baseline_capacity
    prior_loads = []
    for collapse_date in collapse_dates:
        if collapse_date >= current_date:
            continue
        prior_loads.append(_window_average(raw_exposures, all_dates, collapse_date, 7))
    if not prior_loads:
        return 0.0
    closest = min(abs(current_normalized_load - (load / max(_mean(prior_loads), 1.0))) for load in prior_loads)
    return round(_clamp(1.0 - closest, 0.0, 1.0), 3)


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
