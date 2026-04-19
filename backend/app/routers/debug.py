"""Debug endpoint for remote log tailing, protected by HTTP Basic Auth."""

import logging
import secrets
from collections import deque
from contextvars import ContextVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

router = APIRouter(prefix="/api/debug", tags=["debug"])
security = HTTPBasic()

# Ring buffer that captures recent log lines
LOG_BUFFER_SIZE = 1000

# Per-request user context — populated by the auth layer so log records can be
# tagged with which user triggered them.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


class UserContextFilter(logging.Filter):
    """Annotate log records with the current user_id from contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.user_id = current_user_id.get() or "-"
        return True


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = LOG_BUFFER_SIZE):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        self.buffer.append(self.format(record))


# Singleton handler — attached to root logger in main.py
ring_handler = RingBufferHandler()
ring_handler.addFilter(UserContextFilter())
ring_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] [user=%(user_id)s] %(message)s"
    )
)


def _verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not settings.logs_user or not settings.logs_password:
        raise HTTPException(status_code=503, detail="Log endpoint not configured")
    user_ok = secrets.compare_digest(credentials.username, settings.logs_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.logs_password)
    if not user_ok or not pass_ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


@router.get("/logs")
def get_logs(
    _user: str = Depends(_verify_basic_auth),
    lines: int = Query(default=100, ge=1, le=LOG_BUFFER_SIZE),
    level: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
):
    """Return recent backend log lines as plain text."""
    entries = list(ring_handler.buffer)
    if level:
        level_upper = level.upper()
        entries = [e for e in entries if f" {level_upper} " in e]
    if user_id:
        entries = [e for e in entries if f"[user={user_id}]" in e]
    entries = entries[-lines:]
    return Response(content="\n".join(entries), media_type="text/plain")
