"""Application configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ root, derived from this file's location (app/core/config.py → backend/).
# All data files live under here so paths are stable regardless of CWD.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_frontend_dist() -> Path:
    """Pick the first existing candidate for the built frontend.

    Order matches the three layouts this project actually ships:
      * repo checkout: <repo>/frontend/dist           (sibling of backend/)
      * local dev:     <backend>/../frontend/dist     (same as above, explicit)
      * docker image:  <backend>/frontend_dist        (Dockerfile copies it there)
    Falls back to the first candidate so .exists() downstream returns False
    and the placeholder branch runs (better than crashing on import).
    """
    candidates: tuple[Path, ...] = (
        _BACKEND_ROOT / "frontend_dist",
        _BACKEND_ROOT.parent / "frontend" / "dist",
        _BACKEND_ROOT / ".." / "frontend" / "dist",
    )
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 6200

    data_dir: Path = _BACKEND_ROOT / "data"

    database_url: str = ""  # computed in model_post_init
    sync_database_url: str = ""  # computed

    log_level: str = "INFO"
    default_interval_seconds: int = 300
    default_timeout_seconds: int = 30
    max_concurrency: int = 10
    retention_days: int = 30
    probe_prompt: str = "hi"
    probe_max_tokens: int = 1
    tz: str = "Asia/Shanghai"

    frontend_dist: Path = _resolve_frontend_dist()

    def model_post_init(self, __context):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{self.data_dir}/llm_usability.db"
        if not self.sync_database_url:
            self.sync_database_url = f"sqlite:///{self.data_dir}/llm_usability.db"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
