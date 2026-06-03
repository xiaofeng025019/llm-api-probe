"""Application configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 8000

    data_dir: Path = Path("./data")

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

    frontend_dist: Path = Path("../frontend/dist")

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
