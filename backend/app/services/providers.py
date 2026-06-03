"""Provider service: CRUD on providers."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Provider
from app.schemas.api import ProviderCreate, ProviderPatch


async def list_providers(session: AsyncSession) -> list[Provider]:
    res = await session.execute(select(Provider).order_by(Provider.id))
    return list(res.scalars().all())


async def get_provider(session: AsyncSession, provider_id: int) -> Provider | None:
    return await session.get(Provider, provider_id)


async def create_provider(session: AsyncSession, data: ProviderCreate) -> Provider:
    p = Provider(
        name=data.name,
        kind=data.kind,
        base_url=data.base_url,
        api_key=data.api_key,
        proxy=data.proxy,
        enabled=data.enabled,
        interval_seconds=data.interval_seconds,
        timeout_seconds=data.timeout_seconds,
        headers_json=data.headers_json,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return p


async def patch_provider(session: AsyncSession, provider_id: int, data: ProviderPatch) -> Provider | None:
    p = await session.get(Provider, provider_id)
    if p is None:
        return None
    for field_, value in data.model_dump(exclude_unset=True).items():
        setattr(p, field_, value)
    await session.commit()
    await session.refresh(p)
    return p


async def delete_provider(session: AsyncSession, provider_id: int) -> bool:
    res = await session.execute(delete(Provider).where(Provider.id == provider_id))
    await session.commit()
    return getattr(res, "rowcount", 0) > 0
