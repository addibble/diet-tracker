"""Database export endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.auth_models import User
from app.db_engines import user_db_path

router = APIRouter(prefix="/api/database", tags=["database"])


@router.get("/download")
def download_database(user: User = Depends(get_current_user)):
    """Download the caller's SQLite database file."""
    db_path = user_db_path(user.id)

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found")

    return FileResponse(
        path=db_path,
        filename="diet_tracker.db",
        media_type="application/octet-stream",
    )
