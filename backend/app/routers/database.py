"""Database export endpoint."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import settings

router = APIRouter(prefix="/api/database", tags=["database"])


@router.get("/download")
def download_database(user: str = Depends(get_current_user)):
    """Download the entire SQLite database file."""
    db_path = Path(settings.database_url.split("///")[-1])
    
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found")
    
    return FileResponse(
        path=db_path,
        filename="diet_tracker.db",
        media_type="application/octet-stream",
    )
