"""Verify init_db applies migrations and seeds default settings."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import init_db
from app.db.models import Setting


@pytest.fixture
async def file_db(monkeypatch):
    """Use a real file-based SQLite so alembic can attach to it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # alembic + create_async_engine will create it
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("SYNC_DATABASE_URL", f"sqlite:///{path}")
    # Force re-read of settings (cached singleton).
    from app.core import config as cfg

    cfg._settings = None  # type: ignore[attr-defined]
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_init_db_runs_migrations_and_seeds_defaults(file_db: str) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{file_db}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await init_db(engine, sm)

    async with sm() as session:
        rows = (await session.execute(select(Setting))).scalars().all()
        keys = {r.key for r in rows}
        assert {"default_interval_seconds", "retention_days", "max_concurrency"} <= keys

        # Verify business tables exist (alembic_version + 5 tables)
        from sqlalchemy import text

        table_rows = (
            await session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            )
        ).all()
        names = {row[0] for row in table_rows}
        assert {
            "providers",
            "models",
            "probe_results",
            "settings",
            "job_states",
            "alembic_version",
        } <= names

        # alembic_version records the head revision
        ver = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        assert ver is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_is_idempotent(file_db: str) -> None:
    """Re-running init_db on an already-migrated DB is a no-op (no schema errors)."""
    from app.core import config as cfg

    cfg._settings = None  # type: ignore[attr-defined]

    engine = create_async_engine(f"sqlite+aiosqlite:///{file_db}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await init_db(engine, sm)
    await init_db(engine, sm)  # second time should not fail

    async with sm() as session:
        from sqlalchemy import text

        # Exactly one alembic_version row
        n = (await session.execute(text("SELECT COUNT(*) FROM alembic_version"))).scalar()
        assert n == 1

    await engine.dispose()
