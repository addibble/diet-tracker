"""Workout planner: selects today's program day and prescribes rep schemes."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import Session, col, select

from app.models import (
    Exercise,
    ExerciseTissue,
    PlannedSession,
    ProgramDay,
    ProgramDayExercise,
    TrainingProgram,
    WorkoutSession,
    WorkoutSet,
)
from app.training_model import build_exercise_strength, build_training_model_summary


def _today(as_of: date | None = None) -> date:
    return as_of or date.today()


def _get_active_program(session: Session) -> TrainingProgram | None:
    return session.exec(
        select(TrainingProgram).where(TrainingProgram.active == 1)
    ).first()


def suggest_today(session: Session, *, as_of: date | None = None) -> dict:
    """Return the recommended program day for today with exercise prescriptions."""
    today = _today(as_of)
    program = _get_active_program(session)
    if program is None:
        return {
            "date": today.isoformat(),
            "message": "No active training program. Create one first.",
            "program": None,
            "suggested_day": None,
            "all_days": [],
        }

    days = list(
        session.exec(
            select(ProgramDay)
            .where(ProgramDay.program_id == program.id)
            .order_by(ProgramDay.sort_order)
        ).all()
    )
    if not days:
        return {
            "date": today.isoformat(),
            "message": "Active program has no days configured.",
            "program": {"id": program.id, "name": program.name},
            "suggested_day": None,
            "all_days": [],
        }

    # Get training model summary for tissue readiness
    summary = build_training_model_summary(session, as_of=as_of)
    tissue_map = {}
    for t in summary.get("tissues", []):
        tissue_info = t["tissue"]
        tissue_map[tissue_info["name"].lower()] = t

    # Get recent planned sessions to determine rotation
    recent_planned = list(
        session.exec(
            select(PlannedSession)
            .where(PlannedSession.date >= today - timedelta(days=30))
            .order_by(col(PlannedSession.date).desc())
        ).all()
    )
    last_day_date: dict[int, date] = {}
    for ps in recent_planned:
        if ps.program_day_id not in last_day_date:
            last_day_date[ps.program_day_id] = ps.date

    # Score each day
    scored_days = []
    max_days_since = 1  # avoid division by zero
    for day in days:
        days_since = (today - last_day_date[day.id]).days if day.id in last_day_date else 14
        if days_since > max_days_since:
            max_days_since = days_since

    for day in days:
        regions = []
        if day.target_regions:
            try:
                regions = json.loads(day.target_regions)
            except (json.JSONDecodeError, TypeError):
                regions = []

        # Compute readiness from tissue states
        readiness_values = []
        risk_penalty = 0.0
        condition_penalty = 0.0
        for region in regions:
            region_lower = region.lower()
            for tissue_name, tissue_data in tissue_map.items():
                if region_lower in tissue_name or tissue_name in region_lower:
                    recovery = tissue_data.get("recovery_estimate", 0.5)
                    readiness_values.append(recovery)
                    if tissue_data.get("risk_7d", 0) >= 60:
                        risk_penalty += 0.2
                    cond = tissue_data.get("current_condition")
                    if cond and cond.get("status") in ("injured", "tender"):
                        condition_penalty += 0.3

        avg_readiness = (
            sum(readiness_values) / len(readiness_values) if readiness_values else 0.7
        )
        readiness_score = max(0.0, avg_readiness - risk_penalty - condition_penalty)

        days_since = (
            (today - last_day_date[day.id]).days if day.id in last_day_date else 14
        )
        rotation_score = min(days_since / max_days_since, 1.0) if max_days_since > 0 else 0.5

        total_score = readiness_score * 0.6 + rotation_score * 0.4

        scored_days.append(
            {
                "day": day,
                "score": round(total_score, 3),
                "readiness": round(readiness_score, 3),
                "rotation": round(rotation_score, 3),
                "days_since_last": days_since,
            }
        )

    scored_days.sort(key=lambda x: x["score"], reverse=True)
    best = scored_days[0]

    # Get exercise prescriptions for the best day
    exercises = prescribe_exercises(session, best["day"].id, as_of=as_of)

    return {
        "date": today.isoformat(),
        "program": {"id": program.id, "name": program.name},
        "suggested_day": {
            "id": best["day"].id,
            "day_label": best["day"].day_label,
            "target_regions": (
                json.loads(best["day"].target_regions)
                if best["day"].target_regions
                else []
            ),
            "score": best["score"],
            "readiness": best["readiness"],
            "rotation": best["rotation"],
            "days_since_last": best["days_since_last"],
            "notes": best["day"].notes,
            "exercises": exercises,
        },
        "all_days": [
            {
                "id": sd["day"].id,
                "day_label": sd["day"].day_label,
                "score": sd["score"],
                "readiness": sd["readiness"],
                "rotation": sd["rotation"],
                "days_since_last": sd["days_since_last"],
            }
            for sd in scored_days
        ],
    }


def prescribe_exercises(
    session: Session, program_day_id: int, *, as_of: date | None = None
) -> list[dict]:
    """For each exercise in the program day, prescribe sets/reps/weight."""
    today = _today(as_of)

    day_exercises = list(
        session.exec(
            select(ProgramDayExercise)
            .where(ProgramDayExercise.program_day_id == program_day_id)
            .order_by(ProgramDayExercise.sort_order)
        ).all()
    )

    if not day_exercises:
        return []

    # Build tissue readiness map
    summary = build_training_model_summary(session, as_of=as_of)
    tissue_readiness: dict[int, float] = {}
    tissue_risk: dict[int, int] = {}
    for t in summary.get("tissues", []):
        tid = t["tissue"]["id"]
        tissue_readiness[tid] = t.get("recovery_estimate", 0.5)
        tissue_risk[tid] = t.get("risk_7d", 0)

    # Build map of exercise -> tissue mappings
    exercise_ids = [de.exercise_id for de in day_exercises]
    exercise_tissues = list(
        session.exec(
            select(ExerciseTissue).where(
                col(ExerciseTissue.exercise_id).in_(exercise_ids)
            )
        ).all()
    )
    tissues_by_exercise: dict[int, list[ExerciseTissue]] = defaultdict(list)
    for et in exercise_tissues:
        tissues_by_exercise[et.exercise_id].append(et)

    results = []
    for de in day_exercises:
        exercise = session.get(Exercise, de.exercise_id)
        if not exercise:
            continue

        # Get e1RM
        current_e1rm = 0.0
        try:
            strength = build_exercise_strength(session, exercise.id, as_of=as_of)
            current_e1rm = strength.get("current_e1rm", 0.0)
        except (KeyError, Exception):
            pass

        # Get tissue readiness for this exercise
        mappings = tissues_by_exercise.get(exercise.id, [])
        readiness_vals = []
        for m in mappings:
            if m.tissue_id in tissue_readiness:
                readiness_vals.append(tissue_readiness[m.tissue_id])

        avg_readiness = (
            sum(readiness_vals) / len(readiness_vals) if readiness_vals else 0.7
        )

        # Check days since last heavy work on these tissues
        days_since_heavy = _days_since_heavy_work(session, exercise.id, today)

        # Select rep scheme
        rep_scheme, target_reps, intensity_range, rationale = _select_rep_scheme(
            avg_readiness, days_since_heavy
        )

        # Compute target weight
        target_weight = None
        if current_e1rm > 0:
            intensity = (intensity_range[0] + intensity_range[1]) / 2
            raw_weight = current_e1rm * intensity
            # Round to nearest sensible increment
            if exercise.equipment == "barbell":
                target_weight = round(raw_weight / 5) * 5
            elif exercise.equipment == "dumbbell":
                target_weight = round(raw_weight / 2.5) * 2.5
            else:
                target_weight = round(raw_weight / 5) * 5
            target_weight = max(target_weight, 0)

        # Get last performance
        last_perf = _get_last_performance(session, exercise.id)

        # Progressive overload logic
        overload_note = None
        if last_perf and target_weight and target_weight > 0:
            last_weight = last_perf.get("max_weight", 0)
            all_full = last_perf.get("all_full", False)
            if all_full and last_weight and last_weight >= target_weight:
                increment = 5.0 if exercise.equipment == "barbell" else 2.5
                target_weight = last_weight + increment
                overload_note = f"Progressive overload: +{increment} lbs"
            elif not all_full and last_weight:
                target_weight = last_weight
                overload_note = "Repeat weight, aim for full completion"

        target_sets = de.target_sets

        results.append(
            {
                "exercise_id": exercise.id,
                "exercise_name": exercise.name,
                "equipment": exercise.equipment,
                "rep_scheme": rep_scheme,
                "target_sets": target_sets,
                "target_reps": target_reps,
                "target_weight": target_weight,
                "rationale": rationale,
                "overload_note": overload_note,
                "current_e1rm": round(current_e1rm, 2) if current_e1rm else None,
                "avg_tissue_readiness": round(avg_readiness, 3),
                "last_performance": last_perf,
            }
        )

    return results


def _select_rep_scheme(
    avg_readiness: float, days_since_heavy: int
) -> tuple[str, str, tuple[float, float], str]:
    """Select rep scheme based on tissue readiness and training history.

    Returns (scheme_name, target_reps_str, intensity_range, rationale).
    """
    if avg_readiness >= 0.8 and days_since_heavy >= 5:
        return (
            "heavy",
            "3-5",
            (0.80, 0.85),
            "Tissues well-recovered and no recent heavy work; strength focus.",
        )
    elif avg_readiness >= 0.6:
        return (
            "volume",
            "8-12",
            (0.65, 0.75),
            "Moderate recovery; hypertrophy-focused volume work.",
        )
    else:
        return (
            "light",
            "15-20",
            (0.50, 0.60),
            "Low tissue readiness; light recovery work recommended.",
        )


def _days_since_heavy_work(
    session: Session, exercise_id: int, today: date
) -> int:
    """Find how many days since last heavy set (<=5 reps at RPE >= 7) for this exercise."""
    stmt = (
        select(WorkoutSession.date)
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSet.exercise_id == exercise_id,
            WorkoutSet.reps != None,  # noqa: E711
            WorkoutSet.reps <= 5,
        )
        .order_by(col(WorkoutSession.date).desc())
        .limit(1)
    )
    result = session.exec(stmt).first()
    if result is None:
        return 999
    return (today - result).days


def _get_last_performance(session: Session, exercise_id: int) -> dict | None:
    """Get last session's performance for an exercise."""
    stmt = (
        select(WorkoutSet, WorkoutSession.date)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id)
        .order_by(col(WorkoutSession.date).desc(), WorkoutSet.set_order)
    )
    rows = session.exec(stmt).all()
    if not rows:
        return None

    last_date = rows[0][1]
    last_sets = [s for s, d in rows if d == last_date]
    all_full = all(s.rep_completion == "full" for s in last_sets)
    max_weight = max((s.weight or 0) for s in last_sets)

    return {
        "date": str(last_date),
        "sets": [
            {
                "reps": s.reps,
                "weight": s.weight,
                "rpe": s.rpe,
                "rep_completion": s.rep_completion,
            }
            for s in last_sets
        ],
        "all_full": all_full,
        "max_weight": max_weight,
    }


def accept_plan(
    session: Session, program_day_id: int, plan_date: date, *, as_of: date | None = None
) -> dict:
    """Create a PlannedSession record linking a program day to a date."""
    day = session.get(ProgramDay, program_day_id)
    if day is None:
        raise ValueError(f"Program day {program_day_id} not found")

    planned = PlannedSession(
        program_day_id=program_day_id,
        date=plan_date,
        status="planned",
    )
    session.add(planned)
    session.commit()
    session.refresh(planned)

    return {
        "id": planned.id,
        "program_day_id": planned.program_day_id,
        "date": planned.date.isoformat(),
        "status": planned.status,
        "workout_session_id": planned.workout_session_id,
        "notes": planned.notes,
    }


def suggest_week(
    session: Session, *, as_of: date | None = None
) -> list[dict]:
    """Return suggestions for the next 7 days."""
    today = _today(as_of)
    results = []
    for offset in range(7):
        day_date = today + timedelta(days=offset)
        suggestion = suggest_today(session, as_of=day_date)
        suggestion["date"] = day_date.isoformat()
        results.append(suggestion)
    return results
