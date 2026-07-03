"""Ingest a CurveFit sync document back into the user's diet-tracker DB.

The write half of the storage-only adapter (docs/SYNC_PLAN.md, Phase 3). Merge/
upsert **keyed by external id**, never a wholesale replace:

* Rows that originated in diet-tracker carry an embedded ``dt:<kind>:<id>``
  external id — we address them directly and update only the fields CurveFit
  owns (weight / reps / RPE / order), leaving diet-tracker-only columns
  (``performed_side``, ``training_mode``, ``rep_completion``, timestamps, …)
  untouched.
* Rows created in CurveFit carry opaque ids; we resolve them via the
  ``curvefit_sync_map`` table (creating rows + map entries on first sight) so
  repeated pushes are idempotent.
* Deletes (tombstones) apply **only** to CurveFit-owned rows (those with a map
  entry). Original diet-tracker rows are never deleted by a push.

No strength-model calculation happens here — readiness β / caches are diet-
tracker's own concern and are recomputed on its schedule.
"""

from __future__ import annotations

import datetime as dt

from sqlmodel import Session, func, select

from app.models import CurveFitSyncMap, Exercise, WorkoutSession, WorkoutSet
from app.units import rir_to_rpe


def _parse_dt_id(external_id: str, kind: str) -> int | None:
    prefix = f"dt:{kind}:"
    if external_id.startswith(prefix):
        try:
            return int(external_id[len(prefix):])
        except ValueError:
            return None
    return None


def _map_get(session: Session, kind: str, external_id: str) -> int | None:
    row = session.exec(
        select(CurveFitSyncMap)
        .where(CurveFitSyncMap.kind == kind)
        .where(CurveFitSyncMap.external_id == external_id)
    ).first()
    return row.internal_id if row else None


def _map_put(session: Session, kind: str, external_id: str, internal_id: int) -> None:
    if _map_get(session, kind, external_id) is not None:
        return
    session.add(CurveFitSyncMap(kind=kind, external_id=external_id, internal_id=internal_id))


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _rir_to_rpe(rir: float | int | None) -> float | None:
    return None if rir is None else rir_to_rpe(rir)


class _Ingestor:
    def __init__(self, session: Session, document: dict) -> None:
        self.session = session
        self.document = document
        self.ex_docs = {e["id"]: e for e in document.get("exercises", [])}
        self.sess_docs = {s["id"]: s for s in document.get("workout_sessions", [])}
        self.created = {"exercises": 0, "sessions": 0, "sets": 0}
        self.updated = {"sets": 0}
        self.deleted = {"exercises": 0, "sessions": 0, "sets": 0}

    # ── resolvers ──────────────────────────────────────────────────────────

    def resolve_exercise(self, ext: str) -> int | None:
        dtid = _parse_dt_id(ext, "exercise")
        if dtid is not None and self.session.get(Exercise, dtid) is not None:
            return dtid
        mapped = _map_get(self.session, "exercise", ext)
        if mapped is not None and self.session.get(Exercise, mapped) is not None:
            return mapped
        ex_doc = self.ex_docs.get(ext)
        if ex_doc is None:
            return None
        name = (ex_doc.get("name") or "").strip()
        if not name:
            return None
        existing = self.session.exec(
            select(Exercise).where(func.lower(Exercise.name) == name.lower())
        ).first()
        if existing is not None and existing.id is not None:
            _map_put(self.session, "exercise", ext, existing.id)
            return existing.id
        created = Exercise(name=name, load_input_mode="external_weight", set_metric_mode="reps")
        self.session.add(created)
        self.session.flush()
        assert created.id is not None
        _map_put(self.session, "exercise", ext, created.id)
        self.created["exercises"] += 1
        return created.id

    def resolve_session(self, ext: str) -> int | None:
        dtid = _parse_dt_id(ext, "session")
        if dtid is not None and self.session.get(WorkoutSession, dtid) is not None:
            return dtid
        mapped = _map_get(self.session, "session", ext)
        if mapped is not None and self.session.get(WorkoutSession, mapped) is not None:
            return mapped
        doc = self.sess_docs.get(ext)
        if doc is None:
            return None
        try:
            date = dt.date.fromisoformat(str(doc["date"]))
        except (KeyError, ValueError):
            return None
        created = WorkoutSession(
            date=date,
            started_at=_parse_dt(doc.get("started_at")),
            finished_at=_parse_dt(doc.get("finished_at")),
            notes=doc.get("notes"),
        )
        self.session.add(created)
        self.session.flush()
        assert created.id is not None
        _map_put(self.session, "session", ext, created.id)
        self.created["sessions"] += 1
        return created.id

    # ── upserts ────────────────────────────────────────────────────────────

    def upsert_set(self, set_doc: dict) -> None:
        ext = set_doc["id"]
        session_internal = self.resolve_session(set_doc["session_id"])
        exercise_internal = self.resolve_exercise(set_doc["exercise_id"])
        if session_internal is None or exercise_internal is None:
            return

        weight = set_doc.get("weight")
        reps = set_doc.get("reps")
        rir = set_doc.get("rir")
        set_order = set_doc.get("set_order", 1)

        # Locate an existing row (dt: embedded id, or previously mapped uuid).
        dtid = _parse_dt_id(ext, "set")
        internal = dtid if (dtid is not None and self.session.get(WorkoutSet, dtid)) else _map_get(self.session, "set", ext)

        if internal is not None:
            row = self.session.get(WorkoutSet, internal)
            if row is None:
                return
            # Only CurveFit-owned fields; never touch performed_side /
            # training_mode / rep_completion / timestamps.
            row.weight = weight
            row.endurance_value = reps
            row.set_order = set_order
            if rir is not None:  # never null out an existing RPE from a push
                row.rpe = _rir_to_rpe(rir)
            self.session.add(row)
            self.updated["sets"] += 1
            return

        row = WorkoutSet(
            session_id=session_internal,
            exercise_id=exercise_internal,
            set_order=set_order,
            weight=weight,
            endurance_value=reps,
            rpe=_rir_to_rpe(rir),
        )
        self.session.add(row)
        self.session.flush()
        assert row.id is not None
        _map_put(self.session, "set", ext, row.id)
        self.created["sets"] += 1

    # ── deletes ────────────────────────────────────────────────────────────

    def apply_tombstone(self, tomb: dict) -> None:
        kind = tomb.get("kind")
        ext = tomb.get("id")
        if kind not in ("set", "session", "exercise") or not ext:
            return
        # Only CurveFit-owned rows (mapped) may be deleted; original
        # diet-tracker rows (dt: ids without a map entry) are preserved.
        internal = _map_get(self.session, kind, ext)
        if internal is None:
            return
        if kind == "set":
            row = self.session.get(WorkoutSet, internal)
            if row is not None:
                self.session.delete(row)
                self.deleted["sets"] += 1
        elif kind == "session":
            for s in self.session.exec(
                select(WorkoutSet).where(WorkoutSet.session_id == internal)
            ).all():
                self.session.delete(s)
                self.deleted["sets"] += 1
            row = self.session.get(WorkoutSession, internal)
            if row is not None:
                self.session.delete(row)
                self.deleted["sessions"] += 1
        elif kind == "exercise":
            still_used = self.session.exec(
                select(WorkoutSet.id).where(WorkoutSet.exercise_id == internal).limit(1)
            ).first()
            if still_used is None:
                row = self.session.get(Exercise, internal)
                if row is not None:
                    self.session.delete(row)
                    self.deleted["exercises"] += 1
        # Drop the map entry so a resurrected id creates fresh.
        map_row = self.session.exec(
            select(CurveFitSyncMap)
            .where(CurveFitSyncMap.kind == kind)
            .where(CurveFitSyncMap.external_id == ext)
        ).first()
        if map_row is not None:
            self.session.delete(map_row)

    def run(self) -> dict:
        for set_doc in self.document.get("workout_sets", []):
            self.upsert_set(set_doc)
        for tomb in self.document.get("tombstones", []):
            self.apply_tombstone(tomb)
        self.session.commit()
        return {"created": self.created, "updated": self.updated, "deleted": self.deleted}


def ingest_curvefit_document(session: Session, document: dict) -> dict:
    """Merge a CurveFit sync document into the user's DB. Returns a summary."""
    return _Ingestor(session, document).run()
