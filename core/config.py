"""Application settings, loaded from defaults and optional .env overrides."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the folder that contains core/, ui/, main.py, etc.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All tunable settings. Environment variables use the CT_ prefix."""

    model_config = SettingsConfigDict(
        env_prefix="CT_",
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Paths ---------------------------------------------------------------
    data_dir: Path = BASE_DIR / "data"
    campaign_file: Path = BASE_DIR / "campaign" / "courses.json"

    # --- Logging -------------------------------------------------------------
    log_level: str = "INFO"

    # --- Networking (used in a later module) -----------------------------------
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {value}")
        return value

    # --- Derived paths ---------------------------------------------------------
    @property
    def database_path(self) -> Path:
        return self.data_dir / "tracker.db"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "tracker.log"

    def ensure_directories(self) -> None:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (created on first call)."""
    return Settings()