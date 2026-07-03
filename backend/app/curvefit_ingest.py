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


def _map_put(
    session: Session,
    kind: str,
    external_id: str,
    internal_id: int,
    updated_at: str | None = None,
) -> None:
    """Upsert a map entry, refreshing the echoed client ``updated_at``."""
    row = session.exec(
        select(CurveFitSyncMap)
        .where(CurveFitSyncMap.kind == kind)
        .where(CurveFitSyncMap.external_id == external_id)
    ).first()
    if row is not None:
        row.internal_id = internal_id
        if updated_at is not None:
            row.updated_at = updated_at
        session.add(row)
        return
    session.add(
        CurveFitSyncMap(
            kind=kind, external_id=external_id, internal_id=internal_id, updated_at=updated_at
        )
    )


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
        ex_doc = self.ex_docs.get(ext)
        updated_at = ex_doc.get("updated_at") if ex_doc else None
        mapped = _map_get(self.session, "exercise", ext)
        if mapped is not None and self.session.get(Exercise, mapped) is not None:
            _map_put(self.session, "exercise", ext, mapped, updated_at)  # refresh ts
            return mapped
        if ex_doc is None:
            return None
        name = (ex_doc.get("name") or "").strip()
        if not name:
            return None
        existing = self.session.exec(
            select(Exercise).where(func.lower(Exercise.name) == name.lower())
        ).first()
        if existing is not None and existing.id is not None:
            _map_put(self.session, "exercise", ext, existing.id, updated_at)
            return existing.id
        created = Exercise(name=name, load_input_mode="external_weight", set_metric_mode="reps")
        self.session.add(created)
        self.session.flush()
        assert created.id is not None
        _map_put(self.session, "exercise", ext, created.id, updated_at)
        self.created["exercises"] += 1
        return created.id

    def upsert_session(self, sess_doc: dict) -> int | None:
        """Create or update a session. CurveFit-owned (mapped) sessions get
        their started_at/finished_at/notes updated so a finished workout stops
        looking active; diet-tracker-native (dt:) sessions are left untouched.
        """
        ext = sess_doc["id"]
        updated_at = sess_doc.get("updated_at")
        dtid = _parse_dt_id(ext, "session")
        if dtid is not None and self.session.get(WorkoutSession, dtid) is not None:
            return dtid  # native diet-tracker session: never clobber
        mapped = _map_get(self.session, "session", ext)
        if mapped is not None:
            row = self.session.get(WorkoutSession, mapped)
            if row is not None:
                row.started_at = _parse_dt(sess_doc.get("started_at")) or row.started_at
                row.finished_at = _parse_dt(sess_doc.get("finished_at"))
                if sess_doc.get("notes") is not None:
                    row.notes = sess_doc.get("notes")
                self.session.add(row)
                _map_put(self.session, "session", ext, mapped, updated_at)
                return mapped
        try:
            date = dt.date.fromisoformat(str(sess_doc["date"]))
        except (KeyError, ValueError):
            return None
        created = WorkoutSession(
            date=date,
            started_at=_parse_dt(sess_doc.get("started_at")),
            finished_at=_parse_dt(sess_doc.get("finished_at")),
            notes=sess_doc.get("notes"),
        )
        self.session.add(created)
        self.session.flush()
        assert created.id is not None
        _map_put(self.session, "session", ext, created.id, updated_at)
        self.created["sessions"] += 1
        return created.id

    def resolve_session(self, ext: str) -> int | None:
        """Look up a session id (created up-front by ``upsert_session``)."""
        dtid = _parse_dt_id(ext, "session")
        if dtid is not None and self.session.get(WorkoutSession, dtid) is not None:
            return dtid
        mapped = _map_get(self.session, "session", ext)
        if mapped is not None and self.session.get(WorkoutSession, mapped) is not None:
            return mapped
        # Not seen in workout_sessions; fall back to creating from the set's
        # denormalized data so an orphan set still lands somewhere.
        return self.upsert_session(self.sess_docs.get(ext, {"id": ext, "date": None}))

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
        is_dt = dtid is not None and self.session.get(WorkoutSet, dtid) is not None
        internal = dtid if is_dt else _map_get(self.session, "set", ext)
        new_rpe = _rir_to_rpe(rir)

        if internal is not None:
            row = self.session.get(WorkoutSet, internal)
            if row is None:
                return
            # Only CurveFit-owned fields; never touch performed_side /
            # training_mode / rep_completion / timestamps.
            changed = (
                row.weight != weight
                or row.endurance_value != reps
                or row.set_order != set_order
                or (rir is not None and row.rpe != new_rpe)
            )
            row.weight = weight
            row.endurance_value = reps
            row.set_order = set_order
            if rir is not None:  # never null out an existing RPE from a push
                row.rpe = new_rpe
            self.session.add(row)
            if not is_dt:  # curvefit-owned → refresh echoed timestamp
                _map_put(self.session, "set", ext, internal, set_doc.get("updated_at"))
            if changed:
                self.updated["sets"] += 1
            return

        row = WorkoutSet(
            session_id=session_internal,
            exercise_id=exercise_internal,
            set_order=set_order,
            weight=weight,
            endurance_value=reps,
            rpe=new_rpe,
        )
        self.session.add(row)
        self.session.flush()
        assert row.id is not None
        _map_put(self.session, "set", ext, row.id, set_doc.get("updated_at"))
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
        # Sessions first so zero-set sessions still sync and set upserts can
        # resolve their parent from the map.
        for sess_doc in self.document.get("workout_sessions", []):
            self.upsert_session(sess_doc)
        for set_doc in self.document.get("workout_sets", []):
            self.upsert_set(set_doc)
        for tomb in self.document.get("tombstones", []):
            self.apply_tombstone(tomb)
        self.session.commit()
        return {"created": self.created, "updated": self.updated, "deleted": self.deleted}


def ingest_curvefit_document(session: Session, document: dict) -> dict:
    """Merge a CurveFit sync document into the user's DB. Returns a summary."""
    return _Ingestor(session, document).run()
