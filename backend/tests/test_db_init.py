"""Verify init_db creates tables and seeds default settings on an in-memory engine."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import init_db
from app.db.models import Setting
from app.db.session import Base


@pytest.mark.asyncio
async def test_init_db_creates_tables_and_seeds_defaults() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await init_db(engine, sm)

    for table in ("providers", "models", "probe_results", "settings", "job_states"):
        assert table in Base.metadata.tables

    async with sm() as session:
        rows = (await session.execute(select(Setting))).scalars().all()
        keys = {r.key for r in rows}
        assert {"default_interval_seconds", "retention_days", "max_concurrency"} <= keys
