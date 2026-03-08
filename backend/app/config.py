from pathlib import Path

from pydantic_settings import BaseSettings

# Look for .env in the project root (parent of backend/)
_env_file = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_password: str = "changeme"
    secret_key: str = "change-this-to-a-random-string"
    database_url: str = "sqlite:///./data/diet_tracker.db"
    openrouter_api_key: str | None = None
    api_token: str | None = None  # Bearer token for Shortcuts/external integrations
    logs_user: str = ""
    logs_password: str = ""

    model_config = {
        "env_file": str(_env_file) if _env_file.exists() else ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
