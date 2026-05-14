"""Tests for app.telemetry — request capture, DB query attribution, summary."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, text
from sqlmodel.pool import StaticPool

from app import telemetry


@pytest.fixture(autouse=True)
def isolated_telemetry_db(monkeypatch: pytest.MonkeyPatch):
    """Point telemetry at a temp DB and reset the module-level cache."""
    tmp = Path(tempfile.mkdtemp()) / "telemetry.db"
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(tmp))
    # Reset the cached path so the override takes effect.
    monkeypatch.setattr(telemetry, "_db_path", None)
    monkeypatch.setattr(telemetry, "_inserts_since_prune", 0)
    yield tmp
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass


def test_phase_outside_request_is_noop():
    # Must not raise when no request scope is active.
    with telemetry.phase("foo"):
        pass


def test_request_scope_records_phases_and_db_queries():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with telemetry.request_scope("rid", "GET", "/api/test") as ctx:
        with telemetry.phase("work"):
            with Session(engine) as s:
                s.exec(text("SELECT 1"))
                s.exec(text("SELECT 2"))

    assert ctx.db_count == 2
    assert "work" in ctx.phases
    assert ctx.phases["work"] >= 0.0

    telemetry.record_request(ctx, status=200, user_id="alice")

    summary = telemetry.summary(hours=1)
    paths = [e["path"] for e in summary["endpoints"]]
    assert "/api/test" in paths
    endpoint = next(e for e in summary["endpoints"] if e["path"] == "/api/test")
    assert endpoint["n"] == 1
    assert endpoint["avg_db_count"] == 2


def test_slow_query_captured_for_index_analysis(monkeypatch: pytest.MonkeyPatch):
    # Force the threshold low enough that any query counts as slow.
    monkeypatch.setattr(telemetry, "SLOW_QUERY_THRESHOLD_MS", 0.0)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with telemetry.request_scope("rid", "GET", "/api/slow") as ctx:
        with Session(engine) as s:
            s.exec(text("SELECT 1 AS thing"))
    telemetry.record_request(ctx, status=200, user_id=None)

    summary = telemetry.summary(hours=1)
    sqls = [q["sql"] for q in summary["slow_queries"]]
    assert any("SELECT 1" in s for s in sqls)


def test_record_frontend_event_persists_and_aggregates():
    telemetry.record_frontend_event(
        user_id="alice",
        route="/training",
        name="api:GET /workout-sessions/:id/beta-evolution",
        duration_ms=42.0,
        meta={"status": 200},
    )
    telemetry.record_frontend_event(
        user_id="alice",
        route="/training",
        name="api:GET /workout-sessions/:id/beta-evolution",
        duration_ms=58.0,
        meta=None,
    )
    summary = telemetry.summary(hours=1)
    names = [e["name"] for e in summary["frontend_events"]]
    assert any("beta-evolution" in n for n in names)


def test_server_timing_header_format():
    with telemetry.request_scope("rid", "GET", "/api/test") as ctx:
        ctx.db_count = 3
        ctx.db_total_ms = 12.5
        ctx.phases["compute"] = 4.2
    header = telemetry.server_timing_header(ctx, total_ms=20.0)
    assert header.startswith("total;dur=20.0")
    assert 'db;desc="3 queries";dur=12.5' in header
    assert "compute;dur=4.2" in header
