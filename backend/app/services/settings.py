"""Setting service: KV config persisted in settings table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting


async def list_settings(session: AsyncSession) -> list[Setting]:
    res = await session.execute(select(Setting).order_by(Setting.key))
    return list(res.scalars().all())


async def get_setting(session: AsyncSession, key: str) -> str | None:
    s = await session.get(Setting, key)
    return s.value if s else None


async def upsert_settings(session: AsyncSession, items: dict[str, str]) -> list[Setting]:
    out: list[Setting] = []
    for k, v in items.items():
        s = await session.get(Setting, k)
        if s is None:
            s = Setting(key=k, value=v)
            session.add(s)
        else:
            s.value = v
        out.append(s)
    await session.commit()
    for s in out:
        await session.refresh(s)
    return out
