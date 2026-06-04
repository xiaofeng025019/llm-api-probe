"""Service layer tests using in-memory SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    ErrorCode,
    ModelType,
    ProbeResult,
    ProbeTarget,
    ProviderKind,
)
from app.db.session import Base
from app.probers.types import DiscoveredModel, ProbeOutcome
from app.schemas.api import (
    ModelPatch,
    ProviderCreate,
    ProviderPatch,
)
from app.services import models as models_svc
from app.services import providers as providers_svc
from app.services import results as results_svc
from app.services import settings as settings_svc


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s


# ---------- provider CRUD ---------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_provider(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="openai-1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="sk-test",
        ),
    )
    assert p.id is not None
    assert (await providers_svc.get_provider(session, p.id)).name == "openai-1"
    assert len(await providers_svc.list_providers(session)) == 1


@pytest.mark.asyncio
async def test_patch_provider_updates_fields(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    out = await providers_svc.patch_provider(session, p.id, ProviderPatch(interval_seconds=60, enabled=False))
    assert out is not None
    assert out.interval_seconds == 60
    assert out.enabled is False


@pytest.mark.asyncio
async def test_delete_provider_returns_true(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    assert await providers_svc.delete_provider(session, p.id)
    assert (await providers_svc.get_provider(session, p.id)) is None


# ---------- model upsert / favorite -----------------------------------------


@pytest.mark.asyncio
async def test_upsert_discovered_inserts_and_updates(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    out = await models_svc.upsert_discovered(
        session,
        p.id,
        [
            DiscoveredModel(model_id="gpt-4o", type=ModelType.chat),
            DiscoveredModel(model_id="dall-e-3", type=ModelType.image),
        ],
    )
    assert {m.model_id for m in out} == {"gpt-4o", "dall-e-3"}
    # upsert same again -> no new rows
    await models_svc.upsert_discovered(
        session,
        p.id,
        [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)],
    )
    assert len(await models_svc.list_models(session, p.id)) == 2


@pytest.mark.asyncio
async def test_patch_model_favorite(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    out = await models_svc.upsert_discovered(
        session, p.id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
    )
    m = await models_svc.patch_model(session, out[0].id, ModelPatch(is_favorite=True, enabled=False))
    assert m is not None
    assert m.is_favorite is True
    assert m.enabled is False


@pytest.mark.asyncio
async def test_disable_stale(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    out = await models_svc.upsert_discovered(
        session, p.id, [DiscoveredModel(model_id="old-1", type=ModelType.chat)]
    )
    # backdate last_seen_at
    out[0].last_seen_at = datetime.now() - timedelta(days=30)
    await session.commit()
    n = await models_svc.disable_stale(session, days=7)
    assert n == 1
    assert (await models_svc.get_model(session, out[0].id)).enabled is False


# ---------- result write + dashboard + cleanup -----------------------------


@pytest.mark.asyncio
async def test_record_outcome_persists(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    out = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=True, http_status=200, latency_ms=42),
    )
    assert out.id is not None
    assert out.success is True


@pytest.mark.asyncio
async def test_cleanup_old_removes_old_rows(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    old = await results_svc.record_outcome(
        session, p, None, ProbeTarget.list_models, ProbeOutcome(success=True, latency_ms=10)
    )
    old.checked_at = datetime.now() - timedelta(days=60)
    await session.commit()
    removed = await results_svc.cleanup_old(session, retention_days=30)
    assert removed == 1
    assert (await results_svc.list_results(session, provider_id=p.id, limit=10)) == []  # only old row existed


@pytest.mark.asyncio
async def test_dashboard_aggregates_24h(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    fav = (
        await models_svc.upsert_discovered(
            session, p.id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
        )
    )[0]
    fav.is_favorite = True
    await session.commit()
    # Order matters: favorites_online uses "most recent probe per
    # model" semantics, so the LAST probe in the loop is the
    # current state. End with success so the favorite is online.
    for ok in (True, False, True):
        await results_svc.record_outcome(
            session,
            p,
            fav.id,
            ProbeTarget.chat_completion,
            ProbeOutcome(
                success=ok,
                http_status=200 if ok else 500,
                latency_ms=100,
                ttfb_ms=50,
                error_code=None if ok else ErrorCode.server,
            ),
        )
    dash = await results_svc.dashboard(session)
    assert dash.totals["providers"] == 1
    assert dash.totals["models"] == 1
    assert dash.totals["favorites_total"] == 1
    assert dash.totals["favorites_online"] == 1
    rows = dash.providers
    assert len(rows) == 1
    assert rows[0].model_count == 1
    assert rows[0].availability_24h is not None


@pytest.mark.asyncio
async def test_dashboard_available_models_counts_recent_per_model(session) -> None:
    """available_models totals how many of all models have a successful
    probe in the last 24h — the model-level equivalent of 'OK' / 'Failing'."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://x",
            api_key="k",
        ),
    )
    ms = await models_svc.upsert_discovered(
        session,
        p.id,
        [
            DiscoveredModel(model_id="gpt-4o"),
            DiscoveredModel(model_id="gpt-4o-mini"),
            DiscoveredModel(model_id="dall-e-3"),
        ],
    )
    await session.commit()
    gpt4o, gpt4omini, _dalle = ms[0], ms[1], ms[2]

    # gpt-4o: latest probe = success. 1 available.
    # gpt-4o-mini: latest probe = failure. 0 available.
    # dall-e-3: no recent probes. 0 available.
    # gpt-4o older failure should be ignored — only the newest counts.
    await results_svc.record_outcome(
        session, p, gpt4o.id, ProbeTarget.chat_completion, ProbeOutcome(success=False, latency_ms=10)
    )
    await results_svc.record_outcome(
        session, p, gpt4o.id, ProbeTarget.chat_completion, ProbeOutcome(success=True, latency_ms=10)
    )
    await results_svc.record_outcome(
        session, p, gpt4omini.id, ProbeTarget.chat_completion, ProbeOutcome(success=False, latency_ms=10)
    )

    dash = await results_svc.dashboard(session)
    assert dash.totals["models"] == 3
    assert dash.totals["available_models"] == 1


@pytest.mark.asyncio
async def test_dashboard_favorites_delta_24h_ago(session) -> None:
    """favorites_online_24h_ago is 'how many favorites were online at
    the time the 24h window started', so a model that's been up the
    whole time counts in both 'now' and '24h ago'."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k"),
    )
    ms = await models_svc.upsert_discovered(
        session,
        p.id,
        [DiscoveredModel(model_id="fav-stable"), DiscoveredModel(model_id="fav-flipped")],
    )
    for m in ms:
        m.is_favorite = True
    await session.commit()
    fav_stable, fav_flipped = ms[0].id, ms[1].id

    # 30h ago: BOTH favorites up. Use UTC-naive timestamps to match
    # dashboard's UTC cutoff math (otherwise the test passes/fails
    # depending on the host's local timezone).

    base_old = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=30)
    # Now: stable is up, flipped is down.
    base_now = datetime.now(UTC).replace(tzinfo=None)

    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav_stable,
            target=ProbeTarget.chat_completion,
            success=True,
            latency_ms=10,
            checked_at=base_old,
        )
    )
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav_flipped,
            target=ProbeTarget.chat_completion,
            success=True,
            latency_ms=10,
            checked_at=base_old,
        )
    )
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav_stable,
            target=ProbeTarget.chat_completion,
            success=True,
            latency_ms=10,
            checked_at=base_now,
        )
    )
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav_flipped,
            target=ProbeTarget.chat_completion,
            success=False,
            latency_ms=10,
            checked_at=base_now + timedelta(seconds=1),
        )
    )
    await session.commit()

    dash = await results_svc.dashboard(session)
    # Current: 1/2 (stable up, flipped down).
    assert dash.totals["favorites_online"] == 1
    assert dash.totals["favorites_total"] == 2
    # 24h ago: 2/2 (both up before the recent failure).
    assert dash.totals["favorites_online_24h_ago"] == 2


@pytest.mark.asyncio
async def test_dashboard_ignores_list_models_probes_for_model_count(session) -> None:
    """ProbeResult rows with model_id=None (list_models target) should not
    be counted as 'a model probe' — the model-level availability only
    counts chat_completion results."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k"),
    )
    ms = await models_svc.upsert_discovered(session, p.id, [DiscoveredModel(model_id="gpt-4o")])
    await session.commit()
    m = ms[0]
    # One list_models probe (no model_id) and one chat_completion failure.
    await results_svc.record_outcome(
        session, p, None, ProbeTarget.list_models, ProbeOutcome(success=True, latency_ms=10)
    )
    await results_svc.record_outcome(
        session, p, m.id, ProbeTarget.chat_completion, ProbeOutcome(success=False, latency_ms=10)
    )
    dash = await results_svc.dashboard(session)
    # available_models counts the chat_completion failure as 0
    assert dash.totals["available_models"] == 0
    # The list_models success still marks the provider as "ok"
    assert dash.totals["ok"] == 1


# ---------- settings --------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_settings_idempotent(session) -> None:
    out = await settings_svc.upsert_settings(session, {"a": "1"})
    assert out[0].value == "1"
    out2 = await settings_svc.upsert_settings(session, {"a": "2"})
    assert out2[0].value == "2"
    assert (await settings_svc.get_setting(session, "a")) == "2"
