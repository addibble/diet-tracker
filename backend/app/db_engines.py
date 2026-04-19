"""Engine factories for the multi-user deployment.

Two kinds of engines:

1. The **auth engine** — a single SQLite DB holding users, WebAuthn credentials,
   server-side sessions, and invites. Tables are scoped to ``AUTH_TABLE_NAMES``
   from ``app.auth_models``.

2. **Per-user data engines** — one SQLite DB per user, holding the diet +
   workout tables (everything in ``app.models``). Engines are cached and each
   user's DB is lazily created + migrated on first access.

``data/`` layout::

    data/
      auth.db
      users/<user_id>/diet_tracker.db
      legacy/prod_backup_<ts>.db
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

logger = logging.getLogger(__name__)


def _data_root() -> Path:
    # settings.database_url historically points at data/diet_tracker.db.
    # We reuse that file's parent as the root for auth.db + users/.
    if settings.database_url.startswith("sqlite:///"):
        legacy_path = Path(settings.database_url.split("///", 1)[1])
        return legacy_path.parent.resolve()
    return Path("./data").resolve()


def data_root() -> Path:
    root = _data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def auth_db_path() -> Path:
    return data_root() / "auth.db"


def user_db_dir(user_id: str) -> Path:
    return data_root() / "users" / user_id


def user_db_path(user_id: str) -> Path:
    return user_db_dir(user_id) / "diet_tracker.db"


def legacy_db_path() -> Path:
    return data_root() / "diet_tracker.db"


def legacy_archive_dir() -> Path:
    return data_root() / "legacy"


# ── Auth engine (singleton) ────────────────────────────────────────────────

_auth_engine: Engine | None = None
_auth_engine_lock = threading.Lock()


def auth_engine() -> Engine:
    global _auth_engine
    if _auth_engine is not None:
        return _auth_engine
    with _auth_engine_lock:
        if _auth_engine is None:
            path = auth_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{path}", echo=False)
            ensure_auth_db_ready(engine)
            _auth_engine = engine
    return _auth_engine


def ensure_auth_db_ready(engine: Engine) -> None:
    """Create auth tables on the auth engine (idempotent)."""

    import app.auth_models  # noqa: F401 — register tables with SQLModel.metadata
    from app.auth_models import AUTH_TABLE_NAMES

    tables = [
        t for name, t in SQLModel.metadata.tables.items() if name in AUTH_TABLE_NAMES
    ]
    SQLModel.metadata.create_all(engine, tables=tables)


def auth_session() -> Session:
    """Return a new ``sqlmodel.Session`` bound to the auth engine.

    Caller is responsible for ``.close()`` / ``with`` block.
    """

    return Session(auth_engine())


# ── Per-user data engines ──────────────────────────────────────────────────

# Cap on how many user engines we hold open. SQLite connection-pool overhead
# is modest, but we still dispose evicted engines to release fds.
MAX_USER_ENGINES = 32

_user_engines: OrderedDict[str, Engine] = OrderedDict()
_user_engines_lock = threading.Lock()
_user_migrate_locks: dict[str, threading.Lock] = {}


def _get_or_make_migrate_lock(user_id: str) -> threading.Lock:
    with _user_engines_lock:
        lock = _user_migrate_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _user_migrate_locks[user_id] = lock
        return lock


def user_engine(user_id: str) -> Engine:
    """Return (creating if needed) the SQLite engine for *user_id*.

    Also runs the per-user data migrations/seeds on first access.
    """

    with _user_engines_lock:
        eng = _user_engines.get(user_id)
        if eng is not None:
            # Touch LRU order.
            _user_engines.move_to_end(user_id)
            return eng

    # Migration must be serialized per user but concurrent across users.
    migrate_lock = _get_or_make_migrate_lock(user_id)
    with migrate_lock:
        with _user_engines_lock:
            eng = _user_engines.get(user_id)
            if eng is not None:
                _user_engines.move_to_end(user_id)
                return eng

        path = user_db_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{path}", echo=False)

        from app.database import ensure_runtime_db_ready

        ensure_runtime_db_ready(eng)

        with _user_engines_lock:
            _user_engines[user_id] = eng
            _user_engines.move_to_end(user_id)
            while len(_user_engines) > MAX_USER_ENGINES:
                evicted_id, evicted_eng = _user_engines.popitem(last=False)
                logger.info("Evicting user engine for %s", evicted_id)
                try:
                    evicted_eng.dispose()
                except Exception:  # noqa: BLE001
                    logger.exception("Failed disposing engine for %s", evicted_id)
        return eng


def dispose_user_engine(user_id: str) -> None:
    """Drop the cached engine for *user_id* (e.g. before deleting their DB)."""

    with _user_engines_lock:
        eng = _user_engines.pop(user_id, None)
    if eng is not None:
        try:
            eng.dispose()
        except Exception:  # noqa: BLE001
            logger.exception("Failed disposing engine for %s", user_id)


def new_user_session(user_id: str) -> Session:
    return Session(user_engine(user_id))


def user_exists_on_disk(user_id: str) -> bool:
    return user_db_path(user_id).exists()
