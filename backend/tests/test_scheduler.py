"""Scheduler integration test using a tiny interval and respx-mocked httpx."""

from __future__ import annotations

import uuid as _uuid

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.scheduler import (
    _run_probe,
    sync_jobs_for_provider,
    trigger_now,
)
from app.db.models import Model, ProbeResult, ProbeTarget
from app.db.session import Base
from app.probers.types import DiscoveredModel
from app.schemas.api import ProviderCreate
from app.services import models as models_svc
from app.services import providers as providers_svc
from app.services import settings as settings_svc


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """Per-test cleanup of in-memory scheduler state.

    Without this, the adaptive-backoff streak dict and SSE lifecycle
    callbacks leak between tests in this module — a test that bumps a
    streak would skew the timing assertions in a later test, and a
    sync_jobs test that registered hooks would leave them installed
    for unrelated tests in this module."""
    from app.core import scheduler as sched_mod
    from app.core.sse import get_sse

    sched_mod._model_success_streak.clear()
    sched_mod._adaptive_backoff_enabled = True
    sched_mod._idle_throttle_enabled = True
    get_sse().set_lifecycle_hooks(None, None)
    yield
    sched_mod._model_success_streak.clear()
    get_sse().set_lifecycle_hooks(None, None)


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        # seed a provider
        p = await providers_svc.create_provider(
            s,
            ProviderCreate(
                name="p1",
                kind="openai",
                base_url="https://api.example.com",
                api_key="k",
            ),
        )
        await s.commit()
        yield {"sm": sm, "provider_id": p.uuid_id, "session": s}


@pytest.mark.asyncio
async def test_run_probe_records_outcome_and_upserts_model(env) -> None:
    with respx.mock:
        respx.get("https://api.example.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        )
        await _run_probe(env["provider_id"], None, ProbeTarget.list_models, env["sm"])

    async with env["sm"]() as s:
        rows = (await s.execute(ProbeResult.__table__.select())).all()
        assert len(rows) == 1
        m = (await s.execute(Model.__table__.select())).first()
        assert m is not None
        assert m.model_id == "gpt-4o"


@pytest.mark.asyncio
async def test_run_probe_handles_timeout_failure(env) -> None:
    # seed a model
    async with env["sm"]() as s:
        ms = await models_svc.upsert_discovered(s, env["provider_id"], [DiscoveredModel(model_id="gpt-4o")])
        await s.commit()
        model_uuid = ms[0].uuid_id

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            side_effect=httpx.ConnectTimeout("slow")
        )
        await _run_probe(env["provider_id"], model_uuid, ProbeTarget.chat_completion, env["sm"])

    async with env["sm"]() as s:
        rows = list((await s.execute(ProbeResult.__table__.select())).all())
        assert len(rows) == 1
        assert rows[0].success == 0
        assert rows[0].error_code == "timeout"


@pytest.mark.asyncio
async def test_sync_jobs_adds_and_removes(env) -> None:
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    from app.core import scheduler as sched_mod

    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")

    await sync_jobs_for_provider(env["provider_id"], session_maker=env["sm"])
    job_ids = sorted(j.id for j in sched.get_jobs())
    assert f"p{env['provider_id']}:list_models:m-" in job_ids

    # disable provider via DB
    async with env["sm"]() as s:
        from sqlalchemy import select

        from app.db.models import Provider

        result = await s.execute(select(Provider).where(Provider.uuid_id == env["provider_id"]))
        p = result.scalar_one()
        p.enabled = False
        await s.commit()
    await sync_jobs_for_provider(env["provider_id"], session_maker=env["sm"])
    assert sched.get_jobs() == []


@pytest.mark.asyncio
async def test_sync_jobs_uses_faster_interval_for_favorite_models(env) -> None:
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    from app.core import scheduler as sched_mod

    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")

    async with env["sm"]() as s:
        await settings_svc.upsert_settings(
            s,
            {
                settings_svc.FAVORITE_MODEL_INTERVAL_KEY: "120",
                settings_svc.REGULAR_MODEL_INTERVAL_KEY: "900",
            },
        )
        models = await models_svc.upsert_discovered(
            s,
            env["provider_id"],
            [
                DiscoveredModel(model_id="gpt-favorite"),
                DiscoveredModel(model_id="gpt-regular"),
            ],
        )
        models[0].is_favorite = True
        await s.commit()
        favorite_uuid = models[0].uuid_id
        regular_uuid = models[1].uuid_id

    await sync_jobs_for_provider(env["provider_id"], session_maker=env["sm"])

    favorite_job = sched.get_job(f"p{env['provider_id']}:chat_completion:m{favorite_uuid}")
    regular_job = sched.get_job(f"p{env['provider_id']}:chat_completion:m{regular_uuid}")
    assert favorite_job is not None
    assert regular_job is not None
    assert favorite_job.trigger.interval.total_seconds() == 120
    assert regular_job.trigger.interval.total_seconds() == 900


@pytest.mark.asyncio
async def test_trigger_now_returns_false_when_disabled(env) -> None:
    # Disabled provider should reject the trigger so the API can surface a 409.
    async with env["sm"]() as s:
        from sqlalchemy import select

        from app.db.models import Provider

        result = await s.execute(select(Provider).where(Provider.uuid_id == env["provider_id"]))
        p = result.scalar_one()
        p.enabled = False
        await s.commit()
    assert await trigger_now(env["provider_id"], None, session_maker=env["sm"]) is False


@pytest.mark.asyncio
async def test_trigger_now_returns_true_when_enabled(env) -> None:
    assert await trigger_now(env["provider_id"], None, session_maker=env["sm"]) is True


@pytest.mark.asyncio
async def test_trigger_now_returns_false_for_disabled_model(env) -> None:
    async with env["sm"]() as s:
        ms = await models_svc.upsert_discovered(s, env["provider_id"], [DiscoveredModel(model_id="gpt-4o")])
        await s.commit()
        model_uuid = ms[0].uuid_id
        # disable the model
        m = await s.get(Model, ms[0].id)
        m.enabled = False
        await s.commit()
    assert await trigger_now(env["provider_id"], model_uuid, session_maker=env["sm"]) is False


@pytest.mark.asyncio
async def test_ensure_random_sweep_job_is_idempotent(env) -> None:
    """Calling _ensure_random_sweep_job multiple times must not add
    duplicate jobs."""
    from app.core import scheduler as sched_mod
    sched = sched_mod.get_scheduler()
    sched_mod._ensure_random_sweep_job(sched)
    sched_mod._ensure_random_sweep_job(sched)
    sched_mod._ensure_random_sweep_job(sched)
    matches = [j for j in sched.get_jobs() if j.id == "background:random_probe_sweep"]
    assert len(matches) == 1


def test_provider_rate_limit_sliding_window() -> None:
    """_check_provider_rate_limit must allow up to N requests in any
    60s window, then reject until the oldest timestamp ages out."""
    import time as _time
    from app.core.scheduler import (
        _check_provider_rate_limit,
        _provider_rate_buckets,
    )
    test_uuid = _uuid.uuid4()
    _provider_rate_buckets.pop(test_uuid, None)

    for i in range(5):
        allowed, retry = _check_provider_rate_limit(test_uuid, limit_per_minute=5)
        assert allowed, f"call {i+1} should be allowed (limit=5)"
        assert retry == 0.0

    allowed, retry = _check_provider_rate_limit(test_uuid, limit_per_minute=5)
    assert not allowed
    assert retry > 0

    _provider_rate_buckets.pop(test_uuid, None)


def test_provider_rate_limit_per_provider_isolation() -> None:
    """Each provider has its own bucket — hitting the limit on one
    must not affect another provider."""
    from app.core.scheduler import (
        _check_provider_rate_limit,
        _provider_rate_buckets,
    )
    a, b = _uuid.uuid4(), _uuid.uuid4()
    _provider_rate_buckets.pop(a, None)
    _provider_rate_buckets.pop(b, None)

    for _ in range(3):
        allowed, _ = _check_provider_rate_limit(a, limit_per_minute=3)
        assert allowed
    allowed, _ = _check_provider_rate_limit(a, limit_per_minute=3)
    assert not allowed

    allowed, _ = _check_provider_rate_limit(b, limit_per_minute=3)
    assert allowed

    _provider_rate_buckets.pop(a, None)
    _provider_rate_buckets.pop(b, None)


# ---------- adaptive backoff + idle throttling -----------------------------


def test_backoff_multiplier_curve() -> None:
    """The tier picker must match the documented curve: 1× under 3
    streak, 2× through 9, 4× through 29, 8× at 30+ (capped)."""
    from app.core.scheduler import _backoff_multiplier

    # Tier 1 (1×)
    assert _backoff_multiplier(0) == 1
    assert _backoff_multiplier(2) == 1
    # Tier 2 (2×)
    assert _backoff_multiplier(3) == 2
    assert _backoff_multiplier(9) == 2
    # Tier 3 (4×)
    assert _backoff_multiplier(10) == 4
    assert _backoff_multiplier(29) == 4
    # Tier 4 (8×, capped)
    assert _backoff_multiplier(30) == 8
    assert _backoff_multiplier(100) == 8
    assert _backoff_multiplier(10_000) == 8


@pytest.mark.asyncio
async def test_run_probe_increments_streak_on_success(env) -> None:
    """A successful chat probe must bump the model's success streak by 1."""
    from app.core import scheduler as sched_mod

    async with env["sm"]() as s:
        ms = await models_svc.upsert_discovered(
            s, env["provider_id"], [DiscoveredModel(model_id="gpt-4o")]
        )
        await s.commit()
        model_uuid = ms[0].uuid_id

    assert sched_mod._model_success_streak.get(model_uuid, 0) == 0

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )
        )
        await _run_probe(
            env["provider_id"], model_uuid, ProbeTarget.chat_completion, env["sm"]
        )

    assert sched_mod._model_success_streak.get(model_uuid, 0) == 1


@pytest.mark.asyncio
async def test_run_probe_resets_streak_on_failure(env) -> None:
    """A failed chat probe must snap the streak back to 0 (key removed),
    even if it was high before."""
    from app.core import scheduler as sched_mod

    async with env["sm"]() as s:
        ms = await models_svc.upsert_discovered(
            s, env["provider_id"], [DiscoveredModel(model_id="gpt-4o")]
        )
        await s.commit()
        model_uuid = ms[0].uuid_id

    # Pretend this model had a long success streak going.
    sched_mod._model_success_streak[model_uuid] = 25
    assert sched_mod._backoff_multiplier(25) == 4

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            side_effect=httpx.ConnectTimeout("slow")
        )
        await _run_probe(
            env["provider_id"], model_uuid, ProbeTarget.chat_completion, env["sm"]
        )

    # Key removed on failure → next backoff is 1× (base interval).
    assert model_uuid not in sched_mod._model_success_streak
    assert sched_mod._backoff_multiplier(
        sched_mod._model_success_streak.get(model_uuid, 0)
    ) == 1


def test_idle_multiplier_zero_subscribers() -> None:
    """With no SSE subscribers, the idle multiplier kicks in at 5×."""
    from app.core import scheduler as sched_mod
    from app.core.sse import get_sse

    # Ensure no subscribers and the toggle is on
    assert get_sse().active_subscribers() == 0
    sched_mod._idle_throttle_enabled = True
    assert sched_mod._idle_multiplier() == sched_mod.IDLE_MULTIPLIER


def test_idle_multiplier_disabled_toggle() -> None:
    """When the idle_throttle_enabled toggle is OFF, the multiplier is
    always 1 regardless of subscriber count."""
    from app.core import scheduler as sched_mod

    sched_mod._idle_throttle_enabled = False
    assert sched_mod._idle_multiplier() == 1


@pytest.mark.asyncio
async def test_idle_multiplier_with_subscriber() -> None:
    """Once at least one SSE client subscribes, the multiplier drops to 1×."""
    from app.core import scheduler as sched_mod
    from app.core.sse import get_sse

    sse = get_sse()
    q = await sse.subscribe()
    try:
        sched_mod._idle_throttle_enabled = True
        assert sse.active_subscribers() == 1
        assert sched_mod._idle_multiplier() == 1
    finally:
        await sse.unsubscribe(q)


@pytest.mark.asyncio
async def test_sse_wake_hook_fires_on_first_subscriber() -> None:
    """0→1 transition must invoke the on_first_subscriber callback;
    1→0 must invoke on_last_unsubscribe. Repeat subscribers must NOT
    re-trigger the wake."""
    from app.core.sse import SseManager

    sse = SseManager()
    wakes = 0
    sleeps = 0

    def _wake() -> None:
        nonlocal wakes
        wakes += 1

    def _sleep() -> None:
        nonlocal sleeps
        sleeps += 1

    sse.set_lifecycle_hooks(on_first_subscriber=_wake, on_last_unsubscribe=_sleep)

    q1 = await sse.subscribe()
    assert wakes == 1 and sleeps == 0
    q2 = await sse.subscribe()  # 1→2, no extra wake
    assert wakes == 1
    await sse.unsubscribe(q1)  # 2→1, not the last yet
    assert sleeps == 0
    await sse.unsubscribe(q2)  # 1→0
    assert sleeps == 1
    # Re-subscribe → wake fires again
    q3 = await sse.subscribe()
    assert wakes == 2
    await sse.unsubscribe(q3)


def test_get_bool_setting_parses_truthy_values() -> None:
    """Sanity check on the new boolean settings helper —
    accepted truthy strings and the unset/default fallback."""
    # Pure function test; spin up a tiny in-memory engine
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.session import Base as _Base

    async def _run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            # No row present → default returned
            assert await settings_svc.get_bool_setting(s, "x", True) is True
            assert await settings_svc.get_bool_setting(s, "x", False) is False
            # Set various truthy strings
            for v in ("true", "TRUE", "1", "yes", "on", "True"):
                await settings_svc.upsert_settings(s, {"x": v})
                assert await settings_svc.get_bool_setting(s, "x", False) is True
            for v in ("false", "0", "no", "off", "", "garbage"):
                await settings_svc.upsert_settings(s, {"x": v})
                assert await settings_svc.get_bool_setting(s, "x", True) is False

    asyncio.run(_run())
