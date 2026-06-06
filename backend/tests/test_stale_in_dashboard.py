"""Integration test: the dashboard service must demote an old
"online" favorite model to status="stale" in the API response.

This is the wiring test — proves that `_is_model_stale` is actually
called from `dashboard()` and the result flows through to the
Pydantic response, not just defined and forgotten.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    ErrorCode,
    Model,
    ModelType,
    ProbeResult,
    ProbeTarget,
    Provider,
    ProviderKind,
)
from app.db.session import Base
from app.services import results as results_svc


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s


@pytest.mark.asyncio
async def test_dashboard_demotes_old_online_favorite_to_stale(
    session: AsyncSession,
) -> None:
    """A favorite model whose last successful probe is older than
    2× the favorite interval must appear as `stale` in the
    dashboard response (not `online`).
    """
    provider = Provider(
        name="stale-test-provider",
        kind=ProviderKind.openai_compat,
        base_url="https://example.com",
        api_key="sk-test",
        enabled=True,
        interval_seconds=300,
        timeout_seconds=30,
    )
    session.add(provider)
    await session.flush()

    model = Model(
        provider_id=provider.id,
        model_id="fav-model",
        display_name="Fav",
        type=ModelType.chat,
        is_favorite=True,
        enabled=True,
        status="online",
        status_checked_at=datetime.now(UTC) - timedelta(hours=1),
        last_success_at=datetime.now(UTC) - timedelta(hours=1),
        consecutive_failures=0,
    )
    session.add(model)
    await session.flush()

    # Insert an old successful probe (1 hour ago) — well past
    # 2 × 60s = 120s threshold for a favorite with default settings.
    old = datetime.now(UTC) - timedelta(hours=1)
    session.add(
        ProbeResult(
            provider_id=provider.id,
            model_id=model.id,
            target=ProbeTarget.chat_completion,
            success=True,
            http_status=200,
            latency_ms=150,
            checked_at=old,
            provider_uuid_at_probe=provider.uuid_id,
            model_uuid_at_probe=model.uuid_id,
            provider_name_at_probe=provider.name,
            model_id_at_probe=model.model_id,
        )
    )
    await session.commit()

    out = await results_svc.dashboard(session)
    assert len(out.providers) == 1
    favorites = out.providers[0].favorite_models
    assert len(favorites) == 1
    assert favorites[0].status == "stale", (
        f"expected 'stale', got {favorites[0].status!r}"
    )


@pytest.mark.asyncio
async def test_dashboard_keeps_recent_online_favorite_as_online(
    session: AsyncSession,
) -> None:
    """A favorite whose last successful probe is fresh (within 2×
    interval) must stay `online` — we don't want to over-flag.
    """
    provider = Provider(
        name="fresh-test-provider",
        kind=ProviderKind.openai_compat,
        base_url="https://example.com",
        api_key="sk-test",
        enabled=True,
        interval_seconds=300,
        timeout_seconds=30,
    )
    session.add(provider)
    await session.flush()

    model = Model(
        provider_id=provider.id,
        model_id="fresh-fav",
        display_name="Fresh",
        type=ModelType.chat,
        is_favorite=True,
        enabled=True,
        status="online",
        consecutive_failures=0,
    )
    session.add(model)
    await session.flush()

    # 10 seconds ago — well within 2 × 60s = 120s
    fresh = datetime.now(UTC) - timedelta(seconds=10)
    session.add(
        ProbeResult(
            provider_id=provider.id,
            model_id=model.id,
            target=ProbeTarget.chat_completion,
            success=True,
            http_status=200,
            latency_ms=120,
            checked_at=fresh,
            provider_uuid_at_probe=provider.uuid_id,
            model_uuid_at_probe=model.uuid_id,
            provider_name_at_probe=provider.name,
            model_id_at_probe=model.model_id,
        )
    )
    await session.commit()

    out = await results_svc.dashboard(session)
    favorites = out.providers[0].favorite_models
    assert favorites[0].status == "online"


@pytest.mark.asyncio
async def test_dashboard_keeps_offline_favorite_as_offline(
    session: AsyncSession,
) -> None:
    """A favorite that's been failing must NOT be re-labelled as
    stale — that would hide a real problem.
    """
    provider = Provider(
        name="offline-test-provider",
        kind=ProviderKind.openai_compat,
        base_url="https://example.com",
        api_key="sk-test",
        enabled=True,
        interval_seconds=300,
        timeout_seconds=30,
    )
    session.add(provider)
    await session.flush()

    model = Model(
        provider_id=provider.id,
        model_id="offline-fav",
        display_name="Offline",
        type=ModelType.chat,
        is_favorite=True,
        enabled=True,
        status="offline",
        status_checked_at=datetime.now(UTC) - timedelta(hours=1),
        consecutive_failures=3,
    )
    session.add(model)
    await session.flush()

    old = datetime.now(UTC) - timedelta(hours=1)
    session.add(
        ProbeResult(
            provider_id=provider.id,
            model_id=model.id,
            target=ProbeTarget.chat_completion,
            success=False,
            http_status=500,
            latency_ms=200,
            error_code=ErrorCode.server,
            error_message="upstream down",
            checked_at=old,
            provider_uuid_at_probe=provider.uuid_id,
            model_uuid_at_probe=model.uuid_id,
            provider_name_at_probe=provider.name,
            model_id_at_probe=model.model_id,
        )
    )
    await session.commit()

    out = await results_svc.dashboard(session)
    favorites = out.providers[0].favorite_models
    assert favorites[0].status == "offline", (
        "an offline model must not be re-labelled stale"
    )
