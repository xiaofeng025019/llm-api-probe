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
    # Validate well-known keys at the service layer. The Settings page
    # previously bound every row to a free-text input, so the user
    # could submit "abc" for an integer setting; the backend would
    # accept it, `get_int_setting` would silently fall back to the
    # default on parse error, and the dashboard would show the
    # default with no indication that the save was ignored. Now we
    # raise ValueError early so the API surface returns 400 + a
    # useful message instead.
    for k, v in items.items():
        _validate_setting_value(k, v)
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


# Per-key value validators. Unknown keys are accepted as-is (the
# Settings table is a generic KV store; only the well-known runtime
# tunables are constrained here). Each validator raises ValueError
# with a user-readable message; the API layer's ValueError handler
# maps that to a 400 with the message in the error envelope.
_TRUTHY_STRINGS = {"true", "false", "1", "0", "yes", "no", "on", "off"}


def _validate_setting_value(key: str, value: str) -> None:
    if key in _INT_SETTING_BOUNDS:
        lo, hi = _INT_SETTING_BOUNDS[key]
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"setting {key!r} must be an integer; got {value!r}") from None
        if n < lo or n > hi:
            raise ValueError(f"setting {key!r} must be in [{lo}, {hi}]; got {n}")
    elif key in _BOOL_SETTING_KEYS:
        if value.strip().lower() not in _TRUTHY_STRINGS:
            raise ValueError(f"setting {key!r} must be one of {sorted(_TRUTHY_STRINGS)}; got {value!r}")
    # Unknown key → no validation


# The bounds map mirrors the user-facing Settings page rows. Keep in
# sync with frontend/src/pages/SettingsPage.tsx:KNOWN_SETTINGS.
_INT_SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "default_interval_seconds": (10, 86400),
    FAVORITE_MODEL_INTERVAL_KEY: (10, 86400),
    REGULAR_MODEL_INTERVAL_KEY: (10, 86400),
    PROVIDER_RATE_LIMIT_KEY: (1, 1000),
    FAVORITE_MODEL_FAILURE_CONFIRMATIONS_KEY: (1, 100),
    REGULAR_MODEL_FAILURE_CONFIRMATIONS_KEY: (1, 100),
    "default_timeout_seconds": (2, 600),
    "max_concurrency": (1, 1000),
    "retention_days": (1, 3650),
}
_BOOL_SETTING_KEYS: set[str] = {
    ADAPTIVE_BACKOFF_ENABLED_KEY,
    IDLE_THROTTLE_ENABLED_KEY,
}
