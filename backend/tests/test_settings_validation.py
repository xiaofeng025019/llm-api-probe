"""Tests for settings validation.

The Settings page used to bind every setting row to a free-text input,
so the user could enter "abc" for `provider_rate_limit_per_minute`;
the backend stored it, `get_int_setting` silently fell back to the
default on parse error, and the dashboard kept using the default
with no indication the save was ignored. The contract enforced here:

* Known integer keys → reject non-integer / out-of-bounds (400)
* Known boolean keys → reject anything outside the truthy/falsy set (400)
* Unknown keys → accepted as-is (Settings table is a generic KV store)
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import Base
from app.services import settings as settings_svc


@pytest.fixture
async def sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_upsert_rejects_non_integer_for_int_setting(sm) -> None:
    async with sm() as s:
        with pytest.raises(ValueError, match="must be an integer"):
            await settings_svc.upsert_settings(s, {"provider_rate_limit_per_minute": "abc"})


@pytest.mark.asyncio
async def test_upsert_rejects_negative_for_int_setting(sm) -> None:
    async with sm() as s:
        with pytest.raises(ValueError, match=r"must be in"):
            await settings_svc.upsert_settings(s, {"provider_rate_limit_per_minute": "-5"})


@pytest.mark.asyncio
async def test_upsert_rejects_too_large_for_int_setting(sm) -> None:
    async with sm() as s:
        with pytest.raises(ValueError, match=r"must be in"):
            await settings_svc.upsert_settings(s, {"max_concurrency": "999999"})


@pytest.mark.asyncio
async def test_upsert_rejects_non_boolean_for_bool_setting(sm) -> None:
    async with sm() as s:
        with pytest.raises(ValueError, match="must be one of"):
            await settings_svc.upsert_settings(s, {"adaptive_backoff_enabled": "maybe"})


@pytest.mark.asyncio
async def test_upsert_accepts_valid_values(sm) -> None:
    async with sm() as s:
        out = await settings_svc.upsert_settings(
            s,
            {
                "provider_rate_limit_per_minute": "5",
                "adaptive_backoff_enabled": "true",
                "idle_throttle_enabled": "0",
                "retention_days": "14",
            },
        )
        # All 4 saved
        assert {r.key for r in out} == {
            "provider_rate_limit_per_minute",
            "adaptive_backoff_enabled",
            "idle_throttle_enabled",
            "retention_days",
        }


@pytest.mark.asyncio
async def test_upsert_accepts_unknown_keys(sm) -> None:
    """Unknown keys are unconstrained — the Settings table is a generic
    KV store. Only well-known runtime tunables get type validation."""
    async with sm() as s:
        out = await settings_svc.upsert_settings(s, {"unknown_freeform": "any value here"})
        assert len(out) == 1
        assert out[0].value == "any value here"


@pytest.mark.asyncio
async def test_upsert_validates_atomically(sm) -> None:
    """If ONE key in the batch is invalid, NONE should be persisted —
    the validation pass runs before any DB write."""
    async with sm() as s:
        with pytest.raises(ValueError):
            await settings_svc.upsert_settings(
                s,
                {
                    "retention_days": "7",  # valid
                    "max_concurrency": "garbage",  # invalid
                },
            )
        # The valid one must NOT have been persisted (atomic batch)
        existing = await settings_svc.get_setting(s, "retention_days")
        assert existing is None
