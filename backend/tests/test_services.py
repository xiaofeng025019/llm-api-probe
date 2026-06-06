"""Service layer tests using in-memory SQLite."""

from __future__ import annotations

import uuid as _uuid
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
    assert p.uuid_id is not None
    assert (await providers_svc.get_provider(session, p.uuid_id)).name == "openai-1"
    assert len(await providers_svc.list_providers(session)) == 1


@pytest.mark.asyncio
async def test_find_duplicate_detects_same_base_url_and_key(session) -> None:
    """Same (base_url, api_key) → duplicate. Different api_key at same
    base_url → NOT duplicate (multi-account is allowed)."""
    p1 = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="acct-A",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="sk-aaa",
        ),
    )
    # Same base_url + same key → duplicate
    dup = await providers_svc.find_duplicate(session, "https://api.openai.com", "sk-aaa")
    assert dup is not None
    assert dup.uuid_id == p1.uuid_id

    # Same base_url, different key → NOT duplicate (multi-account)
    assert (await providers_svc.find_duplicate(session, "https://api.openai.com", "sk-bbb")) is None

    # Different base_url, same key → NOT duplicate
    assert (await providers_svc.find_duplicate(session, "https://other.example.com", "sk-aaa")) is None

    # No api_key → never duplicate (caller decides what to do with secrets-less rows)
    assert (await providers_svc.find_duplicate(session, "https://api.openai.com", None)) is None

    # exclude_uuid lets the caller exclude the row being patched
    assert (
        await providers_svc.find_duplicate(
            session, "https://api.openai.com", "sk-aaa", exclude_uuid=p1.uuid_id
        )
    ) is None


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
    out = await providers_svc.patch_provider(
        session, p.uuid_id, ProviderPatch(interval_seconds=60, enabled=False)
    )
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
    assert await providers_svc.delete_provider(session, p.uuid_id)
    assert (await providers_svc.get_provider(session, p.uuid_id)) is None


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
        p.uuid_id,
        [
            DiscoveredModel(model_id="gpt-4o", type=ModelType.chat),
            DiscoveredModel(model_id="dall-e-3", type=ModelType.image),
        ],
    )
    assert {m.model_id for m in out} == {"gpt-4o", "dall-e-3"}
    # upsert same again -> no new rows
    await models_svc.upsert_discovered(
        session,
        p.uuid_id,
        [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)],
    )
    assert len(await models_svc.list_models(session, p.uuid_id)) == 2


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
        session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
    )
    m = await models_svc.patch_model(session, out[0].uuid_id, ModelPatch(is_favorite=True, enabled=False))
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
        session, p.uuid_id, [DiscoveredModel(model_id="old-1", type=ModelType.chat)]
    )
    # backdate last_seen_at
    out[0].last_seen_at = datetime.now() - timedelta(days=30)
    await session.commit()
    n = await models_svc.disable_stale(session, days=7)
    assert n == 1
    assert (await models_svc.get_model(session, out[0].uuid_id)).enabled is False


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
async def test_record_outcome_populates_provider_uuid_snapshot(session) -> None:
    """Every probe_result must snapshot the provider's UUID at probe time,
    so historical reports survive provider rename / id reassignment."""
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
    assert out.provider_uuid_at_probe == p.uuid_id
    # String snapshot also recorded for human display
    assert out.provider_name_at_probe == "p1"


@pytest.mark.asyncio
async def test_record_outcome_populates_model_uuid_snapshot(session) -> None:
    """Probes with a model must snapshot the model's UUID."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    ms = await models_svc.upsert_discovered(
        session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
    )
    model = ms[0]
    out = await results_svc.record_outcome(
        session,
        p,
        model.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=True, http_status=200, latency_ms=100),
    )
    assert out.model_uuid_at_probe == model.uuid_id
    assert out.model_id_at_probe == "gpt-4o"


@pytest.mark.asyncio
async def test_record_outcome_model_uuid_is_null_for_list_models(session) -> None:
    """list_models probes without a model row get model_uuid_at_probe = None."""
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
    assert out.model_uuid_at_probe is None
    assert out.provider_uuid_at_probe == p.uuid_id


@pytest.mark.asyncio
async def test_snapshot_uuid_survives_provider_rename(session) -> None:
    """If the provider's name changes after a probe, the probe_result's
    UUID + name snapshots still point to the historical identity."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="original-name",
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
    # Now rename the provider
    p.name = "renamed-name"
    await session.commit()

    # The snapshot fields still point to the *original* identity
    assert out.provider_uuid_at_probe == p.uuid_id
    assert out.provider_name_at_probe == "original-name"


@pytest.mark.asyncio
async def test_find_provider_by_uuid_resolves_snapshot(session) -> None:
    """The find_by_uuid helper resolves a probe's provider_uuid_at_probe
    back to the current provider row, even after soft-delete + restore."""
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

    # Soft-delete the provider
    p.deleted_at = datetime.now(UTC)
    await session.commit()

    # Plain get_provider would NOT find it; find_provider_by_uuid should
    found = await providers_svc.find_provider_by_uuid(session, out.provider_uuid_at_probe)
    assert found is not None
    assert found.uuid_id == p.uuid_id

    plain = await providers_svc.get_provider(session, p.uuid_id, include_deleted=False)
    assert plain is None  # confirms the contrast


@pytest.mark.asyncio
async def test_find_model_by_uuid_resolves_snapshot(session) -> None:
    """find_model_by_uuid resolves probe's model_uuid_at_probe even if the
    model is soft-deleted."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    ms = await models_svc.upsert_discovered(
        session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
    )
    model = ms[0]
    out = await results_svc.record_outcome(
        session,
        p,
        model.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=True, http_status=200, latency_ms=100),
    )

    # Soft-delete the model
    model.deleted_at = datetime.now(UTC)
    await session.commit()

    found = await models_svc.find_model_by_uuid(session, out.model_uuid_at_probe)
    assert found is not None
    assert found.uuid_id == model.uuid_id


@pytest.mark.asyncio
async def test_snapshot_persists_through_soft_delete_and_restore(session) -> None:
    """The UUID snapshot must allow attribution after a model is
    soft-deleted, then re-appears (typical case: model re-offered by
    upstream after being removed for a while)."""
    from sqlalchemy import select

    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    ms = await models_svc.upsert_discovered(
        session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
    )
    model = ms[0]
    out = await results_svc.record_outcome(
        session,
        p,
        model.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=True, http_status=200, latency_ms=100),
    )
    # Soft-delete the model (out of provider's offering for a while)
    model.deleted_at = datetime.now(UTC)
    await session.commit()
    # Re-fetch fresh
    out = await session.scalar(select(ProbeResult).where(ProbeResult.uuid_id == out.uuid_id))

    # Snapshot still allows finding the model
    found = await models_svc.find_model_by_uuid(session, out.model_uuid_at_probe)
    assert found is not None
    assert found.uuid_id == model.uuid_id
    assert found.deleted_at is not None  # confirms we did find the soft-deleted row


@pytest.mark.asyncio
async def test_record_outcome_updates_confirmed_model_status(session) -> None:
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
            session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
        )
    )[0]
    fav.is_favorite = True
    await session.commit()

    await results_svc.record_outcome(
        session,
        p,
        fav.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=False, http_status=500, latency_ms=100, error_code=ErrorCode.server),
    )
    model = await models_svc.get_model(session, fav.uuid_id)
    assert model is not None
    assert model.status == "suspect"
    assert model.consecutive_failures == 1
    assert model.status_checked_at is not None
    assert model.status_confirmed_at is not None
    assert model.last_success_at is None

    await results_svc.record_outcome(
        session,
        p,
        fav.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=False, http_status=500, latency_ms=100, error_code=ErrorCode.server),
    )
    model = await models_svc.get_model(session, fav.uuid_id)
    assert model is not None
    assert model.status == "offline"
    assert model.status_reason == "server"
    assert model.consecutive_failures == 2

    await results_svc.record_outcome(
        session,
        p,
        fav.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=True, http_status=200, latency_ms=80),
    )
    model = await models_svc.get_model(session, fav.uuid_id)
    assert model is not None
    assert model.status == "online"
    assert model.status_reason is None
    assert model.consecutive_failures == 0
    assert model.last_success_at is not None


@pytest.mark.asyncio
async def test_record_outcome_uses_precise_failure_status(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    model = (
        await models_svc.upsert_discovered(
            session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
        )
    )[0]

    await results_svc.record_outcome(
        session,
        p,
        model.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=False, http_status=429, latency_ms=100, error_code=ErrorCode.rate_limit),
    )
    refreshed = await models_svc.get_model(session, model.uuid_id)
    assert refreshed is not None
    assert refreshed.status == "rate_limited"
    assert refreshed.status_reason == "rate_limit"

    await results_svc.record_outcome(
        session,
        p,
        model.id,
        ProbeTarget.chat_completion,
        ProbeOutcome(success=False, http_status=404, latency_ms=100, error_code=ErrorCode.other),
    )
    refreshed = await models_svc.get_model(session, model.uuid_id)
    assert refreshed is not None
    assert refreshed.status == "not_found"


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
    assert (await results_svc.list_results(session, provider_id=p.uuid_id, limit=10)) == []


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
            session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o", type=ModelType.chat)]
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
        p.uuid_id,
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
    assert dash.providers[0].available_models_online == 1


@pytest.mark.asyncio
async def test_dashboard_includes_favorite_model_status_details(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k"),
    )
    fav, other = await models_svc.upsert_discovered(
        session,
        p.uuid_id,
        [
            DiscoveredModel(model_id="gpt-4o"),
            DiscoveredModel(model_id="gpt-4o-mini"),
        ],
    )
    fav.is_favorite = True
    await session.commit()

    base_now = datetime.now(UTC).replace(tzinfo=None)
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav.id,
            target=ProbeTarget.chat_completion,
            success=False,
            http_status=500,
            latency_ms=220,
            ttfb_ms=70,
            error_code=ErrorCode.server,
            error_message="server failed",
            checked_at=base_now,
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=fav.model_id,
            model_uuid_at_probe=fav.uuid_id,
        )
    )
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=fav.id,
            target=ProbeTarget.chat_completion,
            success=True,
            http_status=200,
            latency_ms=120,
            ttfb_ms=40,
            checked_at=base_now + timedelta(seconds=1),
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=fav.model_id,
            model_uuid_at_probe=fav.uuid_id,
        )
    )
    session.add(
        ProbeResult(
            provider_id=p.id,
            model_id=other.id,
            target=ProbeTarget.chat_completion,
            success=True,
            latency_ms=50,
            checked_at=base_now + timedelta(seconds=2),
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=other.model_id,
            model_uuid_at_probe=other.uuid_id,
        )
    )
    fav.status = "online"
    fav.status_checked_at = base_now + timedelta(seconds=1)
    fav.status_confirmed_at = base_now + timedelta(seconds=1)
    fav.last_success_at = base_now + timedelta(seconds=1)
    fav.consecutive_failures = 0
    await session.commit()

    dash = await results_svc.dashboard(session)
    favorite_models = dash.providers[0].favorite_models
    assert len(favorite_models) == 1
    assert favorite_models[0].model_id == "gpt-4o"
    assert favorite_models[0].status == "online"
    assert favorite_models[0].last_success_at == fav.last_success_at
    assert favorite_models[0].latency_ms == 120
    assert favorite_models[0].ttfb_ms == 40
    assert favorite_models[0].availability_24h == 50
    assert favorite_models[0].samples_24h == 2
    assert favorite_models[0].p95_latency_ms_24h == 220
    assert favorite_models[0].p95_ttfb_ms_24h == 70
    assert favorite_models[0].consecutive_failures == 0


@pytest.mark.asyncio
async def test_dashboard_provider_health_uses_provider_and_model_signals(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k"),
    )
    m1, m2 = await models_svc.upsert_discovered(
        session,
        p.uuid_id,
        [DiscoveredModel(model_id="ok-model"), DiscoveredModel(model_id="bad-model")],
    )
    await session.commit()

    await results_svc.record_outcome(
        session, p, None, ProbeTarget.list_models, ProbeOutcome(success=True, latency_ms=10)
    )
    await results_svc.record_outcome(
        session, p, m1.id, ProbeTarget.chat_completion, ProbeOutcome(success=True, latency_ms=10)
    )
    await results_svc.record_outcome(
        session, p, m2.id, ProbeTarget.chat_completion, ProbeOutcome(success=False, latency_ms=10)
    )

    dash = await results_svc.dashboard(session)
    assert dash.providers[0].last_status == "degraded"
    assert dash.providers[0].samples_24h == 3
    assert dash.providers[0].failures_24h == 1
    assert dash.providers[0].p95_latency_ms_24h == 10
    assert dash.providers[0].list_models_status == "ok"
    assert dash.providers[0].list_models_latency_ms == 10
    assert dash.totals["degraded"] == 1
    assert dash.totals["ok"] == 0
    assert dash.totals["failing"] == 0


@pytest.mark.asyncio
async def test_dashboard_provider_health_fails_when_list_models_fails(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(name="p1", kind=ProviderKind.openai, base_url="https://x", api_key="k"),
    )
    m = (await models_svc.upsert_discovered(session, p.uuid_id, [DiscoveredModel(model_id="ok-model")]))[0]
    await session.commit()

    await results_svc.record_outcome(
        session, p, m.id, ProbeTarget.chat_completion, ProbeOutcome(success=True, latency_ms=10)
    )
    await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, latency_ms=10, error_code=ErrorCode.other),
    )

    dash = await results_svc.dashboard(session)
    assert dash.providers[0].last_status == "fail"
    assert dash.providers[0].list_models_status == "fail"
    assert dash.providers[0].error_counts_24h == {"other": 1}
    assert dash.totals["failing"] == 1


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
        p.uuid_id,
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
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=ms[0].model_id,
            model_uuid_at_probe=ms[0].uuid_id,
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
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=ms[1].model_id,
            model_uuid_at_probe=ms[1].uuid_id,
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
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=ms[0].model_id,
            model_uuid_at_probe=ms[0].uuid_id,
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
            provider_name_at_probe=p.name,
            provider_uuid_at_probe=p.uuid_id,
            model_id_at_probe=ms[1].model_id,
            model_uuid_at_probe=ms[1].uuid_id,
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
    ms = await models_svc.upsert_discovered(session, p.uuid_id, [DiscoveredModel(model_id="gpt-4o")])
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
    # The list_models success marks the provider reachable, but all enabled
    # models failed, so the provider health is failing.
    assert dash.totals["failing"] == 1


# ---------- settings --------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_settings_idempotent(session) -> None:
    out = await settings_svc.upsert_settings(session, {"a": "1"})
    assert out[0].value == "1"
    out2 = await settings_svc.upsert_settings(session, {"a": "2"})
    assert out2[0].value == "2"
    assert (await settings_svc.get_setting(session, "a")) == "2"


@pytest.mark.asyncio
async def test_get_int_setting_uses_default_and_minimum(session) -> None:
    assert await settings_svc.get_int_setting(session, "missing", 300) == 300
    await settings_svc.upsert_settings(session, {"bad": "abc", "low": "3", "ok": "120"})
    assert await settings_svc.get_int_setting(session, "bad", 300) == 300
    assert await settings_svc.get_int_setting(session, "low", 300) == 10
    assert await settings_svc.get_int_setting(session, "ok", 300) == 120


@pytest.mark.asyncio
async def test_get_int_setting_clamps_to_maximum(session) -> None:
    """A maximum bound (used for the rate-limit knob) must clamp
    out-of-range values down to the ceiling, not just the floor."""
    # Above the max → clamped to max
    await settings_svc.upsert_settings(session, {"too_high": "5000"})
    assert await settings_svc.get_int_setting(session, "too_high", 300, minimum=1, maximum=100) == 100
    # In range → unchanged
    await settings_svc.upsert_settings(session, {"just_right": "42"})
    assert await settings_svc.get_int_setting(session, "just_right", 300, minimum=1, maximum=100) == 42
    # Below the min → clamped to min (still applies)
    await settings_svc.upsert_settings(session, {"too_low": "0"})
    assert await settings_svc.get_int_setting(session, "too_low", 300, minimum=1, maximum=100) == 1


@pytest.mark.asyncio
async def test_provider_health_status_never_probed_yields_none_not_degraded(session) -> None:
    """A provider with enabled models but NO chat probes yet must NOT
    be classified as 'degraded'. The list_models probe succeeded, so
    the upstream is reachable — we just haven't reached the per-model
    cadence yet. Return None (= "no signal yet") instead of misleading
    the user with a 'degraded' label.

    Pre-fix behavior: 0 probed models out of N enabled -> 'degraded'.
    Post-fix behavior: same input -> None.
    """
    from app.services.results import _provider_health_status

    # No probed models, but list_models succeeded
    status = _provider_health_status(
        provider_enabled=True,
        latest_list_models=None,  # type: ignore[arg-type]
        enabled_model_ids=[1, 2, 3],  # 3 enabled, but none probed
        recent_per_model={},
    )
    # list_models probe missing here, so we get None (not degraded).
    # The point: no signal = no status, not "degraded".
    assert status is None

    # Now repeat with a successful list_models probe — still must not be degraded
    class _FakeResult:
        success = True

    fake = _FakeResult()
    status_with_list = _provider_health_status(
        provider_enabled=True,
        latest_list_models=fake,  # type: ignore[arg-type]
        enabled_model_ids=[1, 2, 3],
        recent_per_model={},
    )
    assert status_with_list is None, f"expected None (no signal yet), got {status_with_list!r}"


@pytest.mark.asyncio
async def test_provider_health_status_probed_models_only(session) -> None:
    """Only the enabled models that have actually been probed should
    count toward the ok/fail/degraded decision. If 1/3 enabled models
    has been probed and that one is online, status should be 'ok' —
    not 'degraded' (which would be the pre-fix behavior)."""
    from app.services.results import _provider_health_status

    # 3 enabled models, only model 1 has been probed and is online
    status = _provider_health_status(
        provider_enabled=True,
        latest_list_models=None,  # type: ignore[arg-type]
        enabled_model_ids=[1, 2, 3],
        recent_per_model={1: True},  # only model 1 probed, all OK
    )
    assert status == "ok", f"expected 'ok' (probed models all online), got {status!r}"

    # Mix: 1 probed model online, 1 probed model offline
    status = _provider_health_status(
        provider_enabled=True,
        latest_list_models=None,  # type: ignore[arg-type]
        enabled_model_ids=[1, 2, 3],
        recent_per_model={1: True, 2: False},
    )
    assert status == "degraded", f"expected 'degraded' (mixed), got {status!r}"

    # All probed models failing
    status = _provider_health_status(
        provider_enabled=True,
        latest_list_models=None,  # type: ignore[arg-type]
        enabled_model_ids=[1, 2, 3],
        recent_per_model={1: False, 2: False},
    )
    assert status == "fail", f"expected 'fail' (all probed failing), got {status!r}"


@pytest.mark.asyncio
async def test_pin_unpin_probe_result(session) -> None:
    """Pin a failure row, verify it sticks, then unpin."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    failed = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=500, error_code=ErrorCode.server),
    )
    successful = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=True, http_status=200, latency_ms=42),
    )
    # Pinning a failure works
    assert await results_svc.pin_probe_result(session, failed.uuid_id) is True
    # Pinning a success returns False (meaningless semantically)
    assert await results_svc.pin_probe_result(session, successful.uuid_id) is False
    # Pinning a non-existent row returns False
    assert await results_svc.pin_probe_result(session, _uuid.uuid4()) is False
    # Unpinning the pinned row works
    assert await results_svc.unpin_probe_result(session, failed.uuid_id) is True
    # Verify the row's pinned column is now False
    rows = (
        await session.execute(ProbeResult.__table__.select().where(ProbeResult.uuid_id == failed.uuid_id))
    ).all()
    assert rows[0].pinned == 0


@pytest.mark.asyncio
async def test_list_recent_errors_pinned_first(session) -> None:
    """Pinned errors must sort to the top regardless of recency."""
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    # Three failures
    a = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=500, error_code=ErrorCode.server),
    )
    b = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=502, error_code=ErrorCode.server),
    )
    c = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=503, error_code=ErrorCode.server),
    )
    # Pin the OLDEST one (a) — it should still come first
    assert await results_svc.pin_probe_result(session, a.uuid_id) is True

    rows = await results_svc.list_recent_errors(session, limit=10)
    uuids = [r.uuid_id for r in rows]
    # a is pinned, must be at index 0
    assert uuids[0] == a.uuid_id
    # b, c are not pinned, ordered by recency (c first since it was last)
    assert uuids[1] == c.uuid_id
    assert uuids[2] == b.uuid_id


@pytest.mark.asyncio
async def test_error_history_filters_and_cleans_older_than_15_minutes(session) -> None:
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
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=500, error_code=ErrorCode.server),
    )
    fresh = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=502, error_code=ErrorCode.server),
    )

    await session.execute(
        ProbeResult.__table__.update()
        .where(ProbeResult.uuid_id == old.uuid_id)
        .values(checked_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=16))
    )
    await session.commit()

    rows = await results_svc.list_recent_errors(session, limit=100)
    assert [r.uuid_id for r in rows] == [fresh.uuid_id]

    removed = await results_svc.cleanup_error_history(session)
    assert removed == 1
    remaining = (await session.execute(ProbeResult.__table__.select())).all()
    assert [r.uuid_id for r in remaining] == [fresh.uuid_id]


@pytest.mark.asyncio
async def test_error_history_keeps_at_most_100_failures(session) -> None:
    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )

    rows = []
    for i in range(101):
        rows.append(
            await results_svc.record_outcome(
                session,
                p,
                None,
                ProbeTarget.list_models,
                ProbeOutcome(success=False, http_status=500 + (i % 3), error_code=ErrorCode.server),
            )
        )

    errors = await results_svc.list_recent_errors(session, limit=200)
    assert len(errors) == 100
    assert rows[0].uuid_id not in {r.uuid_id for r in errors}
    assert rows[-1].uuid_id == errors[0].uuid_id


@pytest.mark.asyncio
async def test_cleanup_old_skips_pinned(session) -> None:
    """Pinned rows must NOT be deleted by the retention cleanup.
    Otherwise the pin is meaningless — the user pinned something
    and it silently disappeared after `retention_days`."""
    from datetime import UTC, datetime, timedelta

    p = await providers_svc.create_provider(
        session,
        ProviderCreate(
            name="p1",
            kind=ProviderKind.openai,
            base_url="https://api.openai.com",
            api_key="k",
        ),
    )
    pinned = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=500, error_code=ErrorCode.server),
    )
    not_pinned = await results_svc.record_outcome(
        session,
        p,
        None,
        ProbeTarget.list_models,
        ProbeOutcome(success=False, http_status=500, error_code=ErrorCode.server),
    )
    # Pin one of them
    assert await results_svc.pin_probe_result(session, pinned.uuid_id) is True

    # Backdate BOTH rows to 100 days ago
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=100)
    await session.execute(
        ProbeResult.__table__.update().where(ProbeResult.uuid_id == pinned.uuid_id).values(checked_at=old)
    )
    await session.execute(
        ProbeResult.__table__.update().where(ProbeResult.uuid_id == not_pinned.uuid_id).values(checked_at=old)
    )
    await session.commit()

    # Run cleanup with a 30-day window. Should delete only the
    # non-pinned row.
    removed = await results_svc.cleanup_old(session, retention_days=30)
    assert removed == 1, f"expected exactly 1 row deleted, got {removed}"

    # Verify: the pinned row is still there, the non-pinned one is gone
    rows = (
        await session.execute(
            ProbeResult.__table__.select().where(
                ProbeResult.uuid_id.in_([pinned.uuid_id, not_pinned.uuid_id])
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].uuid_id == pinned.uuid_id
