"""Project a user's diet-tracker workout log onto CurveFit's sync document.

Read-only, pure row mapping — **no strength-model calculation** (see
docs/SYNC_PLAN.md in the curvefit repo). Everything CurveFit needs to fit
curves and plan sets is derived in its frontend; here we only translate rows.

Scope filters for CurveFit v1 (quantified against production: ~89% of logged
sets survive):

* Only ``load_input_mode == "external_weight"`` exercises (CurveFit has no
  effective-weight / bodyweight model yet).
* Only ``set_metric_mode == "reps"`` exercises (no duration/distance).
* Only sets with a non-null ``weight`` and ``endurance_value``.

RIR mapping: uses ``app.units.rpe_to_rir`` when ``rpe`` is set, else ``None``
(CurveFit's
``WorkoutSetRow.rir`` is nullable; such sets show in History but are excluded
from the fit).

External ids are deterministic (``dt:<internal_id>``) so repeated pulls agree
without any stored mapping. The CurveFit client reconciles these against its
shipped catalog by name.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from app.exercise_groups import get_exercise_group
from app.models import CurveFitSyncMap, Exercise, WorkoutSession, WorkoutSet
from app.units import rpe_to_rir

# Wire format constants — mirror curvefit/src/lib/storage.ts.
SYNC_FORMAT_VERSION = 1
CURVEFIT_SCHEMA_VERSION = 2

# CurveFit groups. diet-tracker's classifier returns these plus "Uncategorized".
_GROUP_MAP = {
    "Push": "Push",
    "Pull": "Pull",
    "Legs": "Legs",
    "Shoulders": "Shoulders",
    "Core": "Core",
    "Uncategorized": "Other",
}
_HIGH_REP_GROUPS = {"Shoulders", "Core"}


def _ext_id(kind: str, internal_id: int) -> str:
    return f"dt:{kind}:{internal_id}"


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset.

    DB timestamps are stored naive-UTC; emitting them without an offset would
    let the client's ``Date.parse`` read them as *local* time, skewing every
    last-writer-wins comparison by the client's UTC offset. Always tag UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _plain_notes(notes: str | None) -> str | None:
    """Drop notes that are diet-tracker JSON metadata (would render as raw
    JSON in CurveFit's History). Keep genuine free-text notes."""
    if not notes:
        return notes
    stripped = notes.strip()
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return None
        except (json.JSONDecodeError, ValueError):
            pass
    return notes


def _curvefit_group(exercise_id: int, session: Session) -> str:
    return _GROUP_MAP.get(get_exercise_group(exercise_id, session), "Other")


def _default_reps(group: str) -> int:
    return 15 if group in _HIGH_REP_GROUPS else 10


def build_curvefit_document(session: Session, *, device_id: str = "diet-tracker") -> dict:
    """Build the CurveFit sync document for the user bound to ``session``."""
    # Reverse maps keyed by (kind, internal_id): the external id CurveFit
    # originally used, plus the client's own updated_at. Echoing both lets a
    # round-tripped row keep its id (no duplicate) *and* its client timestamp,
    # so the projection can't clobber local customization the projection
    # can't reproduce (exercise group / notes / rep target).
    map_rows = session.exec(select(CurveFitSyncMap)).all()
    ext_by_internal: dict[tuple[str, int], str] = {
        (row.kind, row.internal_id): row.external_id for row in map_rows
    }
    updated_by_internal: dict[tuple[str, int], str] = {
        (row.kind, row.internal_id): row.updated_at
        for row in map_rows
        if row.updated_at is not None
    }

    def ext_id(kind: str, internal_id: int) -> str:
        return ext_by_internal.get((kind, internal_id)) or _ext_id(kind, internal_id)

    def row_updated(kind: str, internal_id: int, fallback: str) -> str:
        return updated_by_internal.get((kind, internal_id)) or fallback

    exercises = {
        ex.id: ex
        for ex in session.exec(select(Exercise)).all()
        if ex.id is not None
    }

    def _is_supported(ex: Exercise) -> bool:
        return (ex.load_input_mode or "external_weight") == "external_weight" and (
            ex.set_metric_mode or "reps"
        ) == "reps"

    rows = session.exec(
        select(WorkoutSet, WorkoutSession)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .order_by(col(WorkoutSession.date), WorkoutSet.session_id, WorkoutSet.set_order)
    ).all()

    out_sets: list[dict] = []
    sessions_seen: dict[int, WorkoutSession] = {}
    session_exercise_ids: dict[int, list[str]] = {}
    referenced_exercise_ids: set[int] = set()

    for ws, session_row in rows:
        exercise = exercises.get(ws.exercise_id)
        if exercise is None or not _is_supported(exercise):
            continue
        if ws.weight is None or ws.endurance_value is None:
            continue

        set_ext = ext_id("set", ws.id)  # type: ignore[arg-type]
        session_ext = ext_id("session", session_row.id)  # type: ignore[arg-type]
        exercise_ext = ext_id("exercise", exercise.id)
        referenced_exercise_ids.add(exercise.id)

        rir = None if ws.rpe is None else rpe_to_rir(ws.rpe)
        created = _iso(ws.created_at) or f"{session_row.date}T00:00:00+00:00"
        out_sets.append(
            {
                "id": set_ext,
                "session_id": session_ext,
                "exercise_id": exercise_ext,
                "set_order": ws.set_order,
                "weight": ws.weight,
                "reps": int(round(ws.endurance_value)),
                "rir": rir,
                "date": str(session_row.date),
                "created_at": created,
                "updated_at": row_updated("set", ws.id, created),  # type: ignore[arg-type]
            }
        )

        sessions_seen.setdefault(session_row.id, session_row)  # type: ignore[arg-type]
        session_exercise_ids.setdefault(session_row.id, [])  # type: ignore[arg-type]
        if exercise_ext not in session_exercise_ids[session_row.id]:  # type: ignore[index]
            session_exercise_ids[session_row.id].append(exercise_ext)  # type: ignore[index]

    out_sessions: list[dict] = []
    for sid, session_row in sessions_seen.items():
        started = _iso(session_row.started_at) or f"{session_row.date}T00:00:00+00:00"
        session_updated = _iso(session_row.created_at) or started
        out_sessions.append(
            {
                "id": ext_id("session", sid),
                "date": str(session_row.date),
                "started_at": started,
                "finished_at": _iso(session_row.finished_at),
                "exercise_ids": session_exercise_ids.get(sid, []),
                "notes": _plain_notes(session_row.notes),
                "updated_at": row_updated("session", sid, session_updated),
            }
        )

    out_exercises: list[dict] = []
    for ex_id in sorted(referenced_exercise_ids):
        exercise = exercises[ex_id]
        group = _curvefit_group(ex_id, session)
        ex_updated = _iso(exercise.created_at) or "1970-01-01T00:00:00+00:00"
        out_exercises.append(
            {
                "id": ext_id("exercise", ex_id),
                "name": exercise.name,
                "group": group,
                "default_reps_set1": _default_reps(group),
                # "user" so CurveFit's mergeExercises keeps rows that aren't in
                # its shipped catalog; the client remaps name-matches to its
                # own catalog ids on import.
                "source": "user",
                "updated_at": row_updated("exercise", ex_id, ex_updated),
            }
        )

    return {
        "sync_format": SYNC_FORMAT_VERSION,
        "schema_version": CURVEFIT_SCHEMA_VERSION,
        "device_id": device_id,
        "updated_at": datetime.now(UTC).isoformat(),
        # Minimal prefs with an ancient timestamp so the client's local prefs
        # always win the whole-object LWW merge.
        "user_prefs": {"tutorial_completed": False, "updated_at": "1970-01-01T00:00:00+00:00"},
        "exercises": out_exercises,
        "workout_sessions": out_sessions,
        "workout_sets": out_sets,
        # curve_fits intentionally omitted — diet-tracker can't reproduce fits;
        # the client recomputes from the raw sets.
        "tombstones": [],
    }
