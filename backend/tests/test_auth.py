from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app


def test_login_success():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    # Don't override auth — test real auth flow
    app.dependency_overrides.pop(
        __import__("app.auth", fromlist=["get_current_user"]).get_current_user, None
    )
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"password": "changeme"})
    assert resp.status_code == 200
    assert "session" in resp.cookies
    app.dependency_overrides.clear()


def test_login_failure():
    client = TestClient(app)
    app.dependency_overrides.clear()
    resp = client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


def test_protected_endpoint_without_auth():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides = {get_session: override}
    client = TestClient(app)
    resp = client.get("/api/foods")
    assert resp.status_code == 401
    app.dependency_overrides.clear()
