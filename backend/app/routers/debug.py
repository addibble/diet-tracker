"""Debug endpoint for remote log tailing, protected by HTTP Basic Auth."""

import logging
import secrets
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

router = APIRouter(prefix="/api/debug", tags=["debug"])
security = HTTPBasic()

# Ring buffer that captures recent log lines
LOG_BUFFER_SIZE = 1000


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = LOG_BUFFER_SIZE):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        self.buffer.append(self.format(record))


# Singleton handler — attached to root logger in main.py
ring_handler = RingBufferHandler()
ring_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))


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
):
    """Return recent backend log lines as plain text."""
    entries = list(ring_handler.buffer)
    if level:
        level_upper = level.upper()
        entries = [e for e in entries if f" {level_upper} " in e]
    entries = entries[-lines:]
    return Response(content="\n".join(entries), media_type="text/plain")
