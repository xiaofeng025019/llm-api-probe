"""Setting service: KV config persisted in settings table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

FAVORITE_MODEL_INTERVAL_KEY = "favorite_model_interval_seconds"
REGULAR_MODEL_INTERVAL_KEY = "regular_model_interval_seconds"
FAVORITE_MODEL_FAILURE_CONFIRMATIONS_KEY = "favorite_model_failure_confirmations"
REGULAR_MODEL_FAILURE_CONFIRMATIONS_KEY = "regular_model_failure_confirmations"
DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS = 300
# Non-favorite models are checked less often than favorites to keep
# upstream API volume reasonable, but not so long that the dashboard
# has to wait many minutes for signal. 120s strikes a balance: signal
# within 2 minutes, ~30 calls/model/hour worst case.
DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS = 120
DEFAULT_FAVORITE_MODEL_FAILURE_CONFIRMATIONS = 2
DEFAULT_REGULAR_MODEL_FAILURE_CONFIRMATIONS = 3


async def list_settings(session: AsyncSession) -> list[Setting]:
    res = await session.execute(select(Setting).order_by(Setting.key))
    return list(res.scalars().all())


async def get_setting(session: AsyncSession, key: str) -> str | None:
    s = await session.get(Setting, key)
    return s.value if s else None


async def get_int_setting(session: AsyncSession, key: str, default: int, minimum: int = 10) -> int:
    value = await get_setting(session, key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


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
