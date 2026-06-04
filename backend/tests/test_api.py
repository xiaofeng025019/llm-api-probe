"""End-to-end API tests using httpx.ASGITransport with patched DB engine."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import scheduler as sched_mod
from app.db.session import Base, set_session_maker
from app.main import app


@pytest.fixture
async def api_client():
    """Wire a fresh in-memory DB + session_maker override + reset scheduler."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_session_maker(sm)
    sched_mod._scheduler = None

    async def override_session():
        async with sm() as s:
            yield s

    # override get_session FastAPI dep
    from app.db.session import get_session

    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    set_session_maker(None)


# ---------- health/ready ----------------------------------------------------


@pytest.mark.asyncio
async def test_healthz(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------- providers CRUD --------------------------------------------------


@pytest.mark.asyncio
async def test_provider_crud(api_client: httpx.AsyncClient) -> None:
    payload = {
        "name": "openai-1",
        "kind": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-test",
    }
    r = await api_client.post("/api/v1/providers", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    pid = data["id"]
    assert data["name"] == "openai-1"

    r = await api_client.get("/api/v1/providers")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1

    r = await api_client.patch(f"/api/v1/providers/{pid}", json={"interval_seconds": 60})
    assert r.status_code == 200
    assert r.json()["data"]["interval_seconds"] == 60

    r = await api_client.delete(f"/api/v1/providers/{pid}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_provider_duplicate_name_409(api_client: httpx.AsyncClient) -> None:
    payload = {
        "name": "dup",
        "kind": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "k",
    }
    r1 = await api_client.post("/api/v1/providers", json=payload)
    assert r1.status_code == 201
    r2 = await api_client.post("/api/v1/providers", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_provider_404(api_client: httpx.AsyncClient) -> None:
    import uuid
    r = await api_client.get(f"/api/v1/providers/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_provider_run_disabled_returns_409(api_client: httpx.AsyncClient) -> None:
    r = await api_client.post(
        "/api/v1/providers",
        json={"name": "p1", "kind": "openai", "base_url": "https://x", "api_key": "k", "enabled": False},
    )
    pid = r.json()["data"]["id"]
    r = await api_client.post(f"/api/v1/providers/{pid}/run")
    assert r.status_code == 409


# ---------- models + sync-models -------------------------------------------


@pytest.mark.asyncio
async def test_sync_models_creates_models(api_client: httpx.AsyncClient) -> None:
    payload = {
        "name": "openai-1",
        "kind": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "k",
    }
    r = await api_client.post("/api/v1/providers", json=payload)
    pid = r.json()["data"]["id"]

    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "dall-e-3"}]})
        )
        r = await api_client.post(f"/api/v1/providers/{pid}/sync-models")
    assert r.status_code == 200, r.text
    ids = {m["model_id"] for m in r.json()["data"]}
    assert ids == {"gpt-4o", "dall-e-3"}


@pytest.mark.asyncio
async def test_patch_model_toggles_favorite(api_client: httpx.AsyncClient) -> None:
    payload = {
        "name": "p1",
        "kind": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "k",
    }
    pid = (await api_client.post("/api/v1/providers", json=payload)).json()["data"]["id"]
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        )
        await api_client.post(f"/api/v1/providers/{pid}/sync-models")
    r = await api_client.get(f"/api/v1/providers/{pid}/models")
    mid = r.json()["data"][0]["id"]
    r = await api_client.patch(f"/api/v1/models/{mid}", json={"is_favorite": True})
    assert r.status_code == 200
    assert r.json()["data"]["is_favorite"] is True


# ---------- settings --------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_round_trip(api_client: httpx.AsyncClient) -> None:
    r = await api_client.put(
        "/api/v1/settings", json={"items": {"retention_days": "7", "max_concurrency": "4"}}
    )
    assert r.status_code == 200
    r = await api_client.get("/api/v1/settings")
    items = {s["key"]: s["value"] for s in r.json()["data"]}
    assert items["retention_days"] == "7"
    assert items["max_concurrency"] == "4"


# ---------- import / export -------------------------------------------------


@pytest.mark.asyncio
async def test_import_export_roundtrip(api_client: httpx.AsyncClient) -> None:
    """Basic shape: create → export → re-import → still 1 provider,
    api_key is always carried (no more ?include_keys opt-out)."""
    payload = {
        "name": "p1",
        "kind": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "api_key": "k1",
    }
    await api_client.post("/api/v1/providers", json=payload)

    r = await api_client.post("/api/v1/export")
    body = r.json()["data"]
    assert any(p["name"] == "p1" for p in body["providers"])
    # api_key is always included.
    assert body["providers"][0]["api_key"] == "k1"

    # Re-import with a different api_key: existing provider gets
    # updated (api_key is now always overwritten on import).
    r = await api_client.post(
        "/api/v1/import",
        json={
            "providers": [
                {
                    "name": "p1",
                    "kind": "openai_compat",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "k2",
                }
            ],
            "settings": {"retention_days": "14"},
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["providers_updated"] == 1

    r = await api_client.post("/api/v1/export")
    assert r.json()["data"]["providers"][0]["api_key"] == "k2"


@pytest.mark.asyncio
async def test_export_includes_favorites(api_client: httpx.AsyncClient) -> None:
    """Exported payload should include a favorites_by_provider map
    that mirrors which model_ids are is_favorite=True on each provider."""
    # Seed: one provider with two models, one of them a favorite.
    r = await api_client.post(
        "/api/v1/providers",
        json={
            "name": "fav-test",
            "kind": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "k",
        },
    )
    pid = r.json()["data"]["id"]
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
            )
        )
        await api_client.post(f"/api/v1/providers/{pid}/sync-models")
    r = await api_client.get(f"/api/v1/providers/{pid}/models")
    models = r.json()["data"]
    fav = next(m for m in models if m["model_id"] == "gpt-4o")
    await api_client.patch(f"/api/v1/models/{fav['id']}", json={"is_favorite": True})

    # Export and check the favorites map.
    r = await api_client.post("/api/v1/export")
    body = r.json()["data"]
    assert body["favorites_by_provider"] == {"fav-test": ["gpt-4o"]}


@pytest.mark.asyncio
async def test_import_restores_favorites(api_client: httpx.AsyncClient) -> None:
    """Re-importing an export that contains favorites should re-set
    is_favorite on the named provider's models, but only after that
    provider has had its model list synced (otherwise the favorite
    model_id can't be matched to a row)."""
    # Seed one provider with two models; export with fav on 'gpt-4o'.
    r = await api_client.post(
        "/api/v1/providers",
        json={"name": "p", "kind": "openai", "base_url": "https://x", "api_key": "k"},
    )
    pid = r.json()["data"]["id"]
    with respx.mock:
        respx.get("https://x/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
        )
        await api_client.post(f"/api/v1/providers/{pid}/sync-models")
    r = await api_client.get(f"/api/v1/providers/{pid}/models")
    fav = next(m for m in r.json()["data"] if m["model_id"] == "gpt-4o")
    await api_client.patch(f"/api/v1/models/{fav['id']}", json={"is_favorite": True})

    exported = (await api_client.post("/api/v1/export")).json()["data"]
    assert exported["favorites_by_provider"] == {"p": ["gpt-4o"]}

    # Wipe, then import (which should restore the soft-deleted provider).
    await api_client.delete(f"/api/v1/providers/{pid}")

    r = await api_client.post("/api/v1/import", json=exported)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["providers_updated"] == 1
    assert body["favorites_restored"] >= 1

    # The favorite is back. Locate the provider by name since SQLite
    # recycles rowids after DELETE.
    r = await api_client.get("/api/v1/providers")
    prov_id = next(p["id"] for p in r.json()["data"] if p["name"] == "p")
    r = await api_client.get(f"/api/v1/providers/{prov_id}/models")
    favorites = {m["model_id"]: m["is_favorite"] for m in r.json()["data"]}
    assert favorites.get("gpt-4o") is True


# ---------- dashboard + probe.run ------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_empty(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/api/v1/dashboard")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["providers"] == []
    assert body["totals"]["providers"] == 0


@pytest.mark.asyncio
async def test_probe_run_triggers(api_client: httpx.AsyncClient) -> None:
    pid = (
        await api_client.post(
            "/api/v1/providers",
            json={"name": "p1", "kind": "openai", "base_url": "https://x", "api_key": "k"},
        )
    ).json()["data"]["id"]
    r = await api_client.post(f"/api/v1/probe/run?provider_id={pid}")
    assert r.status_code == 200
    import uuid as _uuid
    r = await api_client.post(f"/api/v1/probe/run?provider_id={_uuid.uuid4()}")
    assert r.status_code == 404
