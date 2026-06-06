"""Tests for the partial unique index on (provider_id, model_id) WHERE deleted_at IS NULL.

Without this constraint, two concurrent `POST /providers/{id}/models`
calls with the same model_id can both pass their `existing is None`
check and both insert a row — producing duplicate active rows under
the same (provider, model_id). The list_models flow then merges
stats from both rows into a confused dashboard view.

The fix: a partial unique index on (provider_id, model_id) where
deleted_at IS NULL. Soft-deleted rows are exempt (they may share
the same (provider_id, model_id) as a now-active row created later).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Model, ModelType, Provider, ProviderKind
from app.db.session import Base


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def provider_id(sm):
    async with sm() as s:
        p = Provider(
            name="p",
            kind=ProviderKind.openai,
            base_url="https://x",
            api_key="k",
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        yield p.id


@pytest.mark.asyncio
async def test_duplicate_active_model_id_raises_integrity_error(sm, provider_id) -> None:
    """Two active rows with the same (provider_id, model_id) must be rejected
    by the DB itself, not just by application-level check-then-insert."""
    async with sm() as s:
        s.add(
            Model(
                provider_id=provider_id,
                model_id="gpt-4o",
                type=ModelType.chat,
                enabled=True,
            )
        )
        await s.commit()
        s.add(
            Model(
                provider_id=provider_id,
                model_id="gpt-4o",
                type=ModelType.chat,
                enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_soft_deleted_row_does_not_block_new_active_row(sm, provider_id) -> None:
    """A soft-deleted (deleted_at != NULL) row with the same (provider_id, model_id)
    must NOT block a new active row from being inserted — soft-deleted rows are
    exempt from the partial unique constraint."""
    from datetime import UTC, datetime

    async with sm() as s:
        # Soft-delete the first row
        m_deleted = Model(
            provider_id=provider_id,
            model_id="gpt-4o",
            type=ModelType.chat,
            enabled=True,
            deleted_at=datetime.now(UTC),
        )
        s.add(m_deleted)
        await s.commit()

        # Create an active row with the same (provider_id, model_id) — must succeed
        m_active = Model(
            provider_id=provider_id,
            model_id="gpt-4o",
            type=ModelType.chat,
            enabled=True,
        )
        s.add(m_active)
        await s.commit()

        # Verify both rows coexist (1 active + 1 deleted)
        rows = list((await s.execute(select(Model).where(Model.provider_id == provider_id))).scalars().all())
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_different_providers_with_same_model_id_allowed(sm) -> None:
    """The constraint is per-provider; same model_id across different providers is fine."""
    async with sm() as s:
        p1 = Provider(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k")
        p2 = Provider(name="p2", kind=ProviderKind.openai, base_url="https://y", api_key="k")
        s.add_all([p1, p2])
        await s.commit()
        await s.refresh(p1)
        await s.refresh(p2)
        s.add_all(
            [
                Model(provider_id=p1.id, model_id="gpt-4o", type=ModelType.chat, enabled=True),
                Model(provider_id=p2.id, model_id="gpt-4o", type=ModelType.chat, enabled=True),
            ]
        )
        await s.commit()  # must NOT raise
