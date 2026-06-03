"""Scheduler integration test using a tiny interval and respx-mocked httpx."""
from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.scheduler import (
    _run_probe,
    get_scheduler,
    sync_jobs_for_provider,
)
from app.db.models import Model, ProbeResult, ProbeTarget
from app.db.session import Base
from app.probers.types import DiscoveredModel
from app.schemas.api import ProviderCreate
from app.services import models as models_svc
from app.services import providers as providers_svc


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
        yield {"sm": sm, "provider_id": p.id, "session": s}


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
        ms = await models_svc.upsert_discovered(
            s, env["provider_id"], [DiscoveredModel(model_id="gpt-4o")]
        )
        await s.commit()
        model_id = ms[0].id

    with respx.mock:
        respx.post("https://api.example.com/v1/chat/completions").mock(
            side_effect=httpx.ConnectTimeout("slow")
        )
        await _run_probe(env["provider_id"], model_id, ProbeTarget.chat_completion, env["sm"])

    async with env["sm"]() as s:
        rows = list((await s.execute(ProbeResult.__table__.select())).all())
        assert len(rows) == 1
        assert rows[0].success == 0
        assert rows[0].error_code == "timeout"


@pytest.mark.asyncio
async def test_sync_jobs_adds_and_removes(env) -> None:
    from app.core import scheduler as sched_mod
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")

    await sync_jobs_for_provider(env["provider_id"], session_maker=env["sm"])
    job_ids = sorted(j.id for j in sched.get_jobs())
    assert f"p{env['provider_id']}:list_models:m-" in job_ids

    # disable provider via DB
    async with env["sm"]() as s:
        from app.db.models import Provider

        p = await s.get(Provider, env["provider_id"])
        p.enabled = False
        await s.commit()
    await sync_jobs_for_provider(env["provider_id"], session_maker=env["sm"])
    assert sched.get_jobs() == []

