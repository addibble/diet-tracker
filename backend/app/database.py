from collections.abc import Generator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(settings.database_url, echo=False)


def create_db_and_tables():
    # Ensure the directory for the SQLite file exists
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _migrate_add_columns()
    _seed_data()


def _migrate_add_columns():
    """Add new columns to existing tables (no Alembic)."""
    insp = inspect(engine)
    if "foods" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("foods")}
        if "brand" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE foods ADD COLUMN brand TEXT"))
                conn.commit()


def _seed_data():
    """Seed reference data after table creation."""
    from app.seed_tissues import seed_tissues

    with Session(engine) as session:
        seed_tissues(session)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
