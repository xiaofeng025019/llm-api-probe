"""Database init helpers: run alembic migrations + seed default settings."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Setting
from app.db.session import get_engine, get_session_maker

log = logging.getLogger(__name__)


DEFAULT_SETTINGS: dict[str, str] = {
    "default_interval_seconds": "300",
    "favorite_model_interval_seconds": "300",
    "regular_model_interval_seconds": "600",
    "default_timeout_seconds": "30",
    "max_concurrency": "10",
    "retention_days": "30",
}


async def seed_default_settings(session: AsyncSession) -> None:
    for k, v in DEFAULT_SETTINGS.items():
        existing = await session.scalar(select(Setting).where(Setting.key == k))
        if existing is None:
            session.add(Setting(key=k, value=v))
    await session.commit()


def _run_alembic_upgrade() -> None:
    """Run `alembic upgrade head` from the backend CWD. Sync (Alembic doesn't
    need async for SQLite DDL). Raises on any migration failure."""
    from alembic.config import Config

    from alembic import command

    backend_root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")


async def _ensure_wal(engine: AsyncEngine) -> None:
    """Set SQLite pragmas on the live engine. Migrations also use WAL but
    we want the async app's connections to honor busy_timeout too."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))


async def init_db(
    engine: AsyncEngine | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Bring the database up to the latest schema and seed default settings.

    On first start this applies all migrations. On subsequent starts it is a
    no-op if the schema is already current. If migrations fail, the
    exception is re-raised so the lifespan handler can surface it via
    uvicorn's startup failure path.
    """
    engine = engine or get_engine()
    sm = session_maker or get_session_maker()

    # Migrations run on a sync engine (Alembic's design). Keep this
    # synchronous: running Alembic in a worker thread can leave local SQLite
    # startup stuck before the app begins accepting requests.
    try:
        _run_alembic_upgrade()
    except Exception:
        log.exception("alembic upgrade head failed")
        raise

    await _ensure_wal(engine)
    async with sm() as session:
        await seed_default_settings(session)


def get_default_interval() -> int:
    from app.core.config import get_settings

    return int(get_settings().default_interval_seconds)


def get_default_timeout() -> int:
    from app.core.config import get_settings

    return int(get_settings().default_timeout_seconds)
