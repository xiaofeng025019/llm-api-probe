"""Setting service: KV config persisted in the ``settings`` DB table.

This is the **runtime tunable** layer. Counterpart: ``app.core.config``
holds the bootstrap layer (host/port/db URL/data dir) — see that file's
docstring for the split rule. Anything an operator should be able to
flip from the Settings page lives here, not there.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

FAVORITE_MODEL_INTERVAL_KEY = "favorite_model_interval_seconds"
REGULAR_MODEL_INTERVAL_KEY = "regular_model_interval_seconds"
FAVORITE_MODEL_FAILURE_CONFIRMATIONS_KEY = "favorite_model_failure_confirmations"
REGULAR_MODEL_FAILURE_CONFIRMATIONS_KEY = "regular_model_failure_confirmations"
PROVIDER_RATE_LIMIT_KEY = "provider_rate_limit_per_minute"
# Toggles for the cost-saving probe scheduling features (see
# app/core/scheduler.py). Both default ON so the saving kicks in
# automatically; flip either OFF to revert to the old behaviour for
# debugging. Stored as "true"/"false" strings to match every other
# Setting row (no JSON, keep the table dumb-and-greppable).
ADAPTIVE_BACKOFF_ENABLED_KEY = "adaptive_backoff_enabled"
IDLE_THROTTLE_ENABLED_KEY = "idle_throttle_enabled"
DEFAULT_ADAPTIVE_BACKOFF_ENABLED = True
DEFAULT_IDLE_THROTTLE_ENABLED = True
DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS = 60
# Regular models are barely used (the user marked favorites for the
# ones they actually care about), so we probe them much less often
# than favorites. 15 minutes is a reasonable upper bound: a rarely-
# used model that's gone offline is still discovered in time for
# the next user-initiated check, without burning upstream tokens.
DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS = 900
DEFAULT_FAVORITE_MODEL_FAILURE_CONFIRMATIONS = 2
DEFAULT_REGULAR_MODEL_FAILURE_CONFIRMATIONS = 3
# Hard ceiling on probes-per-minute to one provider. Prevents the
# bursty "Refresh all" + random sweep from overwhelming upstream
# (which often rate-limits or flags monitoring traffic). Exceeding
# this turns the next probe into a synthetic `rate_limited` outcome
# without actually hitting the upstream.
DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE = 20


async def list_settings(session: AsyncSession) -> list[Setting]:
    res = await session.execute(select(Setting).order_by(Setting.key))
    return list(res.scalars().all())


async def get_setting(session: AsyncSession, key: str) -> str | None:
    s = await session.get(Setting, key)
    return s.value if s else None


async def get_int_setting(
    session: AsyncSession,
    key: str,
    default: int,
    minimum: int = 10,
    maximum: int | None = None,
) -> int:
    value = await get_setting(session, key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if maximum is not None:
        return max(minimum, min(parsed, maximum))
    return max(minimum, parsed)


async def get_bool_setting(session: AsyncSession, key: str, default: bool) -> bool:
    """Read a boolean setting. Accepts "true"/"1"/"yes"/"on" (case-insensitive)
    as True; anything else as False. Missing key returns ``default``."""
    value = await get_setting(session, key)
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


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
