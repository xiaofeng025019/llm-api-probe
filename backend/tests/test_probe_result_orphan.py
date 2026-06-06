"""Tests for ProbeResult null-safety when the provider was hard-deleted.

The `ProbeResult.provider_id` FK is `ON DELETE SET NULL`, so hard-
deleting a provider preserves the historical probe_results rows
with `provider_id=NULL`. The `provider_rel` relationship attribute
then returns None.

The Pydantic schema `ProbeResultOut` must be able to validate such
a row without crashing — the `provider_uuid` / `provider_name`
fields fall back to the `provider_uuid_at_probe` /
`provider_name_at_probe` snapshots.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.models import ProbeResult, ProbeTarget, Provider, ProviderKind
from app.db.session import Base
from app.schemas.api import ProbeResultOut


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite requires explicit PRAGMA to enforce FK constraints
        # (including ON DELETE SET NULL). Production code enables this
        # via the event listener in app/db/session.py.
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_probe_result_out_validates_when_provider_rel_is_none(sm) -> None:
    """A ProbeResult with provider_id=NULL (provider hard-deleted) must
    not crash ProbeResultOut.model_validate. The output uses the
    `provider_uuid_at_probe` / `provider_name_at_probe` snapshots."""
    provider_uuid_snapshot = _uuid.uuid4()
    async with sm() as s:
        p = Provider(
            name="p-soon-to-be-deleted",
            kind=ProviderKind.openai,
            base_url="https://x",
            api_key="k",
            uuid_id=provider_uuid_snapshot,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)

        # Insert a probe result attached to the provider.
        pr = ProbeResult(
            provider_id=p.id,
            model_id=None,
            target=ProbeTarget.list_models,
            success=True,
            checked_at=datetime.now(UTC),
            provider_name_at_probe="p-soon-to-be-deleted",
            model_id_at_probe=None,
            provider_uuid_at_probe=provider_uuid_snapshot,
            model_uuid_at_probe=None,
            http_status=200,
            latency_ms=100,
        )
        s.add(pr)
        await s.commit()
        await s.refresh(pr)

        # Hard-delete the provider via raw SQL (bypasses the ORM
        # cascade interception). The FK is ON DELETE SET NULL so the
        # probe_result row stays with provider_id=NULL.
        await s.execute(text("DELETE FROM providers WHERE id = :id"), {"id": p.id})
        await s.commit()

    # Re-fetch the probe result in a fresh session
    async with sm() as s:
        rows = list(
            (await s.execute(select(ProbeResult).options(selectinload(ProbeResult.provider_rel))))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        # provider_rel is now None (FK was SET NULL)
        assert row.provider_rel is None
        assert row.provider_id is None
        # The snapshot fields still carry the original identity
        assert row.provider_uuid_at_probe == provider_uuid_snapshot
        assert row.provider_name_at_probe == "p-soon-to-be-deleted"

        # The key contract: ProbeResultOut.model_validate must work.
        # Without the null-safe property, this would raise AttributeError
        # on `provider_rel.uuid_id` when provider_rel is None.
        out = ProbeResultOut.model_validate(row)
        assert out.provider_id == provider_uuid_snapshot
        assert out.provider_name_at_probe == "p-soon-to-be-deleted"
        assert out.success is True
