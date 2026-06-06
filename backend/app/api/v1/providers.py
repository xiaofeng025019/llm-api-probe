"""Provider CRUD + sync-models."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import get_client
from app.core.scheduler import (
    sync_jobs_for_provider,
    trigger_provider_now,
)
from app.core.sse import get_sse
from app.db.models import ModelType, ProbeTarget, ProviderKind
from app.db.session import get_session
from app.probers import get_prober
from app.schemas.api import (
    ApiResponse,
    ModelOut,
    ProviderCreate,
    ProviderOut,
    ProviderPatch,
)
from app.services import models as models_svc
from app.services import providers as providers_svc
from app.services import results as results_svc

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=ApiResponse)
async def list_(session: AsyncSession = Depends(get_session)) -> ApiResponse:
    items = await providers_svc.list_providers(session)
    return ApiResponse(data=[ProviderOut.model_validate(p) for p in items])


@router.post("", response_model=ApiResponse, status_code=201)
async def create_(body: ProviderCreate, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    # unique name check
    if any(p.name == body.name for p in await providers_svc.list_providers(session)):
        raise HTTPException(status_code=409, detail=f"provider name already exists: {body.name}")
    # duplicate (base_url, api_key) check — same account on the same
    # endpoint is almost certainly an accidental re-add
    dup = await providers_svc.find_duplicate(session, body.base_url, body.api_key)
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"another provider {dup.name!r} already uses the same base_url "
                f"and api_key; refusing to create a duplicate"
            ),
        )
    p = await providers_svc.create_provider(session, body)
    await sync_jobs_for_provider(p.uuid_id)
    # Notify SSE subscribers so the dashboard refreshes without
    # waiting for the next 30s poll. See frontend/src/hooks/useDashboard.ts —
    # `provider.updated` is one of the events that triggers a fast-tier refresh.
    await get_sse().broadcast("provider.updated", {"provider_id": str(p.uuid_id), "change": "created"})
    return ApiResponse(data=ProviderOut.model_validate(p))


@router.get("/{provider_id}", response_model=ApiResponse)
async def read(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return ApiResponse(data=ProviderOut.model_validate(p))


@router.patch("/{provider_id}", response_model=ApiResponse)
async def patch(
    provider_id: uuid.UUID, body: ProviderPatch, session: AsyncSession = Depends(get_session)
) -> ApiResponse:
    # If the patch changes base_url or api_key, make sure the new
    # combination doesn't collide with another existing provider.
    if body.base_url is not None or body.api_key is not None:
        current = await providers_svc.get_provider(session, provider_id)
        if current is None:
            raise HTTPException(status_code=404, detail="provider not found")
        new_base_url = body.base_url if body.base_url is not None else current.base_url
        new_api_key = body.api_key if body.api_key is not None else current.api_key
        dup = await providers_svc.find_duplicate(
            session, new_base_url, new_api_key, exclude_uuid=provider_id
        )
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"another provider {dup.name!r} already uses the same base_url "
                    f"and api_key; refusing to create a duplicate"
                ),
            )
    p = await providers_svc.patch_provider(session, provider_id, body)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    await sync_jobs_for_provider(provider_id)
    await get_sse().broadcast("provider.updated", {"provider_id": str(provider_id), "change": "patched"})
    return ApiResponse(data=ProviderOut.model_validate(p))


@router.delete("/{provider_id}", response_model=ApiResponse)
async def delete(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    if not await providers_svc.delete_provider(session, provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    await sync_jobs_for_provider(provider_id)  # will remove all jobs for this provider
    await get_sse().broadcast("provider.updated", {"provider_id": str(provider_id), "change": "deleted"})
    return ApiResponse(data={"deleted": str(provider_id)})


@router.post("/{provider_id}/sync-models", response_model=ApiResponse)
async def sync_models(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    """Refresh the provider's model list with an inline list_models call."""
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    # We run it inline here so we can return discovered models; a small latency cost is acceptable.
    client = get_client()
    prober = get_prober(ProviderKind(p.kind), client)
    outcome = await prober.list_models(p)
    # Record the outcome so the dashboard sees a fresh list_models result
    # (overwriting any stale 404 from before a URL fix or fallback update).
    await results_svc.record_outcome(session, p, None, ProbeTarget.list_models, outcome)
    if not outcome.success:
        raise HTTPException(
            status_code=502,
            detail=f"upstream list_models failed: {outcome.error_code} {outcome.error_message}",
        )
    if outcome.models:
        await models_svc.upsert_discovered(session, provider_id, outcome.models)
        await sync_jobs_for_provider(provider_id)
    models = await models_svc.list_models(session, provider_id)
    # sync-models can add/remove models, so the dashboard's model
    # counts and the ProviderDetail's model grid both need to know.
    await get_sse().broadcast(
        "model.updated", {"provider_id": str(provider_id), "change": "synced", "count": len(models)}
    )
    return ApiResponse(data=[ModelOut.model_validate(m) for m in models])


@router.get("/{provider_id}/models", response_model=ApiResponse)
async def list_models(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    items = await models_svc.list_models(session, provider_id)
    return ApiResponse(data=[ModelOut.model_validate(m) for m in items])


@router.post("/{provider_id}/models", response_model=ApiResponse, status_code=201)
async def add_model(
    provider_id: uuid.UUID,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Manually add a model to a provider.

    Used when the upstream does not expose a /v1/models endpoint
    (e.g. MiniMax) so the user can still register models for probing.
    """
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    model_id = body.get("model_id")
    if not model_id or not isinstance(model_id, str):
        raise HTTPException(status_code=400, detail="model_id is required")
    # Check for duplicates (active or soft-deleted)
    from sqlalchemy import select

    from app.db.models import Model

    existing = await session.scalar(
        select(Model).where(
            Model.provider_id == p.id,
            Model.model_id == model_id,
            Model.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"model {model_id!r} already exists")
    m = Model(
        provider_id=p.id,
        model_id=model_id,
        display_name=body.get("display_name") or None,
        type=body.get("type") or ModelType.chat,
        enabled=True,
        is_favorite=False,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    # Re-sync jobs so the new model gets a probe schedule
    await sync_jobs_for_provider(provider_id)
    await get_sse().broadcast(
        "model.updated", {"provider_id": str(provider_id), "model_id": str(m.uuid_id), "change": "added"}
    )
    return ApiResponse(data=ModelOut.model_validate(m))


@router.post("/{provider_id}/run", response_model=ApiResponse)
async def run_now(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    """Schedule a probe for **every** enabled model under this provider.

    Provider-grained convenience endpoint — the dashboard's per-card
    "Check model status" button uses this. For **single-model**
    probing the frontend calls ``POST /probe/run?provider_id=…&model_id=…``
    instead (see ``app.api.v1.dashboard.probe_run``). Both endpoints
    exist on purpose:

    * ``/providers/{id}/run`` — provider-wide, REST-y URL, no query string
    * ``/probe/run`` — model-grained, takes ``model_id`` to single out one
      probe; also accepts a bare ``provider_id`` for backward compat

    Don't quietly fold one into the other; the URL shape is part of the
    public surface and the React calls hard-code these paths.
    """
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    scheduled, skipped = await trigger_provider_now(provider_id)
    if scheduled == 0 and skipped > 0:
        raise HTTPException(
            status_code=409,
            detail="provider is disabled; enable it before checking model status",
        )
    return ApiResponse(data={"scheduled": scheduled, "skipped": skipped})
