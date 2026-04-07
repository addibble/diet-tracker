from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.exercise_loads import (
    bodyweight_by_date,
    effective_set_load,
    effective_weight,
    supports_strength_estimate,
)
from app.exercise_protection import (
    build_tracked_protection_profiles,
    evaluate_exercise_protection,
)
from app.models import (
    Exercise,
    ExerciseTissue,
    ProgramDay,
    ProgramDayExercise,
    RecoveryCheckIn,
    RegionSorenessCheckIn,
    Tissue,
    TissueCondition,
    TissueModelConfig,
    TissueRegionLink,
    TrackedTissue,
    TrainingExclusionWindow,
    TrainingProgram,
    WeightLog,
    WorkoutSession,
    WorkoutSet,
)
from app.tissue_regions import canonicalize_region, load_tissue_regions

_DEFAULT_WINDOW_DAYS = 90


@dataclass
class ExposureRecord:
    date: date
    raw_load: float = 0.0
    fatigue_load: float = 0.0
    strain_load: float = 0.0
    strength_load: float = 0.0   # from 1-5 rep sets
    hypertrophy_load: float = 0.0  # from 6-12 rep sets
    endurance_load: float = 0.0  # from 13+ rep sets
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
    fatigue_input: float = 0.0
    current_soreness: int = 0


@dataclass
class RecoveryLearningResult:
    learned_recovery_days: float
    volume_rebound: float
    subjective_days: float | None


def build_training_model_summary(
    session: Session,
    *,
    as_of: date | None = None,
    include_exercises: bool = False,
) -> dict:
    context = _build_context(session, as_of=as_of)

    # Load region links for all tissues
    region_links = list(session.exec(select(TissueRegionLink)).all())
    regions_by_tissue: dict[int, list[str]] = defaultdict(list)
    for link in region_links:
        regions_by_tissue[link.tissue_id].append(link.region)

    tissues = []
    for tissue in context["tissues"]:
        state = context["states_by_tissue"][tissue.id][-1] if context["states_by_tissue"][tissue.id] else None
        if state is None:
            continue
        collapse_count = sum(1 for item in context["states_by_tissue"][tissue.id] if item.collapse_flag)
        # Capacity trend over last 30 days
        states_list = context["states_by_tissue"][tissue.id]
        capacity_trend = 0.0
        if len(states_list) >= 2:
            recent_cap = states_list[-1].capacity_state
            days_ago = min(30, len(states_list) - 1)
            earlier_cap = states_list[-1 - days_ago].capacity_state
            if earlier_cap > 0:
                capacity_trend = round(((recent_cap - earlier_cap) / earlier_cap) * 100, 2)
        # Baseline capacity
        baseline = _baseline_capacity(
            context["exposures_by_tissue"][tissue.id],
            context["excluded_days"],
            context["configs"][tissue.id].capacity_prior,
            prefer_strain=tissue.type in {"joint", "tendon"},
        )
        # Last trained date: last date with non-zero load
        last_trained_date = None
        exposure_data = context["exposures_by_tissue"][tissue.id]
        for d in reversed(context["all_dates"]):
            rec = exposure_data.get(d)
            if rec and (rec.raw_load > 0 or rec.strain_load > 0):
                last_trained_date = d.isoformat()
                break
        # Recovery learning intermediates
        rl = context["recovery_learning"][tissue.id]
        # Overworked status from risk thresholds
        risk = state.risk_7d
        if risk >= 75:
            overworked = "avoid"
        elif risk >= 55:
            overworked = "caution"
        else:
            overworked = "good"
        # Region data
        tissue_regions = regions_by_tissue.get(tissue.id, [])
        if not tissue_regions and tissue.region:
            tissue_regions = [tissue.region]
        tissues.append(
            {
                "tissue": _serialize_tissue(tissue, context["configs"][tissue.id]),
                "current_capacity": round(state.capacity_state, 3),
                "baseline_capacity": round(baseline, 3),
                "capacity_trend_30d_pct": capacity_trend,
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
                # New fields for model transparency
                "fatigue_input": round(state.fatigue_input, 3),
                "current_soreness": state.current_soreness,
                "volume_rebound": round(rl.volume_rebound, 3),
                "subjective_days": round(rl.subjective_days, 3) if rl.subjective_days is not None else None,
                "overworked": overworked,
                "tissue_region": tissue.region,
                "tissue_regions": tissue_regions,
                "last_trained_date": last_trained_date,
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
    protection_profiles = build_tracked_protection_profiles(
        session,
        as_of=context["as_of"],
    )
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
            mapping_load = max(
                routing,
                factors["joint_strain"],
                factors["tendon_strain"],
                float(mapping.loading_factor or 0.0),
            )
            current_condition = context["conditions"].get(mapping.tissue_id)
            condition_status = current_condition["status"] if current_condition else None
            condition_floor_7d = _condition_risk_floor_for_mapping(
                condition_status,
                horizon_days=7,
                mapping_load=mapping_load,
                max_loading_factor=(
                    current_condition.get("max_loading_factor")
                    if current_condition
                    else None
                ),
            )
            condition_floor_14d = _condition_risk_floor_for_mapping(
                condition_status,
                horizon_days=14,
                mapping_load=mapping_load,
                max_loading_factor=(
                    current_condition.get("max_loading_factor")
                    if current_condition
                    else None
                ),
            )
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
                and mapping_load > current_condition["max_loading_factor"]
            )
            if is_significant_mapping and (
                tissue_risk_7d >= 60
                or (
                    condition_status == "injured"
                    and mapping_load >= 0.2
                )
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
                    "loading_factor": round(float(mapping.loading_factor or 0.0), 4),
                    "routing_factor": round(routing, 4),
                    "fatigue_factor": round(factors["fatigue"], 4),
                    "joint_strain_factor": round(factors["joint_strain"], 4),
                    "tendon_strain_factor": round(factors["tendon_strain"], 4),
                    "laterality_mode": mapping.laterality_mode,
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
        protection_eval = evaluate_exercise_protection(
            exercise,
            {"tissues": mappings},
            protection_profiles,
        )
        suitability = _clamp(
            100.0
            - weighted_risk_7d
            - (max_tissue_risk * 0.2)
            + (recovering_bonus * 10.0)
            + (float(protection_eval.get("score_bonus") or 0.0) * 100.0),
            0.0,
            100.0,
        )
        blocked_tissues = list(dict.fromkeys(blocked_tissues))
        favored_tissues = list(dict.fromkeys(favored_tissues))
        if protection_eval["blocked"]:
            for tissue_name in protection_eval["protected_tissues"]:
                if tissue_name not in blocked_tissues:
                    blocked_tissues.append(tissue_name)
        recommendation = _recommend_exercise(
            weighted_risk_7d,
            significant_max_tissue_risk,
            blocked_tissues,
            protection_blocked=bool(protection_eval["blocked"]),
        )
        recommendation_reason, recommendation_details = _build_recommendation_reason(
            recommendation=recommendation,
            weighted_risk_7d=weighted_risk_7d,
            max_tissue_risk=significant_max_tissue_risk,
            blocked_tissues=blocked_tissues,
            favored_tissues=favored_tissues,
            weighted_normalized_load=weighted_normalized_load,
        )
        if protection_eval["blocked"] and protection_eval["gating_reason"]:
            recommendation_details = [
                str(protection_eval["gating_reason"]),
                *recommendation_details,
            ][:4]
        elif (
            protection_eval["protected_tissues"]
            and float(protection_eval.get("score_bonus") or 0.0) > 0
        ):
            recommendation_details = [
                "safer variant for "
                + ", ".join(protection_eval["protected_tissues"][:2]),
                *recommendation_details,
            ][:4]
        # e1RM summary for this exercise
        e1rm_series = context.get("e1rm_by_exercise", {}).get(exercise.id, [])
        current_e1rm = round(e1rm_series[-1][1], 2) if e1rm_series else None
        peak_e1rm = round(max((v for _, v in e1rm_series), default=0.0), 2) if e1rm_series else None
        exercise_rows.append(
            {
                "id": exercise.id,
                "name": exercise.name,
                "equipment": exercise.equipment,
                "load_input_mode": exercise.load_input_mode,
                "laterality": exercise.laterality,
                "bodyweight_fraction": exercise.bodyweight_fraction,
                "external_load_multiplier": exercise.external_load_multiplier,
                "variant_group": exercise.variant_group,
                "grip_style": exercise.grip_style,
                "grip_width": exercise.grip_width,
                "support_style": exercise.support_style,
                "set_metric_mode": exercise.set_metric_mode,
                "estimated_minutes_per_set": exercise.estimated_minutes_per_set,
                "in_active_program": exercise.id in context.get(
                    "active_program_exercise_ids", set()
                ),
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
                "current_e1rm": current_e1rm,
                "peak_e1rm": peak_e1rm,
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
            "fatigue_input": round(state.fatigue_input, 3),
            "current_soreness": state.current_soreness,
        }
        for state in series
        if state.date >= cutoff
    ]

    # Compute baseline capacity for this tissue
    baseline = _baseline_capacity(
        context["exposures_by_tissue"][tissue_id],
        context["excluded_days"],
        context["configs"][tissue_id].capacity_prior,
        prefer_strain=tissue.type in {"joint", "tendon"},
    )

    # Detect overload windows (days where ramp_ratio > 1.3)
    overload_dates = [
        item["date"] for item in history if item["ramp_ratio"] > 1.3
    ]

    # Capacity trend: % change over last 30 days
    capacity_values = [item["capacity_state"] for item in history]
    capacity_trend = 0.0
    if len(capacity_values) >= 2:
        recent = capacity_values[-1]
        days_ago = min(30, len(capacity_values) - 1)
        earlier = capacity_values[-1 - days_ago]
        if earlier > 0:
            capacity_trend = round(((recent - earlier) / earlier) * 100, 2)

    return {
        "tissue": _serialize_tissue(tissue, context["configs"][tissue.id]),
        "as_of": context["as_of"].isoformat(),
        "learned_recovery_days": round(context["recovery_days"][tissue.id], 2),
        "baseline_capacity": round(baseline, 3),
        "capacity_trend_30d_pct": capacity_trend,
        "collapse_dates": [item["date"] for item in history if item["collapse_flag"]],
        "overload_dates": overload_dates,
        "history": history,
    }


def build_exercise_strength(
    session: Session,
    exercise_id: int,
    *,
    as_of: date | None = None,
    days: int = _DEFAULT_WINDOW_DAYS,
) -> dict:
    """Return e1RM time series and trend for a single exercise.

    This is a lightweight query that only reads workout sets — it does NOT
    rebuild the full training model context, so it returns quickly.
    """
    exercise = session.get(Exercise, exercise_id)
    if exercise is None:
        raise KeyError(f"Unknown exercise_id {exercise_id}")

    as_of_date = as_of or date.today()
    cutoff = as_of_date - timedelta(days=max(1, days - 1))

    # Load bodyweight data for bodyweight/mixed exercises
    weight_rows = list(
        session.exec(select(WeightLog).order_by(col(WeightLog.logged_at).asc())).all()
    )
    bw_by_date = bodyweight_by_date(
        [r for r in weight_rows if r.logged_at.date() <= as_of_date]
    )

    # Query sets for this exercise only
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id)
        .where(WorkoutSession.date >= cutoff)
        .where(WorkoutSession.date <= as_of_date)
        .where(WorkoutSet.reps.isnot(None))  # type: ignore[union-attr]
        .order_by(WorkoutSession.date)
    )
    rows = session.exec(stmt).all()

    # Compute best e1RM per day
    daily_best: dict[date, float] = {}
    for workout_set, workout_date in rows:
        ew = effective_weight(exercise, workout_set, bw_by_date, workout_date)
        if ew <= 0 or not supports_strength_estimate(exercise, workout_set):
            continue
        e1rm = _estimated_1rm(ew, workout_set.reps)
        if e1rm > daily_best.get(workout_date, 0.0):
            daily_best[workout_date] = e1rm

    e1rm_series = sorted(daily_best.items())
    filtered = [(d.isoformat(), v) for d, v in e1rm_series]

    # Trend: compare last 14 days avg vs prior 14 days avg
    recent_values = [v for d, v in e1rm_series if d >= as_of_date - timedelta(days=14)]
    prior_values = [
        v for d, v in e1rm_series
        if as_of_date - timedelta(days=28) <= d < as_of_date - timedelta(days=14)
    ]
    trend = "stable"
    trend_pct = 0.0
    if recent_values and prior_values:
        recent_avg = _mean(recent_values)
        prior_avg = _mean(prior_values)
        if prior_avg > 0:
            trend_pct = round(((recent_avg - prior_avg) / prior_avg) * 100, 2)
            if trend_pct > 2:
                trend = "rising"
            elif trend_pct < -2:
                trend = "falling"
    peak = max((v for _, v in e1rm_series), default=0.0)
    current = e1rm_series[-1][1] if e1rm_series else 0.0
    return {
        "exercise_id": exercise_id,
        "exercise_name": exercise.name,
        "as_of": as_of_date.isoformat(),
        "current_e1rm": round(current, 2),
        "peak_e1rm": round(peak, 2),
        "trend": trend,
        "trend_pct": trend_pct,
        "history": [{"date": d, "e1rm": round(v, 2)} for d, v in filtered],
    }


def list_exclusion_windows(session: Session) -> list[TrainingExclusionWindow]:
    return list(
        session.exec(
            select(TrainingExclusionWindow).order_by(TrainingExclusionWindow.start_date)
        ).all()
    )


def _load_recovery_checkins(
    session: Session,
    tissues: list[Tissue],
    exposures_by_tissue: dict[int, dict[date, ExposureRecord]],
    as_of: date | None,
) -> dict[int, dict[date, dict]]:
    """Load soreness observations and distribute regional signals across tissues."""
    stmt = select(RecoveryCheckIn).order_by(col(RecoveryCheckIn.date).asc())
    if as_of is not None:
        stmt = stmt.where(col(RecoveryCheckIn.date) <= as_of)
    legacy_rows = list(session.exec(stmt).all())
    soreness_stmt = select(RegionSorenessCheckIn).order_by(col(RegionSorenessCheckIn.date).asc())
    if as_of is not None:
        soreness_stmt = soreness_stmt.where(col(RegionSorenessCheckIn.date) <= as_of)
    soreness_rows = list(session.exec(soreness_stmt).all())

    if not legacy_rows and not soreness_rows:
        return {}

    # Build region -> [tissue] mapping from canonical recovery-region associations.
    region_tissues: dict[str, list[Tissue]] = defaultdict(list)
    regions_by_tissue = load_tissue_regions(session, tissues=tissues)
    for tissue in tissues:
        for region in regions_by_tissue.get(tissue.id, ()):
            region_tissues[region].append(tissue)
    tracked_to_tissue = {
        tracked.id: tracked.tissue_id
        for tracked in session.exec(select(TrackedTissue)).all()
    }

    result: dict[int, dict[date, dict]] = defaultdict(dict)

    def set_soreness_signal(*, tissue_id: int, checkin_date: date, soreness: int) -> None:
        current = result[tissue_id].get(checkin_date)
        if current is None or soreness > current["soreness"]:
            result[tissue_id][checkin_date] = {"soreness": soreness}

    def distribute_region_signal(*, checkin_date: date, region: str, soreness: int) -> None:
        target_tissues = region_tissues.get(region, [])
        if not target_tissues:
            return

        # Weight by recent exercise exposure (last 7 days)
        tissue_weights: dict[int, float] = {}
        for tissue in target_tissues:
            exposure_map = exposures_by_tissue.get(tissue.id, {})
            recent_load = 0.0
            for day_offset in range(7):
                check_date = checkin_date - timedelta(days=day_offset)
                rec = exposure_map.get(check_date)
                if rec:
                    recent_load += max(rec.raw_load, rec.strain_load)
            tissue_weights[tissue.id] = recent_load

        total_weight = sum(tissue_weights.values())
        if total_weight <= 0:
            # Equal distribution when no recent exposure
            for tissue in target_tissues:
                set_soreness_signal(
                    tissue_id=tissue.id,
                    checkin_date=checkin_date,
                    soreness=soreness,
                )
            return

        # Weighted distribution: tissues with more recent load get more signal
        for tissue in target_tissues:
            weight_fraction = tissue_weights[tissue.id] / total_weight
            weighted_soreness = (
                round(soreness * weight_fraction * len(target_tissues))
                if weight_fraction > 0
                else 0
            )
            set_soreness_signal(
                tissue_id=tissue.id,
                checkin_date=checkin_date,
                soreness=weighted_soreness,
            )

    for row in soreness_rows:
        region = canonicalize_region(row.region) or row.region
        distribute_region_signal(
            checkin_date=row.date,
            region=region,
            soreness=row.soreness_0_10,
        )

    for row in legacy_rows:
        uses_legacy_soreness_signal = row.soreness_0_10 > 0
        if not uses_legacy_soreness_signal:
            continue
        if row.tracked_tissue_id is not None:
            tissue_id = tracked_to_tissue.get(row.tracked_tissue_id)
            if tissue_id is None:
                continue
            set_soreness_signal(
                tissue_id=tissue_id,
                checkin_date=row.date,
                soreness=row.soreness_0_10,
            )
            continue
        region = canonicalize_region(row.region) or row.region
        distribute_region_signal(
            checkin_date=row.date,
            region=region,
            soreness=row.soreness_0_10,
        )

    return dict(result)


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
    all_dates, exposures_by_tissue, sets_by_date, e1rm_by_exercise = _collect_daily_exposure(
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

    checkin_data = _load_recovery_checkins(
        session, tissues, exposures_by_tissue, as_of=as_of,
    )

    recovery_learning = {
        tissue.id: _learn_recovery_days(
            all_dates,
            exposures_by_tissue[tissue.id],
            excluded_days,
            _recovery_seed_days(tissue, configs[tissue.id], current_conditions.get(tissue.id)),
            prefer_strain=tissue.type in {"joint", "tendon"},
            checkin_data=checkin_data.get(tissue.id),
        )
        for tissue in tissues
    }
    recovery_days = {tid: result.learned_recovery_days for tid, result in recovery_learning.items()}
    for tissue in tissues:
        condition = current_conditions.get(tissue.id)
        if condition and condition.get("recovery_hours_override") is not None:
            recovery_days[tissue.id] = max(
                recovery_days[tissue.id],
                float(condition["recovery_hours_override"]) / 24.0,
            )
    collapse_dates = {
        tissue.id: _detect_collapse_dates(
            tissue.id,
            all_dates,
            exposures_by_tissue[tissue.id],
            excluded_days,
            configs[tissue.id].collapse_drop_threshold,
            prefer_strain=tissue.type in {"joint", "tendon"},
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
            checkin_data=checkin_data.get(tissue.id),
        )

    # Active program exercise IDs for "in_active_program" flag
    active_program = session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()
    active_program_exercise_ids: set[int] = set()
    if active_program:
        days = session.exec(
            select(ProgramDay).where(
                ProgramDay.program_id == active_program.id
            )
        ).all()
        day_ids = [d.id for d in days]
        if day_ids:
            pdes = session.exec(
                select(ProgramDayExercise).where(
                    col(ProgramDayExercise.program_day_id).in_(day_ids)
                )
            ).all()
            active_program_exercise_ids = {
                pde.exercise_id for pde in pdes
            }

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
        "recovery_learning": recovery_learning,
        "exclusion_windows": exclusion_windows,
        "e1rm_by_exercise": e1rm_by_exercise,
        "exposures_by_tissue": exposures_by_tissue,
        "all_dates": all_dates,
        "excluded_days": excluded_days,
        "collapse_dates": collapse_dates,
        "checkin_data": checkin_data,
        "active_program_exercise_ids": active_program_exercise_ids,
    }


def _collect_daily_exposure(
    session: Session,
    exercise_by_id: dict[int, Exercise],
    exercise_mappings: dict[int, list[ExerciseTissue]],
    tissue_by_id: dict[int, Tissue],
    *,
    as_of: date | None,
    excluded_days: set[date],
) -> tuple[
    list[date],
    dict[int, dict[date, ExposureRecord]],
    dict[date, list[dict]],
    dict[int, list[tuple[date, float]]],  # e1rm_by_exercise
]:
    weight_rows = list(
        session.exec(select(WeightLog).order_by(col(WeightLog.logged_at).asc())).all()
    )
    weights = [
        row
        for row in weight_rows
        if row.logged_at.date() <= (as_of or date.max)
    ]
    bodyweight_lookup = bodyweight_by_date(weights)

    session_rows = list(
        session.exec(select(WorkoutSession).order_by(WorkoutSession.date, WorkoutSession.id)).all()
    )
    set_rows = list(session.exec(select(WorkoutSet)).all())
    sets_by_session: dict[int, list[WorkoutSet]] = defaultdict(list)
    for workout_set in set_rows:
        sets_by_session[workout_set.session_id].append(workout_set)

    exposures_by_tissue: dict[int, dict[date, ExposureRecord]] = defaultdict(dict)
    sets_by_date: dict[date, list[dict]] = defaultdict(list)
    # Per-exercise best e1RM per day: exercise_id -> [(date, e1rm)]
    e1rm_by_exercise: dict[int, list[tuple[date, float]]] = defaultdict(list)
    # Track best e1RM per exercise per day during collection
    _daily_e1rm: dict[tuple[int, date], float] = {}
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
            or workout_set.distance_steps is not None
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
                workout_set.distance_steps,
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
            effective_weight_lb = effective_weight(
                exercise,
                workout_set,
                bodyweight_lookup,
                workout_session.date,
            )
            effective_load = effective_set_load(
                exercise,
                workout_set,
                effective_weight_lb,
            )
            if effective_load <= 0:
                continue
            all_dates.add(workout_session.date)
            failure_flag = 1 if workout_set.rep_completion == "failed" else 0
            channel = _rep_range_channel(workout_set)
            sets_by_date[workout_session.date].append(
                {
                    "exercise_id": workout_set.exercise_id,
                    "effective_load": effective_load,
                    "failure": failure_flag,
                    "channel": channel,
                }
            )
            # Track best estimated 1RM per exercise per day
            if supports_strength_estimate(exercise, workout_set):
                e1rm = _estimated_1rm(effective_weight_lb, workout_set.reps)
                key = (workout_set.exercise_id, workout_session.date)
                if e1rm > _daily_e1rm.get(key, 0.0):
                    _daily_e1rm[key] = e1rm
            for mapping in exercise_mappings.get(workout_set.exercise_id, []):
                tissue = tissue_by_id.get(mapping.tissue_id)
                factors = _mapping_factors(mapping, tissue.type if tissue else None)
                record = exposures_by_tissue[mapping.tissue_id].setdefault(
                    workout_session.date,
                    ExposureRecord(date=workout_session.date),
                )
                routing_factor = factors["routing"]
                routed_load = effective_load * routing_factor
                record.raw_load += routed_load
                record.fatigue_load += effective_load * factors["fatigue"]
                record.strain_load += effective_load * max(
                    factors["joint_strain"],
                    factors["tendon_strain"],
                )
                # Route into stimulus channels
                if channel == "strength":
                    record.strength_load += routed_load
                elif channel == "hypertrophy":
                    record.hypertrophy_load += routed_load
                else:
                    record.endurance_load += routed_load
                record.failures += failure_flag
                record.exercise_loads[workout_set.exercise_id] = (
                    record.exercise_loads.get(workout_set.exercise_id, 0.0)
                    + routed_load
                )

    # Flatten daily e1RM tracking into sorted time series per exercise
    for (exercise_id, workout_date), e1rm_val in sorted(_daily_e1rm.items(), key=lambda x: x[0][1]):
        e1rm_by_exercise[exercise_id].append((workout_date, e1rm_val))

    return sorted(all_dates), exposures_by_tissue, sets_by_date, e1rm_by_exercise


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
    checkin_data: dict[date, dict] | None = None,
) -> list[TissueState]:
    prefer_strain = tissue.type in {"joint", "tendon"}
    baseline_capacity = _baseline_capacity(
        exposure_by_date,
        excluded_days,
        config.capacity_prior,
        prefer_strain=prefer_strain,
    )
    fatigue_tau = max(1.0, config.fatigue_tau_days)
    chronic_tau = max(7.0, recovery_days * 6.0)
    current_capacity = baseline_capacity
    acute_fatigue = 0.0
    chronic_load = 0.0
    raw_series = [
        _record_load(exposure_by_date.get(day, ExposureRecord(day)), prefer_strain=prefer_strain)
        for day in all_dates
    ]
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
        current_soreness = 0
        if checkin_data:
            for day_offset in range(7):
                ci = checkin_data.get(current_date - timedelta(days=day_offset))
                if ci:
                    current_soreness = ci["soreness"]
                    break
        raw_load = _record_load(record, prefer_strain=prefer_strain)
        normalized_load = raw_load / max(current_capacity, 1.0)
        fatigue_input = (
            max(record.fatigue_load, record.strain_load)
            if prefer_strain
            else record.fatigue_load
        )
        acute_fatigue = _decay(acute_fatigue, fatigue_tau) + (
            fatigue_input / max(current_capacity, 1.0)
        )
        chronic_load = _decay(chronic_load, chronic_tau) + normalized_load
        recovery_state = 1.0 / (1.0 + acute_fatigue)
        recovery_state *= _clamp(
            1.0 - (min(current_soreness, 10) * 0.04),
            0.75,
            1.0,
        )
        recovery_state = _clamp(recovery_state, 0.0, 1.0)
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
            current_soreness=current_soreness,
            ramp_sensitivity=config.ramp_sensitivity,
            risk_sensitivity=config.risk_sensitivity,
        )
        risk_14d, contributors_14d = _score_risk(
            normalized_load=(recent_28 / 4.0) / max(current_capacity, 1.0),
            acute_fatigue=acute_fatigue,
            ramp_ratio=ramp_ratio * 0.9,
            failures=record.failures,
            condition_severity=condition_severity,
            prior_event_signal=prior_event_signal,
            learned_coefficients=event_coeffs_14,
            current_soreness=current_soreness,
            ramp_sensitivity=config.ramp_sensitivity,
            risk_sensitivity=config.risk_sensitivity,
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
                fatigue_input=fatigue_input,
                current_soreness=current_soreness,
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


def _estimated_1rm(weight: float, reps: int) -> float:
    """Epley formula for estimated 1RM. Only reliable for reps <= 10."""
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return weight
    capped_reps = min(reps, 10)
    return round(weight * (1.0 + capped_reps / 30.0), 2)


def _rep_range_channel(workout_set: WorkoutSet) -> str:
    """Classify a set into a stimulus channel by rep count."""
    if workout_set.duration_secs is not None or workout_set.distance_steps is not None:
        return "endurance"
    if workout_set.reps is None:
        return "endurance"
    if workout_set.reps <= 5:
        return "strength"
    if workout_set.reps <= 12:
        return "hypertrophy"
    return "endurance"


def _baseline_capacity(
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    prior: float,
    *,
    prefer_strain: bool = False,
) -> float:
    values = sorted(
        _record_load(record, prefer_strain=prefer_strain)
        for day, record in exposure_by_date.items()
        if day not in excluded_days
        and _record_load(record, prefer_strain=prefer_strain) > 0
    )
    if not values:
        return max(1.0, prior)
    return max(1.0, _percentile(values, 0.75))


def _recovery_seed_days(
    tissue: Tissue,
    config: TissueModelConfig,
    current_condition: dict | None,
) -> float:
    seed = max(config.recovery_tau_days, tissue.recovery_hours / 24.0)
    if current_condition and current_condition.get("recovery_hours_override") is not None:
        seed = max(seed, float(current_condition["recovery_hours_override"]) / 24.0)
    return seed


def _record_load(record: ExposureRecord, *, prefer_strain: bool) -> float:
    return record.strain_load if prefer_strain else record.raw_load


def _learn_recovery_days(
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    seed: float,
    *,
    prefer_strain: bool = False,
    checkin_data: dict[date, dict] | None = None,
) -> RecoveryLearningResult:
    exposures = [
        _record_load(exposure_by_date.get(day, ExposureRecord(day)), prefer_strain=prefer_strain)
        for day in all_dates
    ]
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
        volume_rebound = seed
    else:
        rebound_days.sort()
        midpoint = rebound_days[len(rebound_days) // 2]
        volume_rebound = round((seed + midpoint) / 2.0, 3)

    # Blend with soreness-calibrated recovery from check-in data when available
    subjective_days: float | None = None
    if checkin_data:
        subjective_days = _subjective_recovery_days(
            all_dates,
            exposure_by_date,
            excluded_days,
            checkin_data,
            baseline_days=volume_rebound,
        )
        if subjective_days is not None:
            final = round(
                max(volume_rebound * 0.85, 0.7 * volume_rebound + 0.3 * subjective_days),
                3,
            )
            return RecoveryLearningResult(
                learned_recovery_days=final,
                volume_rebound=volume_rebound,
                subjective_days=subjective_days,
            )

    return RecoveryLearningResult(
        learned_recovery_days=volume_rebound,
        volume_rebound=volume_rebound,
        subjective_days=None,
    )


def _subjective_recovery_days(
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    checkin_data: dict[date, dict],
    *,
    baseline_days: float,
) -> float | None:
    """Estimate recovery duration using soreness as a calibration signal.

    Recent load remains the baseline readiness model. Soreness can extend or
    modestly contract that prediction, but cannot by itself imply instant
    readiness after a sufficiently heavy training day.
    """
    recovery_durations: list[float] = []
    for current_date in all_dates:
        if current_date in excluded_days:
            continue
        record = exposure_by_date.get(current_date)
        if not record or max(record.raw_load, record.strain_load) <= 0:
            continue
        expected = max(1.0, baseline_days)
        # Look for soreness peak in days +1 to +3
        peak_soreness = 0
        peak_day = None
        for offset in range(1, 4):
            check = current_date + timedelta(days=offset)
            ci = checkin_data.get(check)
            if ci and ci["soreness"] > peak_soreness:
                peak_soreness = ci["soreness"]
                peak_day = check
        if peak_soreness <= 2 or peak_day is None:
            recovery_durations.append(expected)
            continue
        # Find when soreness returns to <= 2
        resolved_days: float | None = None
        for offset in range(1, 15):
            check = peak_day + timedelta(days=offset)
            ci = checkin_data.get(check)
            if ci and ci["soreness"] <= 2:
                resolved_days = float((check - current_date).days)
                break
        if resolved_days is None:
            resolved_days = max(expected, float((peak_day - current_date).days) + 1.5)
        if peak_soreness >= 7:
            resolved_days += 1.0
        elif peak_soreness >= 4:
            resolved_days += 0.5
        recovery_durations.append(max(expected * 0.85, resolved_days))
    if not recovery_durations:
        return None
    recovery_durations.sort()
    return float(recovery_durations[len(recovery_durations) // 2])


def _detect_collapse_dates(
    tissue_id: int,
    all_dates: list[date],
    exposure_by_date: dict[date, ExposureRecord],
    excluded_days: set[date],
    threshold: float,
    *,
    prefer_strain: bool = False,
) -> set[date]:
    del tissue_id
    exposures = [
        _record_load(exposure_by_date.get(day, ExposureRecord(day)), prefer_strain=prefer_strain)
        for day in all_dates
    ]
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
    current_soreness: int = 0,
    ramp_sensitivity: float = 1.0,
    risk_sensitivity: float = 1.0,
) -> tuple[int, list[str]]:
    features = {
        "normalized_load": max(0.0, normalized_load - 0.7),
        "acute_ratio": max(0.0, acute_fatigue - 0.8),
        "ramp_ratio": max(0.0, (ramp_ratio - 1.0) * max(ramp_sensitivity, 0.1)),
        "condition": condition_severity / 4.0,
        "prior": prior_event_signal,
        "failures": min(failures, 2) / 2.0,
        "soreness": min(current_soreness, 10) / 10.0,
    }
    weights = {
        "normalized_load": 0.31,
        "acute_ratio": 0.24,
        "ramp_ratio": 0.20,
        "condition": 0.10,
        "prior": 0.07,
        "failures": 0.05,
        "soreness": 0.07,
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
                "soreness": "reported soreness",
            }[name]
            contributions.append((contribution, label))
    score *= max(risk_sensitivity, 0.75)
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


def _condition_risk_floor_for_mapping(
    status: str | None,
    *,
    horizon_days: int,
    mapping_load: float,
    max_loading_factor: float | None,
) -> int:
    base = _condition_risk_floor(status, horizon_days=horizon_days)
    if not base:
        return 0
    if status == "injured":
        scale = max(0.75, min(mapping_load / 0.35, 1.0))
    else:
        ceiling = max_loading_factor if max_loading_factor is not None else 0.5
        ceiling = max(ceiling, 0.15)
        scale = min(mapping_load / ceiling, 1.0)
    return int(round(base * max(scale, 0.0)))


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
    *,
    protection_blocked: bool = False,
) -> str:
    if protection_blocked:
        return "avoid"
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
