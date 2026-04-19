import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.auth import get_current_user, get_current_user_id
from app.database import get_session
from app.main import app


class _FakeUser:
    """Lightweight stand-in for the real User row used in tests."""

    def __init__(self, user_id: str = "test-user") -> None:
        self.id = user_id
        self.email = f"{user_id}@example.test"
        self.display_name = user_id
        self.is_admin = True


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Only create the per-user data tables; the auth tables live in the
    # auth engine and are unused in these tests.
    from app.auth_models import AUTH_TABLE_NAMES

    data_tables = [
        t for name, t in SQLModel.metadata.tables.items() if name not in AUTH_TABLE_NAMES
    ]
    SQLModel.metadata.create_all(engine, tables=data_tables)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    fake_user = _FakeUser()

    def get_session_override():
        yield session

    def get_current_user_override():
        return fake_user

    def get_current_user_id_override():
        return fake_user.id

    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_user] = get_current_user_override
    app.dependency_overrides[get_current_user_id] = get_current_user_id_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
