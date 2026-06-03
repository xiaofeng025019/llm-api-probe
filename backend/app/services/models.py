"""Model service: upsert discovered models, patch enabled/favorite, prune stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, ModelType
from app.probers.types import DiscoveredModel
from app.schemas.api import ModelPatch


async def list_models(session: AsyncSession, provider_id: int) -> list[Model]:
    res = await session.execute(
        select(Model).where(Model.provider_id == provider_id).order_by(Model.model_id)
    )
    return list(res.scalars().all())


async def get_model(session: AsyncSession, model_id: int) -> Model | None:
    return await session.get(Model, model_id)


async def patch_model(session: AsyncSession, model_id: int, data: ModelPatch) -> Model | None:
    m = await session.get(Model, model_id)
    if m is None:
        return None
    for field_, value in data.model_dump(exclude_unset=True).items():
        setattr(m, field_, value)
    await session.commit()
    await session.refresh(m)
    return m


async def upsert_discovered(
    session: AsyncSession, provider_id: int, discovered: list[DiscoveredModel]
) -> list[Model]:
    """Insert new models; update last_seen_at for existing; do not delete yet."""
    now = datetime.now(UTC)
    existing_res = await session.execute(select(Model).where(Model.provider_id == provider_id))
    existing = {m.model_id: m for m in existing_res.scalars().all()}
    result: list[Model] = []
    for d in discovered:
        m = existing.get(d.model_id)
        if m is None:
            m = Model(
                provider_id=provider_id,
                model_id=d.model_id,
                display_name=d.display_name,
                type=d.type if d.type != ModelType.unknown else ModelType.chat,
                enabled=True,
                is_favorite=False,
                last_seen_at=now,
            )
            session.add(m)
        else:
            m.last_seen_at = now
            if d.display_name and not m.display_name:
                m.display_name = d.display_name
            if m.type == ModelType.unknown and d.type != ModelType.unknown:
                m.type = d.type
        result.append(m)
    await session.commit()
    for m in result:
        await session.refresh(m)
    return result


async def disable_stale(session: AsyncSession, days: int = 7) -> int:
    # SQLite returns naive datetimes; compare with naive "now".
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    res = await session.execute(select(Model).where(Model.last_seen_at < cutoff, Model.enabled))
    count = 0
    for m in res.scalars().all():
        m.enabled = False
        count += 1
    await session.commit()
    return count
