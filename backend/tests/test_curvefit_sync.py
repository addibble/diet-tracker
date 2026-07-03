"""Tests for the CurveFit storage-sync adapter (projection + endpoint)."""

from __future__ import annotations

import datetime as dt

from sqlmodel import select

from app.curvefit_projection import build_curvefit_document
from app.models import Exercise, WorkoutSession, WorkoutSet


def _seed(session):
    """Seed a mix of supported and unsupported exercises + sets.

    Returns the ids of the supported exercise for convenience.
    """
    barbell = Exercise(name="Barbell Curl", load_input_mode="external_weight", set_metric_mode="reps")
    pushup = Exercise(name="Push Up", load_input_mode="bodyweight", set_metric_mode="reps")
    plank = Exercise(name="Weighted Plank", load_input_mode="external_weight", set_metric_mode="duration")
    session.add(barbell)
    session.add(pushup)
    session.add(plank)
    session.commit()
    session.refresh(barbell)
    session.refresh(pushup)
    session.refresh(plank)

    ws = WorkoutSession(date=dt.date(2026, 5, 10))
    session.add(ws)
    session.commit()
    session.refresh(ws)

    # Supported: two barbell sets, one with rpe (→ rir 2), one without (→ null).
    session.add(WorkoutSet(session_id=ws.id, exercise_id=barbell.id, set_order=1, weight=40.0, endurance_value=10, rpe=8.0))
    session.add(WorkoutSet(session_id=ws.id, exercise_id=barbell.id, set_order=2, weight=45.0, endurance_value=8, rpe=None))
    # Unsupported: bodyweight (no weight) and duration exercise.
    session.add(WorkoutSet(session_id=ws.id, exercise_id=pushup.id, set_order=3, weight=None, endurance_value=20, rpe=7.0))
    session.add(WorkoutSet(session_id=ws.id, exercise_id=plank.id, set_order=4, weight=25.0, endurance_value=60, rpe=8.0))
    session.commit()
    return barbell.id, pushup.id, plank.id, ws.id


def test_projection_filters_unsupported(session):
    barbell_id, pushup_id, plank_id, ws_id = _seed(session)
    doc = build_curvefit_document(session)

    # Only the two barbell sets survive.
    assert len(doc["workout_sets"]) == 2
    ex_refs = {s["exercise_id"] for s in doc["workout_sets"]}
    assert ex_refs == {f"dt:exercise:{barbell_id}"}
    assert len(doc["exercises"]) == 1
    assert doc["exercises"][0]["source"] == "user"
    assert doc["exercises"][0]["group"] in {"Push", "Pull", "Legs", "Shoulders", "Core", "Other"}


def test_projection_rir_mapping(session):
    _seed(session)
    doc = build_curvefit_document(session)
    by_order = {s["set_order"]: s for s in doc["workout_sets"]}
    assert by_order[1]["rir"] == 2  # rpe 8 → rir 2
    assert by_order[1]["reps"] == 10
    assert by_order[2]["rir"] is None  # rpe null → rir null


def test_projection_session_and_envelope(session):
    _seed(session)
    doc = build_curvefit_document(session)
    assert doc["sync_format"] == 1
    assert doc["schema_version"] == 2
    assert "curve_fits" not in doc  # backend can't reproduce fits
    assert len(doc["workout_sessions"]) == 1
    sess = doc["workout_sessions"][0]
    assert sess["date"] == "2026-05-10"
    # exercise_ids derived from surviving sets only (single supported exercise).
    assert sess["exercise_ids"] == [doc["exercises"][0]["id"]]


def test_get_document_endpoint(session, client):
    from app.main import app
    from app.routers.curvefit_sync import get_sync_session, get_sync_user

    _seed(session)

    class _U:
        id = "test-user"
        disabled_at = None

    app.dependency_overrides[get_sync_user] = lambda: _U()
    app.dependency_overrides[get_sync_session] = lambda: session
    try:
        res = client.get("/api/curvefit-sync")
        assert res.status_code == 200
        assert res.headers.get("ETag")
        body = res.json()
        assert len(body["workout_sets"]) == 2
    finally:
        app.dependency_overrides.pop(get_sync_user, None)
        app.dependency_overrides.pop(get_sync_session, None)


def test_get_document_requires_token(client):
    # No Authorization header, no override → 401.
    res = client.get("/api/curvefit-sync")
    assert res.status_code == 401


# ── Phase 3: ingest / round-trip ───────────────────────────────────────────

from app.curvefit_ingest import ingest_curvefit_document  # noqa: E402
from app.models import CurveFitSyncMap  # noqa: E402


def _push_doc(sets, sessions, exercises, tombstones=None):
    return {
        "sync_format": 1,
        "schema_version": 2,
        "device_id": "dev-local",
        "updated_at": "2026-05-12T00:00:00Z",
        "user_prefs": {"tutorial_completed": True, "updated_at": "2026-05-12T00:00:00Z"},
        "exercises": exercises,
        "workout_sessions": sessions,
        "workout_sets": sets,
        "tombstones": tombstones or [],
    }


def _cf_exercise(ext, name):
    return {"id": ext, "name": name, "group": "Pull", "default_reps_set1": 10,
            "source": "user", "updated_at": "2026-05-12T00:00:00Z"}


def _cf_session(ext, date="2026-05-12"):
    return {"id": ext, "date": date, "started_at": f"{date}T12:00:00", "finished_at": None,
            "exercise_ids": [], "updated_at": "2026-05-12T00:00:00Z"}


def _cf_set(ext, session_ext, exercise_ext, weight=40, reps=10, rir=2):
    return {"id": ext, "session_id": session_ext, "exercise_id": exercise_ext,
            "set_order": 1, "weight": weight, "reps": reps, "rir": rir,
            "date": "2026-05-12", "created_at": "2026-05-12T12:00:00Z",
            "updated_at": "2026-05-12T12:00:00Z"}


def test_ingest_creates_curvefit_rows_idempotently(session):
    doc = _push_doc(
        sets=[_cf_set("cf-set-1", "cf-sess-1", "catalog:barbell-curl")],
        sessions=[_cf_session("cf-sess-1")],
        exercises=[_cf_exercise("catalog:barbell-curl", "Barbell Curl")],
    )
    summary1 = ingest_curvefit_document(session, doc)
    assert summary1["created"] == {"exercises": 1, "sessions": 1, "sets": 1}
    assert session.exec(select(WorkoutSet)).all().__len__() == 1

    # Re-push the same document → no duplicates (mapped by external id).
    summary2 = ingest_curvefit_document(session, doc)
    assert summary2["created"] == {"exercises": 0, "sessions": 0, "sets": 0}
    assert len(session.exec(select(WorkoutSet)).all()) == 1
    assert len(session.exec(select(Exercise)).all()) == 1


def test_ingest_updates_dt_set_preserving_dt_only_fields(session):
    ex = Exercise(name="Row", load_input_mode="external_weight", set_metric_mode="reps")
    session.add(ex)
    session.commit()
    session.refresh(ex)
    ws = WorkoutSession(date=dt.date(2026, 5, 12))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    s = WorkoutSet(session_id=ws.id, exercise_id=ex.id, set_order=1, weight=40.0,
                   endurance_value=10, rpe=8.0, performed_side="left", training_mode="heavy",
                   rep_completion="full")
    session.add(s)
    session.commit()
    session.refresh(s)

    doc = _push_doc(
        sets=[_cf_set(f"dt:set:{s.id}", f"dt:session:{ws.id}", f"dt:exercise:{ex.id}",
                      weight=45, reps=12, rir=1)],
        sessions=[_cf_session(f"dt:session:{ws.id}")],
        exercises=[_cf_exercise(f"dt:exercise:{ex.id}", "Row")],
    )
    ingest_curvefit_document(session, doc)
    session.refresh(s)
    assert s.weight == 45  # CurveFit-owned field updated
    assert s.endurance_value == 12
    assert s.rpe == 9  # 10 - rir(1)
    assert s.performed_side == "left"  # diet-tracker-only preserved
    assert s.training_mode == "heavy"
    assert s.rep_completion == "full"
    # No new rows created (dt: ids address existing rows directly).
    assert len(session.exec(select(WorkoutSet)).all()) == 1


def test_projection_echoes_curvefit_external_ids_after_ingest(session):
    doc = _push_doc(
        sets=[_cf_set("cf-set-9", "cf-sess-9", "catalog:barbell-curl")],
        sessions=[_cf_session("cf-sess-9")],
        exercises=[_cf_exercise("catalog:barbell-curl", "Barbell Curl")],
    )
    ingest_curvefit_document(session, doc)
    projected = build_curvefit_document(session)
    ids = {s["id"] for s in projected["workout_sets"]}
    assert ids == {"cf-set-9"}  # echoed original id, not a fresh dt: id
    assert projected["workout_sessions"][0]["id"] == "cf-sess-9"


def test_tombstone_deletes_only_mapped_rows(session):
    # Seed a diet-tracker-native set (never mapped).
    ex = Exercise(name="Row", load_input_mode="external_weight", set_metric_mode="reps")
    session.add(ex)
    session.commit()
    session.refresh(ex)
    ws = WorkoutSession(date=dt.date(2026, 5, 12))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    native = WorkoutSet(session_id=ws.id, exercise_id=ex.id, set_order=1, weight=40.0, endurance_value=10)
    session.add(native)
    session.commit()
    session.refresh(native)

    # Create a CurveFit-owned set, then tombstone both it and the native set.
    ingest_curvefit_document(session, _push_doc(
        sets=[_cf_set("cf-set-x", "cf-sess-x", "catalog:barbell-curl")],
        sessions=[_cf_session("cf-sess-x")],
        exercises=[_cf_exercise("catalog:barbell-curl", "Barbell Curl")],
    ))
    ingest_curvefit_document(session, _push_doc(
        sets=[], sessions=[], exercises=[],
        tombstones=[
            {"kind": "set", "id": "cf-set-x", "deleted_at": "2026-05-13T00:00:00Z"},
            {"kind": "set", "id": f"dt:set:{native.id}", "deleted_at": "2026-05-13T00:00:00Z"},
        ],
    ))
    remaining = session.exec(select(WorkoutSet)).all()
    assert native.id in {r.id for r in remaining}  # native preserved
    assert len(remaining) == 1  # only the CurveFit-owned set was deleted
    assert session.exec(select(CurveFitSyncMap).where(CurveFitSyncMap.external_id == "cf-set-x")).first() is None


def test_put_endpoint_if_match(session, client):
    from app.main import app
    from app.routers.curvefit_sync import get_sync_session, get_sync_user

    class _U:
        id = "test-user"
        disabled_at = None

    app.dependency_overrides[get_sync_user] = lambda: _U()
    app.dependency_overrides[get_sync_session] = lambda: session
    try:
        etag = client.get("/api/curvefit-sync").headers["ETag"]
        doc = _push_doc(
            sets=[_cf_set("cf-set-1", "cf-sess-1", "catalog:barbell-curl")],
            sessions=[_cf_session("cf-sess-1")],
            exercises=[_cf_exercise("catalog:barbell-curl", "Barbell Curl")],
        )
        # Stale If-Match → 412.
        bad = client.put("/api/curvefit-sync", json=doc, headers={"If-Match": '"deadbeef"'})
        assert bad.status_code == 412
        # Correct If-Match → 200 + new ETag.
        ok = client.put("/api/curvefit-sync", json=doc, headers={"If-Match": etag})
        assert ok.status_code == 200
        assert ok.headers["ETag"] != etag
        assert ok.json()["summary"]["created"]["sets"] == 1
    finally:
        app.dependency_overrides.pop(get_sync_user, None)
        app.dependency_overrides.pop(get_sync_session, None)


# ── Review fixes: timestamps, map updated_at echo, session upsert, origin ────


def test_projection_timestamps_are_utc_tagged(session):
    _seed(session)
    doc = build_curvefit_document(session)
    for s in doc["workout_sets"]:
        assert s["updated_at"].endswith("+00:00")
        assert s["created_at"].endswith("+00:00")
    sess = doc["workout_sessions"][0]
    assert sess["updated_at"].endswith("+00:00")
    assert sess["started_at"].endswith("+00:00")
    for e in doc["exercises"]:
        assert e["updated_at"].endswith("+00:00")


def test_roundtrip_echoes_client_updated_at(session):
    stamp = "2026-05-12T00:00:00+00:00"
    doc = _push_doc(
        sets=[{**_cf_set("cf-set-a", "cf-sess-a", "user:myex"), "updated_at": stamp}],
        sessions=[{**_cf_session("cf-sess-a"), "updated_at": stamp}],
        exercises=[{**_cf_exercise("user:myex", "My Ex"), "updated_at": stamp}],
    )
    ingest_curvefit_document(session, doc)
    proj = build_curvefit_document(session)
    # Echoed client updated_at (not the DB push time) so LWW can't revert
    # local customization the projection can't reproduce.
    assert proj["workout_sets"][0]["updated_at"] == stamp
    assert proj["workout_sessions"][0]["updated_at"] == stamp
    ex = next(e for e in proj["exercises"] if e["id"] == "user:myex")
    assert ex["updated_at"] == stamp


def test_session_upsert_updates_finished_at(session):
    ingest_curvefit_document(session, _push_doc(
        sets=[_cf_set("s1", "cf-sess-f", "user:e")],
        sessions=[{**_cf_session("cf-sess-f"), "finished_at": None}],
        exercises=[_cf_exercise("user:e", "E")],
    ))
    finished = "2026-05-12T13:00:00+00:00"
    ingest_curvefit_document(session, _push_doc(
        sets=[_cf_set("s1", "cf-sess-f", "user:e")],
        sessions=[{**_cf_session("cf-sess-f"), "finished_at": finished}],
        exercises=[_cf_exercise("user:e", "E")],
    ))
    rows = session.exec(select(WorkoutSession)).all()
    assert len(rows) == 1  # not duplicated
    assert rows[0].finished_at is not None  # finished workout no longer active


def test_zero_set_session_syncs(session):
    summary = ingest_curvefit_document(session, _push_doc(
        sets=[], sessions=[_cf_session("cf-empty")], exercises=[],
    ))
    assert summary["created"]["sessions"] == 1
    assert len(session.exec(select(WorkoutSession)).all()) == 1


def test_noop_reingest_reports_no_updates(session):
    doc = _push_doc(
        sets=[_cf_set("cf-set-1", "cf-sess-1", "user:e")],
        sessions=[_cf_session("cf-sess-1")],
        exercises=[_cf_exercise("user:e", "E")],
    )
    ingest_curvefit_document(session, doc)
    summary = ingest_curvefit_document(session, doc)  # identical re-push
    assert summary["updated"]["sets"] == 0  # no cosmetic no-op churn


def test_validated_origin_rejects_injection_and_gates_localhost():
    from app.config import settings
    from app.routers.curvefit_sync import _validated_curvefit_origin

    prior = settings.curvefit_origins
    try:
        # Dev mode (no prod origins): localhost allowed, injection rejected.
        settings.curvefit_origins = ""
        assert _validated_curvefit_origin("http://localhost:7676") == "http://localhost:7676"
        assert _validated_curvefit_origin('http://localhost:1"</script><script>alert(1)</script>') is None
        assert _validated_curvefit_origin("http://localhost:7676/evil") is None
        # Prod mode: only the exact allowlist; arbitrary localhost rejected.
        settings.curvefit_origins = "https://curvefit.app"
        assert _validated_curvefit_origin("https://curvefit.app") == "https://curvefit.app"
        assert _validated_curvefit_origin("http://localhost:7676") is None
        assert _validated_curvefit_origin("https://evil.example.com") is None
    finally:
        settings.curvefit_origins = prior


def test_put_accepts_weak_etag_if_match(session, client):
    """A Cloudflare-weakened If-Match (W/"...") must still match (no 412)."""
    from app.main import app
    from app.routers.curvefit_sync import get_sync_session, get_sync_user

    class _U:
        id = "test-user"
        disabled_at = None

    app.dependency_overrides[get_sync_user] = lambda: _U()
    app.dependency_overrides[get_sync_session] = lambda: session
    try:
        strong = client.get("/api/curvefit-sync").headers["ETag"]
        weak = "W/" + strong  # what a compressing proxy sends back
        doc = _push_doc(
            sets=[_cf_set("cf-set-1", "cf-sess-1", "catalog:barbell-curl")],
            sessions=[_cf_session("cf-sess-1")],
            exercises=[_cf_exercise("catalog:barbell-curl", "Barbell Curl")],
        )
        res = client.put("/api/curvefit-sync", json=doc, headers={"If-Match": weak})
        assert res.status_code == 200
    finally:
        app.dependency_overrides.pop(get_sync_user, None)
        app.dependency_overrides.pop(get_sync_session, None)
