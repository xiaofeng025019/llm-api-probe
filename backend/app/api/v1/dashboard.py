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

from app.core.scheduler import sync_all_jobs, trigger_all_models_now, trigger_now, trigger_provider_now
from app.core.sse import get_sse
from app.db.session import get_session
from app.schemas.api import (
    ApiResponse,
    DashboardOut,
    ExportPayload,
    FavoriteEntry,
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
    # Build a uuid -> provider map (across active + soft-deleted) so we
    # can detect "the import spec's uuid_id already exists in this DB"
    # and merge into that row instead of failing on a unique constraint.
    all_providers = await providers_svc.list_providers(session, include_deleted=True)
    by_uuid: dict[uuid.UUID, Any] = {p.uuid_id: p for p in all_providers}

    for spec in body.providers:
        existing_p = existing_active.get(spec.name)
        deleted_p = existing_deleted.get(spec.name)
        # If the spec carries a uuid_id and it already exists, prefer
        # that row over the name-based lookup. This is the merge path
        # for users who import into a target DB that already has the
        # same provider under a different name (rename + import).
        existing_uuid = by_uuid.get(spec.uuid_id) if spec.uuid_id is not None else None
        if existing_uuid is not None and existing_uuid not in (existing_p, deleted_p):
            # Treat the uuid match as the canonical "existing" row.
            if existing_uuid.deleted_at is None:
                existing_p = existing_uuid
            else:
                deleted_p = existing_uuid

        if existing_p is not None:
            # Update existing active provider. The import spec is the
            # source of truth — overwrite name too, since the user
            # may have renamed the provider between export and import.
            patch_data: dict[str, Any] = {
                "name": spec.name,
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
            deleted_p.name = spec.name
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

    # Restore favorites AFTER providers exist (so we can look them up).
    # set_favorites handles the case where a model row doesn't yet
    # exist on this DB (e.g. fresh import before any /v1/models sync)
    # by creating a placeholder row.
    #
    # Lookup strategy: try uuid first (stable across renames), fall back
    # to name (works for legacy exports that didn't include uuid).
    if body.favorites or body.favorites_by_provider:
        all_providers = await providers_svc.list_providers(session, include_deleted=True)
        # Build lookup maps. Use include_deleted=True so a soft-deleted
        # provider that the import re-created can still be found.
        providers_by_uuid: dict[uuid.UUID, Any] = {p.uuid_id: p for p in all_providers}
        providers_by_name: dict[str, Any] = {p.name: p for p in all_providers}

        # Dedupe by (provider_uuid, name) so the same provider isn't
        # processed twice when the export has both formats pointing to
        # it.
        seen: set[tuple[str, str]] = set()

        # 1. Process the new (rich) format first.
        for entry in body.favorites:
            prov = None
            if entry.provider_uuid is not None and entry.provider_uuid in providers_by_uuid:
                prov = providers_by_uuid[entry.provider_uuid]
            elif entry.provider_name in providers_by_name:
                prov = providers_by_name[entry.provider_name]

            if prov is None:
                # Provider declared in favorites but not in providers
                # list — skip silently. The export's `providers` field
                # is the source of truth; favorites alone can't
                # recreate a provider.
                continue

            key = (str(prov.uuid_id), prov.name)
            if key in seen:
                continue
            seen.add(key)
            favorites_restored += await models_svc.set_favorites(
                session, prov.uuid_id, entry.model_ids
            )

        # 2. Process the legacy format for any provider we haven't seen yet.
        for prov_name, model_ids in body.favorites_by_provider.items():
            prov = providers_by_name.get(prov_name)
            if prov is None:
                continue
            key = (str(prov.uuid_id), prov.name)
            if key in seen:
                continue
            seen.add(key)
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
    # Collect favorites in the new (uuid+name) format. We also emit
    # the legacy name-keyed map for backward compat with older
    # importers. On import, the new format takes precedence.
    favorites: list[FavoriteEntry] = []
    favorites_by_provider: dict[str, list[str]] = {}
    for p in providers:
        favs = [m.model_id for m in await models_svc.list_models(session, p.uuid_id) if m.is_favorite]
        if favs:
            favorites.append(
                FavoriteEntry(
                    provider_uuid=p.uuid_id,
                    provider_name=p.name,
                    model_ids=favs,
                )
            )
            favorites_by_provider[p.name] = favs
    payload = ExportPayload(
        providers=[
            {
                "uuid_id": str(p.uuid_id),
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
        favorites=favorites,
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
    """Trigger a probe — model-grained (preferred) or provider-wide.

    Counterpart to ``POST /providers/{id}/run`` (see
    ``app.api.v1.providers.run_now``). The split is intentional:

    * **With ``model_id``** — schedules a single model. This is the only
      endpoint that does that; the React per-model "Check status" button
      hits this path via ``api.probeNow(providerId, modelId)``.
    * **Without ``model_id``** — falls back to a provider-wide sweep.
      Functionally identical to ``/providers/{id}/run``; both call
      ``trigger_provider_now``. Kept for back-compat with any external
      caller that already wires the query-string form.

    New code that wants the **provider-wide** behaviour should prefer
    ``/providers/{id}/run`` (cleaner URL, no query string). Don't fold
    the two endpoints together — both ship in the public surface.
    """
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
    if model_id is None:
        scheduled_count, skipped = await trigger_provider_now(provider_id)
        if scheduled_count == 0 and skipped > 0:
            raise HTTPException(
                status_code=409,
                detail="provider is disabled; enable it before checking model status",
            )
        return ApiResponse(data={"scheduled": scheduled_count, "skipped": skipped})

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


@router.post("/probe/run-all", response_model=ApiResponse)
async def probe_run_all(session: AsyncSession = Depends(get_session)) -> ApiResponse:
    """Schedule a fresh model-status check for every enabled provider + model.

    Used by the dashboard's "Check all model status" button. Returns
    the count of model checks that were
    enqueued vs. skipped (e.g. provider disabled).

    Probes run asynchronously in the background; the API call
    returns immediately. Watch the SSE stream or poll the
    /results endpoint to see results land.
    """
    scheduled, skipped = await trigger_all_models_now()
    return ApiResponse(data={"scheduled": scheduled, "skipped": skipped})


# ---------- Error history ---------------------------------------------------


@router.get("/errors", response_model=ApiResponse)
async def list_errors(
    limit: int = Query(default=100, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Return the recent failure log for the error history page.

    Pinned errors float to the top inside the short history window.
    Error history is capped at 100 failed rows and 15 minutes."""
    rows = await results_svc.list_recent_errors(session, limit=limit)
    return ApiResponse(data=[ProbeResultOut.model_validate(r) for r in rows])


@router.post("/errors/{probe_uuid}/pin", response_model=ApiResponse)
async def pin_error(
    probe_uuid: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Pin an error so it survives the daily retention cleanup and
    floats to the top of the errors list."""
    ok = await results_svc.pin_probe_result(session, probe_uuid)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="probe result not found or is not a failure (only failed probes can be pinned)",
        )
    return ApiResponse(data={"pinned": True})


@router.delete("/errors/{probe_uuid}/pin", response_model=ApiResponse)
async def unpin_error(
    probe_uuid: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Remove the pinned flag. The row itself stays in the table
    and will be cleaned up by the regular retention cycle."""
    ok = await results_svc.unpin_probe_result(session, probe_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="probe result not found")
    return ApiResponse(data={"pinned": False})


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
