"""Database init helpers: create_all + seed default settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Setting
from app.db.session import Base, get_engine, get_session_maker

DEFAULT_SETTINGS: dict[str, str] = {
    "default_interval_seconds": "300",
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


async def init_db(
    engine: AsyncEngine | None = None,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    engine = engine or get_engine()
    sm = session_maker or get_session_maker()
    # Set SQLite pragmas on every new connection. WAL allows concurrent
    # readers + a single writer; busy_timeout makes the writer wait instead
    # of raising "database is locked" when APScheduler's sync jobstore and
    # the async app connections race.
    async with engine.begin() as conn:
        from sqlalchemy import text

        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as session:
        await seed_default_settings(session)


def get_default_interval() -> int:
    from app.core.config import get_settings

    return int(get_settings().default_interval_seconds)


def get_default_timeout() -> int:
    from app.core.config import get_settings

    return int(get_settings().default_timeout_seconds)
