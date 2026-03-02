from fastapi import APIRouter, Cookie, HTTPException, Response
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

serializer = URLSafeSerializer(settings.secret_key)
COOKIE_NAME = "session"


class LoginRequest(BaseModel):
    password: str


def get_current_user(session: str | None = Cookie(default=None)) -> str:
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = serializer.loads(session)
        if data.get("authenticated"):
            return "user"
    except BadSignature:
        pass
    raise HTTPException(status_code=401, detail="Invalid session")


@router.post("/login")
def login(request: LoginRequest, response: Response):
    if request.password != settings.app_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = serializer.dumps({"authenticated": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    return {"status": "ok"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME)
    return {"status": "ok"}
