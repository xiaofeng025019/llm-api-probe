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


@pytest.mark.asyncio
async def test_run_probe_rate_limited_records_synthetic_outcome(env) -> None:
    """When the per-provider rate limit is hit, _run_probe must NOT
    call upstream — it records a synthetic `rate_limited` outcome
    and returns."""
    import time as _time
    from collections import deque
    from app.core import scheduler as sched_mod

    # Pre-fill the bucket to the limit so the next call is rate-limited
    sched_mod._provider_rate_buckets[env["provider_id"]] = deque(
        [_time.monotonic()] * 5
    )
    async with env["sm"]() as s:
        await settings_svc.upsert_settings(s, {"provider_rate_limit_per_minute": "5"})

    # No respx mock — if _run_probe actually called upstream, the
    # httpx default client would raise (no respx) and we'd see a
    # different error_code than rate_limit.
    await _run_probe(env["provider_id"], None, ProbeTarget.list_models, env["sm"])

    async with env["sm"]() as s:
        row = (await s.execute(ProbeResult.__table__.select())).first()
        assert row is not None
        assert row.success == 0
        assert row.error_code == "rate_limit"
        assert "rate limit" in (row.error_message or "")

    sched_mod._provider_rate_buckets.pop(env["provider_id"], None)
