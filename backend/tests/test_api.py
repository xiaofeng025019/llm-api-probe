"""End-to-end API tests using httpx.ASGITransport with patched DB engine."""

from __future__ import annotations

import uuid as _uuid

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
async def test_provider_duplicate_base_url_and_key_409(api_client: httpx.AsyncClient) -> None:
    """Two providers with different names but the same (base_url, api_key)
    must be rejected — it's almost always an accidental re-add."""
    r1 = await api_client.post(
        "/api/v1/providers",
        json={
            "name": "acct-A",
            "kind": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-shared",
        },
    )
    assert r1.status_code == 201
    # Same base_url + same key, different name → 409
    r2 = await api_client.post(
        "/api/v1/providers",
        json={
            "name": "acct-A-dup",
            "kind": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-shared",
        },
    )
    assert r2.status_code == 409
    # Same base_url, DIFFERENT key → allowed (multi-account)
    r3 = await api_client.post(
        "/api/v1/providers",
        json={
            "name": "acct-B",
            "kind": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-other",
        },
    )
    assert r3.status_code == 201


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
    # New (uuid-keyed) format is always present
    assert body["favorites"] == [{"provider_uuid": pid, "provider_name": "fav-test", "model_ids": ["gpt-4o"]}]
    # Legacy (name-keyed) format is also kept for backward compat
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


@pytest.mark.asyncio
async def test_import_favorites_survive_provider_rename(api_client: httpx.AsyncClient) -> None:
    """The original bug: after the user renames a provider, the old
    export's `favorites_by_provider` map (keyed by name) fails to
    re-attach on import, and the favorite is silently dropped.

    With the new uuid-keyed `favorites` field, the import resolves
    the provider by its stable UUID, so the rename doesn't matter.
    """
    # Seed
    r = await api_client.post(
        "/api/v1/providers",
        json={"name": "original", "kind": "openai", "base_url": "https://x", "api_key": "k"},
    )
    pid = r.json()["data"]["id"]
    with respx.mock:
        respx.get("https://x/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        )
        await api_client.post(f"/api/v1/providers/{pid}/sync-models")
    r = await api_client.get(f"/api/v1/providers/{pid}/models")
    fav = r.json()["data"][0]
    await api_client.patch(f"/api/v1/models/{fav['id']}", json={"is_favorite": True})
    exported = (await api_client.post("/api/v1/export")).json()["data"]

    # Rename the provider
    r = await api_client.patch(f"/api/v1/providers/{pid}", json={"name": "renamed"})
    assert r.status_code == 200

    # Soft-delete then import (which restores the provider)
    await api_client.delete(f"/api/v1/providers/{pid}")
    r = await api_client.post("/api/v1/import", json=exported)
    body = r.json()["data"]
    # The favorite IS restored (the new favorites field used uuid to
    # find the right provider)
    assert body["favorites_restored"] >= 1

    # Verify the favorite was restored
    r = await api_client.get("/api/v1/providers")
    prov_id = next(p["id"] for p in r.json()["data"] if p["name"] in ("original", "renamed"))
    r = await api_client.get(f"/api/v1/providers/{prov_id}/models")
    favorites = {m["model_id"]: m["is_favorite"] for m in r.json()["data"]}
    assert favorites.get("gpt-4o") is True


@pytest.mark.asyncio
async def test_import_legacy_favorites_format_still_works(api_client: httpx.AsyncClient) -> None:
    """An export from an older backend (with the legacy
    `favorites_by_provider` name-keyed map only) must still import
    correctly. The new backend accepts both formats."""
    r = await api_client.post(
        "/api/v1/providers",
        json={"name": "legacy", "kind": "openai", "base_url": "https://x", "api_key": "k"},
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

    # Wipe, then import with ONLY the legacy format
    await api_client.delete(f"/api/v1/providers/{pid}")
    legacy_payload = {
        "providers": [
            {
                "name": "legacy",
                "kind": "openai",
                "base_url": "https://x",
                "api_key": "k",
            }
        ],
        "favorites_by_provider": {"legacy": ["gpt-4o"]},
        "settings": {},
    }
    r = await api_client.post("/api/v1/import", json=legacy_payload)
    assert r.json()["data"]["favorites_restored"] >= 1

    # Verify
    r = await api_client.get("/api/v1/providers")
    prov_id = next(p["id"] for p in r.json()["data"] if p["name"] == "legacy")
    r = await api_client.get(f"/api/v1/providers/{prov_id}/models")
    favorites = {m["model_id"]: m["is_favorite"] for m in r.json()["data"]}
    assert favorites.get("gpt-4o") is True


# ---------- model.enabled roundtrip -----------------------------------------


@pytest.mark.asyncio
async def test_export_includes_disabled_models(api_client: httpx.AsyncClient) -> None:
    """Exported payload should include a per-provider list of
    `disabled_models` (i.e. models with enabled=False), so the user
    can save their disable/enable decisions alongside favorites and
    restore them on import.

    Mirrors the favorites contract: a new (uuid+name) format and a
    legacy name-keyed map for backward compat with older imports.
    """
    # Seed: one provider, two models, one of them disabled.
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
    disabled = next(m for m in r.json()["data"] if m["model_id"] == "gpt-4o-mini")
    await api_client.patch(f"/api/v1/models/{disabled['id']}", json={"enabled": False})

    # Export and check the disabled-models map.
    body = (await api_client.post("/api/v1/export")).json()["data"]
    # New (uuid-keyed) format is always present
    assert body["models_disabled"] == [
        {"provider_uuid": pid, "provider_name": "p", "model_ids": ["gpt-4o-mini"]}
    ]
    # Legacy (name-keyed) format is also kept for backward compat
    assert body["disabled_by_provider"] == {"p": ["gpt-4o-mini"]}


@pytest.mark.asyncio
async def test_import_restores_disabled_models(api_client: httpx.AsyncClient) -> None:
    """Re-importing an export that carries disabled-model entries
    must re-apply enabled=False on the named provider's models, but
    only after the provider has had its model list synced (same
    constraint as favorites)."""
    # Seed one provider with two models; export with disabled='gpt-4o-mini'.
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
    disabled = next(m for m in r.json()["data"] if m["model_id"] == "gpt-4o-mini")
    await api_client.patch(f"/api/v1/models/{disabled['id']}", json={"enabled": False})

    exported = (await api_client.post("/api/v1/export")).json()["data"]
    assert exported["disabled_by_provider"] == {"p": ["gpt-4o-mini"]}

    # Wipe, then import (which restores the soft-deleted provider).
    await api_client.delete(f"/api/v1/providers/{pid}")
    r = await api_client.post("/api/v1/import", json=exported)
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["providers_updated"] >= 1
    assert body["disabled_restored"] >= 1

    # Verify the disabled state is back.
    r = await api_client.get("/api/v1/providers")
    prov_id = next(p["id"] for p in r.json()["data"] if p["name"] == "p")
    r = await api_client.get(f"/api/v1/providers/{prov_id}/models")
    enabled = {m["model_id"]: m["enabled"] for m in r.json()["data"]}
    assert enabled.get("gpt-4o-mini") is False
    assert enabled.get("gpt-4o") is True


@pytest.mark.asyncio
async def test_hard_delete_model_preserves_probe_results(api_client: httpx.AsyncClient) -> None:
    """After our cascade fix, hard-deleting a model no longer
    cascades to delete probe_results. The probe_results stay
    accessible (with model_id=NULL but model_uuid_at_probe
    preserved) for historical reporting.

    We use raw SQL to bypass the ORM-level cascade interception.
    """
    import sqlalchemy as sa
    from sqlalchemy import select

    from app.db.models import Model, ProbeResult, ProbeTarget, Provider
    from app.db.session import get_session_maker

    sm = get_session_maker()
    async with sm() as session:
        # Create a provider + model + probe_result directly in the DB
        p = Provider(
            name="cascade-test",
            kind="openai",
            base_url="https://x",
            api_key="k",
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)

        m = Model(
            provider_id=p.id,
            model_id="gpt-4o",
        )
        session.add(m)
        await session.commit()
        await session.refresh(m)

        pr = ProbeResult(
            provider_id=p.id,
            model_id=m.id,
            target=ProbeTarget.chat_completion,
            success=True,
            http_status=200,
            latency_ms=100,
            provider_name_at_probe=p.name,
            model_id_at_probe=m.model_id,
            provider_uuid_at_probe=p.uuid_id,
            model_uuid_at_probe=m.uuid_id,
        )
        session.add(pr)
        await session.commit()
        await session.refresh(pr)

        m_id_int = m.id
        m_uuid = m.uuid_id

        # Hard-delete the model via raw SQL (bypasses ORM cascade)
        # Enable FK enforcement so ON DELETE SET NULL fires
        await session.execute(sa.text("PRAGMA foreign_keys = ON"))
        await session.execute(
            sa.text("DELETE FROM models WHERE id = :id"),
            {"id": m_id_int},
        )
        await session.commit()

        # Verify: probe_results.model_id should now be NULL,
        # but the uuid snapshot should still be there.
        # Use a fresh select (not session.get) to avoid cached state.
        pr_check = (
            await session.execute(
                select(ProbeResult)
                .where(ProbeResult.model_uuid_at_probe == m_uuid)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert pr_check is not None, "probe_result should survive model hard-delete"
        assert pr_check.model_id is None, "model_id FK should be SET NULL"
        assert pr_check.model_uuid_at_probe == m_uuid, "uuid snapshot should be preserved"


@pytest.mark.asyncio
async def test_hard_delete_provider_preserves_probe_results(api_client: httpx.AsyncClient) -> None:
    """Sibling of test_hard_delete_model_preserves_probe_results — when
    a provider row is hard-deleted (raw SQL bypassing the ORM cascade),
    its probe_results must survive with provider_id NULL and the
    provider_uuid_at_probe snapshot intact.

    This guards the cascade=SET NULL on probe_results.provider_id (vs
    the original CASCADE) so historical reporting / error history
    rows aren't silently lost when a user removes a provider.

    Hard delete (vs the API's soft-delete) is what we test here because
    soft-delete by definition keeps the row and the FK valid; the
    cascade behaviour only matters when the row actually disappears
    (manual DB cleanup, future hard-delete path, or test fixtures).
    """
    import sqlalchemy as sa
    from sqlalchemy import select

    from app.db.models import Model, ProbeResult, ProbeTarget, Provider
    from app.db.session import get_session_maker

    sm = get_session_maker()
    async with sm() as session:
        p = Provider(
            name="provider-cascade-test",
            kind="openai",
            base_url="https://y",
            api_key="k",
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)

        m = Model(provider_id=p.id, model_id="gpt-4o")
        session.add(m)
        await session.commit()
        await session.refresh(m)

        pr = ProbeResult(
            provider_id=p.id,
            model_id=m.id,
            target=ProbeTarget.chat_completion,
            success=True,
            http_status=200,
            latency_ms=100,
            provider_name_at_probe=p.name,
            model_id_at_probe=m.model_id,
            provider_uuid_at_probe=p.uuid_id,
            model_uuid_at_probe=m.uuid_id,
        )
        session.add(pr)
        await session.commit()
        await session.refresh(pr)

        p_id_int = p.id
        p_uuid = p.uuid_id

        # Hard-delete provider via raw SQL. Note: deleting the provider
        # row cascades to the model row (models.provider_id ON DELETE
        # CASCADE), which then triggers SET NULL on probe_results.model_id.
        # Independently, probe_results.provider_id is SET NULL by the
        # provider-side FK. So both FKs should end up NULL while the UUID
        # snapshots survive — that's the contract.
        await session.execute(sa.text("PRAGMA foreign_keys = ON"))
        await session.execute(
            sa.text("DELETE FROM providers WHERE id = :id"),
            {"id": p_id_int},
        )
        await session.commit()

        pr_check = (
            await session.execute(
                select(ProbeResult)
                .where(ProbeResult.provider_uuid_at_probe == p_uuid)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        assert pr_check is not None, "probe_result should survive provider hard-delete"
        assert pr_check.provider_id is None, "provider_id FK should be SET NULL"
        assert pr_check.model_id is None, "model_id FK should be SET NULL via the cascaded model delete"
        assert pr_check.provider_uuid_at_probe == p_uuid, "provider uuid snapshot preserved"


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
    # Provider-wide (no model_id) → list_models target → returns
    # {scheduled, skipped} (0 / 0 because no models exist yet).
    r = await api_client.post(f"/api/v1/probe/run?provider_id={pid}")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body == {"scheduled": 0, "skipped": 0}

    r = await api_client.post(f"/api/v1/probe/run?provider_id={_uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_probe_run_with_model_returns_enqueued_field(
    api_client: httpx.AsyncClient,
) -> None:
    """When a model_id is provided, the single-model MANUAL trigger
    returns {enqueued: True} so the UI can distinguish "queued" from
    "deduped (already pending)".
    """
    pid = (
        await api_client.post(
            "/api/v1/providers",
            json={"name": "p1m", "kind": "openai", "base_url": "https://x", "api_key": "k"},
        )
    ).json()["data"]["id"]
    mid = (
        await api_client.post(
            f"/api/v1/providers/{pid}/models",
            json={"model_id": "gpt-4o"},
        )
    ).json()["data"]["id"]
    r = await api_client.post(f"/api/v1/probe/run?provider_id={pid}&model_id={mid}")
    assert r.status_code == 200
    body = r.json()["data"]
    assert body == {"enqueued": True}


@pytest.mark.asyncio
async def test_probe_run_returns_503_when_queue_full(
    api_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the trigger queue is full of MANUALs and the new MANUAL
    can't displace, the single-model /probe/run endpoint must
    return 503 + Retry-After so the client knows to back off.
    """
    from app.core import trigger_queue
    from app.core.trigger_queue import EnqueueResult

    async def fake_full_enqueue(req):
        return EnqueueResult.DROPPED_FULL

    monkeypatch.setattr(
        trigger_queue.get_trigger_queue(),
        "enqueue",
        fake_full_enqueue,
    )
    pid = (
        await api_client.post(
            "/api/v1/providers",
            json={"name": "p2", "kind": "openai", "base_url": "https://x", "api_key": "k"},
        )
    ).json()["data"]["id"]
    mid = (
        await api_client.post(
            f"/api/v1/providers/{pid}/models",
            json={"model_id": "gpt-4o"},
        )
    ).json()["data"]["id"]
    r = await api_client.post(f"/api/v1/probe/run?provider_id={pid}&model_id={mid}")
    assert r.status_code == 503
    assert "Retry-After" in r.headers
    # Retry-After should be a small positive integer
    assert int(r.headers["Retry-After"]) > 0
