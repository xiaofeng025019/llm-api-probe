"""Dashboard, results, settings, import/export, probe.run, SSE."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.scheduler import sync_all_jobs, trigger_now
from app.core.sse import get_sse
from app.db.session import get_session
from app.schemas.api import (
    ApiResponse,
    DashboardOut,
    ExportPayload,
    ImportPayload,
    ProbeResultOut,
    ProviderPatch,
    SettingOut,
    SettingPut,
)
from app.services import models as models_svc
from app.services import providers as providers_svc
from app.services import results as results_svc
from app.services import settings as settings_svc

router = APIRouter()


@router.get("/dashboard", response_model=ApiResponse)
async def dashboard(session: AsyncSession = Depends(get_session)) -> ApiResponse:
    return ApiResponse(data=DashboardOut.model_validate(await results_svc.dashboard(session)))


@router.get("/results", response_model=ApiResponse)
async def list_results(
    provider_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await results_svc.list_results(
        session, provider_id=provider_id, model_id=model_id, since=since, limit=limit
    )
    return ApiResponse(data=[ProbeResultOut.model_validate(r) for r in rows])


@router.get("/settings", response_model=ApiResponse)
async def list_settings(session: AsyncSession = Depends(get_session)) -> ApiResponse:
    items = await settings_svc.list_settings(session)
    return ApiResponse(data=[SettingOut.model_validate(s) for s in items])


@router.put("/settings", response_model=ApiResponse)
async def put_settings(body: SettingPut, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    out = await settings_svc.upsert_settings(session, body.items)
    await sync_all_jobs()
    return ApiResponse(data=[SettingOut.model_validate(s) for s in out])


@router.post("/import", response_model=ApiResponse)
async def import_(
    body: ImportPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    created = 0
    updated = 0
    favorites_restored = 0
    # Check both active and soft-deleted providers
    existing_active = {p.name: p for p in await providers_svc.list_providers(session)}
    existing_deleted = {
        p.name: p
        for p in await providers_svc.list_providers(session, include_deleted=True)
        if p.deleted_at is not None
    }

    for spec in body.providers:
        existing_p = existing_active.get(spec.name)
        deleted_p = existing_deleted.get(spec.name)

        if existing_p is not None:
            # Update existing active provider
            patch_data: dict[str, Any] = {
                "base_url": spec.base_url,
                "interval_seconds": spec.interval_seconds,
                "timeout_seconds": spec.timeout_seconds,
                "proxy": spec.proxy,
                "headers_json": spec.headers_json,
                "enabled": spec.enabled,
                "api_key": spec.api_key,
            }
            await providers_svc.patch_provider(session, existing_p.uuid_id, ProviderPatch(**patch_data))
            updated += 1
        elif deleted_p is not None:
            # Restore soft-deleted provider directly (patch_provider
            # can't reach soft-deleted rows since it filters by
            # include_deleted=False, and ProviderPatch has no
            # deleted_at field).
            deleted_p.deleted_at = None
            deleted_p.base_url = spec.base_url
            deleted_p.kind = spec.kind
            deleted_p.interval_seconds = spec.interval_seconds
            deleted_p.timeout_seconds = spec.timeout_seconds
            if spec.proxy is not None:
                deleted_p.proxy = spec.proxy
            if spec.headers_json is not None:
                deleted_p.headers_json = spec.headers_json
            deleted_p.enabled = spec.enabled
            if spec.api_key:
                deleted_p.api_key = spec.api_key
            await session.commit()
            await session.refresh(deleted_p)
            updated += 1
        else:
            # Create new provider
            if not spec.api_key:
                # A brand-new provider must have a key.
                raise HTTPException(
                    status_code=400,
                    detail=(f"provider {spec.name!r} is new; an api_key is required."),
                )
            await providers_svc.create_provider(session, spec)
            created += 1
    if body.settings:
        await settings_svc.upsert_settings(session, body.settings)

    # Restore favorites AFTER providers exist (so we can look them up
    # by name). set_favorites handles the case where a model row
    # doesn't yet exist on this DB (e.g. fresh import before any
    # /v1/models sync) by creating a placeholder row.
    if body.favorites_by_provider:
        existing = {p.name: p for p in await providers_svc.list_providers(session)}
        for prov_name, model_ids in body.favorites_by_provider.items():
            prov = existing.get(prov_name)
            if prov is None:
                # Provider declared in favorites but not in providers list
                # — skip silently. The user gets a 0 in the
                # favorites_restored count and the export's `providers`
                # field is the source of truth.
                continue
            favorites_restored += await models_svc.set_favorites(session, prov.uuid_id, model_ids)

    await sync_all_jobs()
    return ApiResponse(
        data={
            "providers_created": created,
            "providers_updated": updated,
            "favorites_restored": favorites_restored,
        }
    )


@router.post("/export", response_model=ApiResponse)
async def export_(session: AsyncSession = Depends(get_session)) -> ApiResponse:
    providers = await providers_svc.list_providers(session)
    settings = await settings_svc.list_settings(session)
    # Collect favorite model_ids per provider name so the import side
    # can restore is_favorite on the right model. Keyed by name (not
    # id) because the import creates new providers and only knows
    # names at that point.
    favorites_by_provider: dict[str, list[str]] = {}
    for p in providers:
        favs = [m.model_id for m in await models_svc.list_models(session, p.uuid_id) if m.is_favorite]
        if favs:
            favorites_by_provider[p.name] = favs
    payload = ExportPayload(
        providers=[
            {
                "name": p.name,
                "kind": p.kind.value,
                "base_url": p.base_url,
                # api_key is always included so the import roundtrip
                # works for brand-new providers too.
                "api_key": p.api_key,
                "proxy": p.proxy,
                "enabled": p.enabled,
                "interval_seconds": p.interval_seconds,
                "timeout_seconds": p.timeout_seconds,
                "headers_json": p.headers_json,
            }
            for p in providers
        ],
        favorites_by_provider=favorites_by_provider,
        settings={s.key: s.value for s in settings},
    )
    return ApiResponse(data=payload.model_dump())


@router.post("/probe/run", response_model=ApiResponse)
async def probe_run(
    provider_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required")
    if (await providers_svc.get_provider(session, provider_id)) is None:
        raise HTTPException(status_code=404, detail="provider not found")
    if model_id is not None:
        m = await models_svc.get_model(session, model_id)
        if m is None:
            raise HTTPException(status_code=404, detail="model not found")
        if m.provider_uuid != provider_id:
            raise HTTPException(
                status_code=400,
                detail=f"model {model_id} does not belong to provider {provider_id}",
            )
    scheduled = await trigger_now(provider_id, model_id)
    if not scheduled:
        # The target job (or its model/provider) is disabled. Surface a 409 so
        # the caller can show feedback instead of a silent no-op.
        target = "model" if model_id is not None else "provider"
        raise HTTPException(
            status_code=409,
            detail=f"{target} is disabled; enable it before triggering a probe",
        )
    return ApiResponse(data={"scheduled": True})


@router.get("/events")
async def events(request: Request) -> EventSourceResponse:
    sse = get_sse()
    queue = await sse.subscribe()

    async def event_generator():
        # initial ping
        yield {"event": "ping", "data": json.dumps({"ts": datetime.now(UTC).isoformat()})}
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                # payload format: "event: x\ndata: {json}\n\n"
                if payload.startswith("event: "):
                    header, _, rest = payload.partition("\n")
                    _, event_name = header.split(": ", 1)
                    data_line = rest.split("\n", 1)[0]
                    _, data_str = data_line.split(": ", 1)
                    yield {"event": event_name, "data": data_str}
                else:
                    yield {"data": payload}
        finally:
            await sse.unsubscribe(queue)

    return EventSourceResponse(event_generator())
