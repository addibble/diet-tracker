"""Lightweight always-on telemetry store.

Captures one row per HTTP request (path, status, duration, DB query count,
slowest SQL + rowcount, phase breakdown, user_id) plus an event stream from
the frontend. Everything goes into ``data/telemetry.db`` — a separate SQLite
file so it can be wiped, rotated, or copied without touching app data.

Design notes:

* Sampling profilers (pyinstrument) are great for one request; this module
  instead gives **continuous** request-level metrics so an offline analyst
  (Claude, after a workout) can run aggregate queries and spot trends.
* SQLAlchemy ``Engine``-level event listeners are attached at import time so
  every engine the app creates (auth + per-user) is observed automatically.
* The current request is tracked via a ``contextvars.ContextVar`` populated
  by the middleware in :mod:`app.main`. Code in handlers can add named
  phases via :func:`phase` and they show up both in the persisted row and
  the response's ``Server-Timing`` header.

Schema (telemetry.db):

* ``requests``  — one row per HTTP request
* ``slow_queries`` — exploded list of the slowest SQL per request (for
  index-opportunity analysis)
* ``frontend_events`` — measurements reported via ``POST /api/telemetry/frontend``

Retention: we periodically prune rows older than ``RETENTION_DAYS`` to keep
the file small. Pruning runs opportunistically on every Nth insert.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

_log = logging.getLogger(__name__)

# Tunables -----------------------------------------------------------------

RETENTION_DAYS = 14
PRUNE_EVERY_N_INSERTS = 500
SLOW_QUERY_KEEP_TOP = 3  # keep the top-N slowest queries per request
SLOW_QUERY_THRESHOLD_MS = 5.0  # below this, don't even bother recording


# In-process state ---------------------------------------------------------


@dataclass
class _RequestCtx:
    request_id: str
    started_at: float
    method: str = ""
    path: str = ""
    user_id: str | None = None
    db_count: int = 0
    db_total_ms: float = 0.0
    # List of (sql, duration_ms, rowcount-or-None). Trimmed to the slowest
    # ``SLOW_QUERY_KEEP_TOP`` at finalize time.
    db_queries: list[tuple[str, float, int | None]] = field(default_factory=list)
    phases: dict[str, float] = field(default_factory=dict)


_current: ContextVar[_RequestCtx | None] = ContextVar("telemetry_request", default=None)


def current_request() -> _RequestCtx | None:
    return _current.get()


@contextmanager
def request_scope(request_id: str, method: str, path: str) -> Iterator[_RequestCtx]:
    """Establish a request scope so DB listeners + ``phase()`` can attribute."""
    ctx = _RequestCtx(request_id=request_id, started_at=time.perf_counter(), method=method, path=path)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a named phase within the current request.

    Multiple entries with the same name accumulate. No-op outside a request
    scope (e.g. background tasks), so this is safe to sprinkle anywhere.
    """
    ctx = _current.get()
    if ctx is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ctx.phases[name] = ctx.phases.get(name, 0.0) + elapsed_ms


# SQLAlchemy hooks ---------------------------------------------------------


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    context._telemetry_t0 = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    ctx = _current.get()
    if ctx is None:
        return
    t0 = getattr(context, "_telemetry_t0", None)
    if t0 is None:
        return
    duration_ms = (time.perf_counter() - t0) * 1000.0
    ctx.db_count += 1
    ctx.db_total_ms += duration_ms
    rowcount: int | None
    try:
        rc = cursor.rowcount
        rowcount = int(rc) if rc is not None and rc >= 0 else None
    except Exception:  # cursor may not support rowcount
        rowcount = None
    if duration_ms >= SLOW_QUERY_THRESHOLD_MS:
        # Truncate gnarly SQL so the telemetry DB stays small.
        sql = " ".join(statement.split())
        if len(sql) > 800:
            sql = sql[:800] + "…"
        ctx.db_queries.append((sql, duration_ms, rowcount))


# Persistence --------------------------------------------------------------


_db_lock = threading.Lock()
_db_path: Path | None = None
_inserts_since_prune = 0


def _resolve_db_path() -> Path:
    global _db_path
    if _db_path is not None:
        return _db_path
    override = os.environ.get("TELEMETRY_DB_PATH")
    if override:
        path = Path(override)
    else:
        # Mirror DB_DIR / data directory used by db_engines.
        from app.db_engines import data_root

        path = data_root() / "telemetry.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db_path(), isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    request_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    user_id TEXT,
    db_count INTEGER NOT NULL DEFAULT 0,
    db_total_ms REAL NOT NULL DEFAULT 0,
    phases_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_path ON requests(path);
CREATE INDEX IF NOT EXISTS idx_requests_duration ON requests(duration_ms);

CREATE TABLE IF NOT EXISTS slow_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_row_id INTEGER NOT NULL,
    sql TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    rowcount INTEGER,
    FOREIGN KEY (request_row_id) REFERENCES requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_slow_queries_request ON slow_queries(request_row_id);
CREATE INDEX IF NOT EXISTS idx_slow_queries_duration ON slow_queries(duration_ms);

CREATE TABLE IF NOT EXISTS frontend_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id TEXT,
    route TEXT,
    name TEXT NOT NULL,
    duration_ms REAL,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_frontend_events_ts ON frontend_events(ts);
CREATE INDEX IF NOT EXISTS idx_frontend_events_name ON frontend_events(name);
"""


def init_db() -> None:
    with _db_lock:
        with _connect() as conn:
            conn.executescript(_SCHEMA)


def _maybe_prune(conn: sqlite3.Connection) -> None:
    global _inserts_since_prune
    _inserts_since_prune += 1
    if _inserts_since_prune < PRUNE_EVERY_N_INSERTS:
        return
    _inserts_since_prune = 0
    cutoff = datetime.now(UTC).timestamp() - RETENTION_DAYS * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
    conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff_iso,))
    conn.execute("DELETE FROM frontend_events WHERE ts < ?", (cutoff_iso,))


def record_request(
    ctx: _RequestCtx,
    *,
    status: int,
    user_id: str | None,
) -> None:
    """Persist a finalized request context. Best-effort: swallow errors so a
    failing telemetry write never breaks a real response.
    """
    try:
        init_db()
        duration_ms = (time.perf_counter() - ctx.started_at) * 1000.0
        ts = datetime.now(UTC).isoformat()
        # Keep only top-N slowest queries to bound row count.
        slow = sorted(ctx.db_queries, key=lambda q: q[1], reverse=True)[:SLOW_QUERY_KEEP_TOP]
        phases_json = json.dumps(ctx.phases) if ctx.phases else None
        with _db_lock:
            with _connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO requests (ts, request_id, method, path, status, duration_ms,
                                          user_id, db_count, db_total_ms, phases_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        ctx.request_id,
                        ctx.method,
                        ctx.path,
                        status,
                        duration_ms,
                        user_id,
                        ctx.db_count,
                        ctx.db_total_ms,
                        phases_json,
                    ),
                )
                row_id = cur.lastrowid
                if slow:
                    conn.executemany(
                        """
                        INSERT INTO slow_queries (request_row_id, sql, duration_ms, rowcount)
                        VALUES (?, ?, ?, ?)
                        """,
                        [(row_id, sql, ms, rc) for sql, ms, rc in slow],
                    )
                _maybe_prune(conn)
    except Exception:  # never let telemetry break the app
        _log.exception("telemetry: failed to persist request")


def record_frontend_event(
    *,
    user_id: str | None,
    route: str | None,
    name: str,
    duration_ms: float | None,
    meta: dict[str, Any] | None,
) -> None:
    try:
        init_db()
        ts = datetime.now(UTC).isoformat()
        meta_json = json.dumps(meta) if meta else None
        with _db_lock:
            with _connect() as conn:
                conn.execute(
                    """
                    INSERT INTO frontend_events (ts, user_id, route, name, duration_ms, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ts, user_id, route, name, duration_ms, meta_json),
                )
                _maybe_prune(conn)
    except Exception:
        _log.exception("telemetry: failed to persist frontend event")


# Read-side helpers --------------------------------------------------------


def summary(*, hours: float = 24) -> dict[str, Any]:
    """Return per-endpoint aggregates over the last ``hours`` hours."""
    init_db()
    cutoff_iso = datetime.fromtimestamp(datetime.now(UTC).timestamp() - hours * 3600, tz=UTC).isoformat()
    with _db_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            endpoints = conn.execute(
                """
                SELECT path,
                       COUNT(*) AS n,
                       AVG(duration_ms) AS avg_ms,
                       MAX(duration_ms) AS max_ms,
                       AVG(db_count) AS avg_db_count,
                       AVG(db_total_ms) AS avg_db_ms,
                       SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) AS errors
                FROM requests
                WHERE ts >= ?
                GROUP BY path
                ORDER BY (AVG(duration_ms) * COUNT(*)) DESC
                LIMIT 50
                """,
                (cutoff_iso,),
            ).fetchall()
            # Approximate p95 per path with a window function.
            p95 = conn.execute(
                """
                SELECT path, duration_ms
                FROM (
                    SELECT path, duration_ms,
                           NTILE(20) OVER (PARTITION BY path ORDER BY duration_ms) AS bucket
                    FROM requests
                    WHERE ts >= ?
                )
                WHERE bucket = 19
                """,
                (cutoff_iso,),
            ).fetchall()
            p95_by_path: dict[str, float] = {}
            for row in p95:
                # The first row of bucket 19 per path is the p95 lower bound.
                p95_by_path.setdefault(row["path"], row["duration_ms"])

            slow_queries = conn.execute(
                """
                SELECT sq.sql AS sql,
                       COUNT(*) AS n,
                       AVG(sq.duration_ms) AS avg_ms,
                       MAX(sq.duration_ms) AS max_ms,
                       AVG(COALESCE(sq.rowcount, 0)) AS avg_rows,
                       MAX(COALESCE(sq.rowcount, 0)) AS max_rows
                FROM slow_queries sq
                JOIN requests r ON r.id = sq.request_row_id
                WHERE r.ts >= ?
                GROUP BY sq.sql
                ORDER BY (AVG(sq.duration_ms) * COUNT(*)) DESC
                LIMIT 25
                """,
                (cutoff_iso,),
            ).fetchall()

            frontend = conn.execute(
                """
                SELECT name,
                       COUNT(*) AS n,
                       AVG(duration_ms) AS avg_ms,
                       MAX(duration_ms) AS max_ms
                FROM frontend_events
                WHERE ts >= ? AND duration_ms IS NOT NULL
                GROUP BY name
                ORDER BY (AVG(duration_ms) * COUNT(*)) DESC
                LIMIT 25
                """,
                (cutoff_iso,),
            ).fetchall()

            # Phases are stored as a JSON map per request in
            # ``requests.phases_json``. Decode + aggregate in Python since
            # SQLite has no native JSON_EACH on every build we ship to.
            phase_rows = conn.execute(
                """
                SELECT path, phases_json
                FROM requests
                WHERE ts >= ? AND phases_json IS NOT NULL
                """,
                (cutoff_iso,),
            ).fetchall()

    phase_totals: dict[tuple[str, str], dict[str, float]] = {}
    for r in phase_rows:
        try:
            phases = json.loads(r["phases_json"])
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(phases, dict):
            continue
        for name, ms in phases.items():
            try:
                ms_f = float(ms)
            except (TypeError, ValueError):
                continue
            key = (r["path"], name)
            agg = phase_totals.setdefault(
                key, {"n": 0.0, "total_ms": 0.0, "max_ms": 0.0},
            )
            agg["n"] += 1
            agg["total_ms"] += ms_f
            agg["max_ms"] = max(agg["max_ms"], ms_f)
    phases_out = [
        {
            "path": path,
            "phase": name,
            "n": int(agg["n"]),
            "avg_ms": agg["total_ms"] / agg["n"] if agg["n"] else 0.0,
            "max_ms": agg["max_ms"],
        }
        for (path, name), agg in phase_totals.items()
    ]
    phases_out.sort(key=lambda p: p["avg_ms"] * p["n"], reverse=True)

    return {
        "window_hours": hours,
        "endpoints": [
            {
                **dict(r),
                "p95_ms": p95_by_path.get(r["path"]),
            }
            for r in endpoints
        ],
        "phases": phases_out[:50],
        "slow_queries": [dict(r) for r in slow_queries],
        "frontend_events": [dict(r) for r in frontend],
    }


def server_timing_header(ctx: _RequestCtx, total_ms: float) -> str:
    """Build a ``Server-Timing`` header from a finalized request context."""
    parts: list[str] = [f"total;dur={total_ms:.1f}"]
    if ctx.db_count:
        parts.append(f'db;desc="{ctx.db_count} queries";dur={ctx.db_total_ms:.1f}')
    for name, ms in ctx.phases.items():
        # Sanitize phase names: comma/semicolon would break the header.
        safe = name.replace(",", "_").replace(";", "_").replace('"', "")
        parts.append(f"{safe};dur={ms:.1f}")
    return ", ".join(parts)
