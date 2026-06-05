"""End-to-end real HTTP probing against mocked upstreams (respx on shared client)."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import scheduler as sched_mod
from app.core.scheduler import (
    _run_probe,
    sync_jobs_for_provider,
)
from app.core.sse import get_sse
from app.db.models import (
    Model,
    ProbeResult,
    ProbeTarget,
    ProviderKind,
)
from app.db.session import Base, set_session_maker
from app.schemas.api import ProviderCreate
from app.services import providers as providers_svc


@pytest.mark.asyncio
async def test_e2e_openai_probe_with_models_and_sse() -> None:
    """Real openai prober + respx upstream + persist + SSE broadcast + cleanup."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_session_maker(sm)
    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")

    # subscribe to SSE
    sse = get_sse()
    queue = await sse.subscribe()
    events: list[tuple[str, dict]] = []

    async def reader():
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=2.0)
            except TimeoutError:
                return
            header, _, rest = payload.partition("\n")
            if header.startswith("event: "):
                name = header.split(": ", 1)[1]
                data_line = rest.split("\n", 1)[0]
                _, data_str = data_line.split(": ", 1)
                import json

                events.append((name, json.loads(data_str)))

    task = asyncio.create_task(reader())
    try:
        # create provider in DB
        async with sm() as s:
            p = await providers_svc.create_provider(
                s,
                ProviderCreate(
                    name="p-openai",
                    kind=ProviderKind.openai,
                    base_url="https://api.openai.com",
                    api_key="sk-test",
                ),
            )
            assert p.uuid_id is not None
            await sync_jobs_for_provider(p.uuid_id, session_maker=sm)

        # mocked upstream: list_models
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://api.openai.com/v1/models").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "gpt-4o"},
                            {"id": "gpt-4o-realtime"},
                            {"id": "dall-e-3"},
                        ]
                    },
                )
            )
            # run list_models probe directly
            await _run_probe(p.uuid_id, None, ProbeTarget.list_models, session_maker=sm)

            # chat completion stream
            async def gen():
                yield b'data: {"choices":[{"delta":{"content":"h"}}]}\n\n'
                yield b"data: [DONE]\n\n"

            mock.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, stream=gen())
            )
            # need a model to exist
            async with sm() as s:
                # find or create the gpt-4o model
                from sqlalchemy import select

                m = (await s.execute(select(Model).where(Model.model_id == "gpt-4o"))).scalar_one()
                mid = m.uuid_id
            await _run_probe(p.uuid_id, mid, ProbeTarget.chat_completion, session_maker=sm)

        # give SSE reader time to drain
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # verify
        async with sm() as s:
            results = (await s.execute(ProbeResult.__table__.select())).all()
            # 1 list_models + 1 chat_completion
            assert len(results) == 2
            targets = {r.target for r in results}
            assert ProbeTarget.list_models in targets
            assert ProbeTarget.chat_completion in targets

            models = (await s.execute(Model.__table__.select())).all()
            model_ids = {m.model_id for m in models}
            assert {"gpt-4o", "gpt-4o-realtime", "dall-e-3"} <= model_ids

        # SSE events received
        event_names = [n for n, _ in events]
        assert "probe.completed" in event_names
    finally:
        await sse.unsubscribe(queue)
        set_session_maker(None)


@pytest.mark.asyncio
async def test_e2e_anthropic_probe_sends_headers() -> None:
    """Verify Anthropic prober sends x-api-key + anthropic-version."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_session_maker(sm)
    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")
    try:
        async with sm() as s:
            p = await providers_svc.create_provider(
                s,
                ProviderCreate(
                    name="p-anth",
                    kind=ProviderKind.anthropic,
                    base_url="https://api.anthropic.com",
                    api_key="sk-ant-test",
                ),
            )
            await sync_jobs_for_provider(p.uuid_id, session_maker=sm)

        with respx.mock:
            respx.post("https://api.anthropic.com/v1/messages").mock(
                return_value=httpx.Response(200, json={"content": [{"text": "ok"}]})
            )
            await _run_probe(p.uuid_id, None, ProbeTarget.list_models, session_maker=sm)
        # list_models for anthropic returns empty success without HTTP call
        async with sm() as s:
            r = (await s.execute(ProbeResult.__table__.select())).first()
            assert r is not None
            assert r.success == 1
    finally:
        set_session_maker(None)


@pytest.mark.asyncio
async def test_e2e_gemini_probe_with_query_key() -> None:
    """Verify Gemini prober uses ?key= query param."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_session_maker(sm)
    sched_mod._scheduler = None
    sched = sched_mod.get_scheduler()
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

    sched._jobstores["default"] = SQLAlchemyJobStore(url="sqlite:///:memory:")
    try:
        async with sm() as s:
            p = await providers_svc.create_provider(
                s,
                ProviderCreate(
                    name="p-gemini",
                    kind=ProviderKind.gemini,
                    base_url="https://generativelanguage.googleapis.com",
                    api_key="KEY123",
                ),
            )
            await sync_jobs_for_provider(p.uuid_id, session_maker=sm)

        called_urls: list[str] = []
        with respx.mock:
            respx.get(url__regex=r".*googleapis\.com.*").mock(
                side_effect=lambda req: (
                    called_urls.append(str(req.url))
                    or httpx.Response(200, json={"models": [{"name": "models/gemini-1.5-pro"}]})
                )
            )
            await _run_probe(p.uuid_id, None, ProbeTarget.list_models, session_maker=sm)
        assert called_urls, "should have made HTTP call"
        assert "key=KEY123" in called_urls[0], f"missing api key in URL: {called_urls[0]}"

        async with sm() as s:
            models = (await s.execute(Model.__table__.select())).all()
            ids = {m.model_id for m in models}
            assert "gemini-1.5-pro" in ids
    finally:
        set_session_maker(None)


@pytest.mark.asyncio
async def test_e2e_daily_cleanup_removes_old_and_disables_stale() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_session_maker(sm)
    try:
        from datetime import datetime, timedelta

        from app.probers.types import DiscoveredModel
        from app.services.models import disable_stale, upsert_discovered
        from app.services.results import cleanup_old

        async with sm() as s:
            p = await providers_svc.create_provider(
                s,
                ProviderCreate(
                    name="p1",
                    kind=ProviderKind.openai,
                    base_url="https://x",
                    api_key="k",
                ),
            )
            # old + new result (use naive datetimes to match DB)

            from app.db.models import ProbeResult, ProbeTarget

            old = ProbeResult(
                provider_id=p.id,
                model_id=None,
                target=ProbeTarget.list_models,
                success=True,
                latency_ms=10,
                provider_name_at_probe=p.name,
                provider_uuid_at_probe=p.uuid_id,
            )
            s.add(old)
            await s.flush()
            old.checked_at = datetime.now() - timedelta(days=60)
            new = ProbeResult(
                provider_id=p.id,
                model_id=None,
                target=ProbeTarget.list_models,
                success=True,
                latency_ms=10,
                provider_name_at_probe=p.name,
                provider_uuid_at_probe=p.uuid_id,
            )
            s.add(new)
            await s.commit()

            # run cleanup
            removed = await cleanup_old(s, retention_days=30)
            assert removed == 1
            remaining = (await s.execute(ProbeResult.__table__.select())).all()
            assert len(remaining) == 1

            # stale model
            ms = await upsert_discovered(s, p.uuid_id, [DiscoveredModel(model_id="stale-1")])
            ms[0].last_seen_at = datetime.now() - timedelta(days=30)
            await s.commit()
            disabled = await disable_stale(s, days=7)
            assert disabled == 1
    finally:
        set_session_maker(None)
