"""Dashboard, results, settings, import/export, probe.run, SSE."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.scheduler import sync_all_jobs, trigger_all_models_now, trigger_now, trigger_provider_now
from app.core.sse import get_sse
from app.core.trigger_queue import EnqueueResult
from app.db.session import get_session
from app.schemas.api import (
    ApiResponse,
    DashboardOut,
    ExportPayload,
    FavoriteEntry,
    ImportPayload,
    ProbeResultOut,
    SettingOut,
    SettingPut,
)
from app.services import import_export as import_export_svc
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
    """Apply a JSON config import to the DB.

    The per-spec merge logic lives in `app.services.import_export`
    so it's unit-testable independent of the HTTP layer. This route
    is now a 4-line coordinator.
    """
    created, updated = await import_export_svc.apply_provider_specs(session, body)
    favorites_restored = await import_export_svc.apply_favorites_import(session, body)
    disabled_restored = await import_export_svc.apply_disabled_import(session, body)
    await import_export_svc.apply_settings_import(session, body)
    await sync_all_jobs()
    return ApiResponse(
        data={
            "providers_created": created,
            "providers_updated": updated,
            "favorites_restored": favorites_restored,
            "disabled_restored": disabled_restored,
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
    # Same pattern for disabled models (enabled=False): a per-provider
    # list of model_ids the user has explicitly disabled, so the
    # dashboard's per-model toggle survives an export/import cycle.
    models_disabled: list[FavoriteEntry] = []
    disabled_by_provider: dict[str, list[str]] = {}
    for p in providers:
        prov_models = await models_svc.list_models(session, p.uuid_id)
        favs = [m.model_id for m in prov_models if m.is_favorite]
        if favs:
            favorites.append(
                FavoriteEntry(
                    provider_uuid=p.uuid_id,
                    provider_name=p.name,
                    model_ids=favs,
                )
            )
            favorites_by_provider[p.name] = favs
        disabled = [m.model_id for m in prov_models if not m.enabled]
        if disabled:
            models_disabled.append(
                FavoriteEntry(
                    provider_uuid=p.uuid_id,
                    provider_name=p.name,
                    model_ids=disabled,
                )
            )
            disabled_by_provider[p.name] = disabled
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
        models_disabled=models_disabled,
        disabled_by_provider=disabled_by_provider,
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

    result = await trigger_now(provider_id, model_id)
    if result == EnqueueResult.DROPPED_FULL:
        # Queue is saturated with MANUALs and nothing to evict. 503
        # + Retry-After so the client knows to back off briefly.
        raise HTTPException(
            status_code=503,
            detail="trigger queue full; please retry shortly",
            headers={"Retry-After": "5"},
        )
    if result == EnqueueResult.DEDUPED:
        # Either the (provider, model) was disabled, or the request
        # matched an already-pending item. The 409 wording is
        # preserved for back-compat — disabled is the common case.
        target = "model" if model_id is not None else "provider"
        raise HTTPException(
            status_code=409,
            detail=f"{target} is disabled or already pending; enable it before triggering a probe",
        )
    return ApiResponse(data={"enqueued": True})


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
    # If EVERY model got DROPPED_FULL, the queue is at capacity
    # with MANUALs. Surface 503 + Retry-After. Mixed results stay
    # as a normal 200 (the user got *some* probes queued).
    if scheduled == 0 and skipped > 0:
        # Heuristic: a saturated queue typically reports 0 enqueued
        # because every request was deduped (DROPPED_FULL counts
        # as skipped in trigger_provider_now). We can't tell apart
        # "all disabled" from "queue full" without inspecting
        # queue state, so let the caller try a smaller request.
        return ApiResponse(data={"scheduled": scheduled, "skipped": skipped})
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
