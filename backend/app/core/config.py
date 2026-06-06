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
    """Bootstrap-tier configuration loaded once at process start.

    There are **two** config layers in this app; both are real, and the
    split is intentional. Be deliberate about where new values land:

    * ``Settings`` (this class) — **bootstrap-only**. Read once at import
      time from environment / ``.env``. Values that the running process
      can't reasonably change without a restart go here: bind host/port,
      database URL, data directory, frontend-dist path, timezone,
      ``max_concurrency`` (APScheduler executor pool size, baked in at
      scheduler-construction time). Changing any of these means
      restarting the process.

    * ``app.services.settings`` (the ``Setting`` DB table) — **runtime
      tunables**. Read on each request, written from the Settings page.
      Probe intervals, failure-confirmation thresholds, per-provider
      rate limit, the adaptive-backoff / idle-throttle toggles all live
      here so the operator can flip them while the dashboard is open.

    Three keys are seeded into the ``settings`` table on first launch
    (``default_interval_seconds``, ``default_timeout_seconds``,
    ``retention_days``) for symmetry, but the code only reads them from
    ``Settings`` — the DB rows are vestigial. Don't add new dual-write
    keys without a stronger reason; the env file should remain the
    source of truth for any value that lives in ``Settings``.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 6200

    data_dir: Path = _BACKEND_ROOT / "data"

    database_url: str = ""  # computed in model_post_init
    sync_database_url: str = ""  # computed

    log_level: str = "INFO"
    default_interval_seconds: int = 300
    default_timeout_seconds: int = 60
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
