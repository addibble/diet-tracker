from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings

# Look for .env in the project root (parent of backend/)
_env_file = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    secret_key: str = "change-this-to-a-random-string"
    database_url: str = "sqlite:///./data/diet_tracker.db"
    openrouter_api_key: str | None = None
    default_timezone: str = "America/Denver"
    logs_user: str = ""
    logs_password: str = ""

    # Multi-user auth / WebAuthn ----------------------------------------
    admin_email: str | None = None  # bootstrap admin email on first boot
    webauthn_rp_id: str = "localhost"  # production domain, e.g. diet.example.com
    webauthn_rp_name: str = "Diet Tracker"
    webauthn_origin: str = "http://localhost:5173"  # full origin incl. scheme
    session_cookie_name: str = "session"
    session_ttl_days: int = 30
    invite_ttl_days: int = 7
    webauthn_challenge_ttl_seconds: int = 300
    cookie_secure: bool = False  # set True in production

    # CurveFit storage-sync ---------------------------------------------
    # Comma-separated list of allowed CurveFit origins for CORS (the sync API
    # is called cross-origin with a bearer token). e.g.
    # "https://curvefit.app,https://www.curvefit.app".
    curvefit_origins: str = ""

    model_config = {
        "env_file": str(_env_file) if _env_file.exists() else ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def curvefit_origins_list(self) -> list[str]:
        return [o.strip() for o in self.curvefit_origins.split(",") if o.strip()]


settings = Settings()


def user_today() -> date:
    """Return today's date in the user's configured timezone."""
    return datetime.now(ZoneInfo(settings.default_timezone)).date()
