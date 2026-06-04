"""Provider CRUD + sync-models."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import get_client
from app.core.scheduler import (
    sync_jobs_for_provider,
    trigger_now,
)
from app.db.models import ProviderKind
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
    p = await providers_svc.create_provider(session, body)
    await sync_jobs_for_provider(p.uuid_id)
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
    p = await providers_svc.patch_provider(session, provider_id, body)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    await sync_jobs_for_provider(provider_id)
    return ApiResponse(data=ProviderOut.model_validate(p))


@router.delete("/{provider_id}", response_model=ApiResponse)
async def delete(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    if not await providers_svc.delete_provider(session, provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    await sync_jobs_for_provider(provider_id)  # will remove all jobs for this provider
    return ApiResponse(data={"deleted": str(provider_id)})


@router.post("/{provider_id}/sync-models", response_model=ApiResponse)
async def sync_models(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    """Manually trigger a list_models probe right now."""
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    # We run it inline here so we can return discovered models; a small latency cost is acceptable.
    client = get_client()
    prober = get_prober(ProviderKind(p.kind), client)
    outcome = await prober.list_models(p)
    if not outcome.success:
        raise HTTPException(
            status_code=502,
            detail=f"upstream list_models failed: {outcome.error_code} {outcome.error_message}",
        )
    if outcome.models:
        await models_svc.upsert_discovered(session, provider_id, outcome.models)
        await sync_jobs_for_provider(provider_id)
    models = await models_svc.list_models(session, provider_id)
    return ApiResponse(data=[ModelOut.model_validate(m) for m in models])


@router.get("/{provider_id}/models", response_model=ApiResponse)
async def list_models(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    items = await models_svc.list_models(session, provider_id)
    return ApiResponse(data=[ModelOut.model_validate(m) for m in items])


@router.post("/{provider_id}/run", response_model=ApiResponse)
async def run_now(provider_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    p = await providers_svc.get_provider(session, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="provider not found")
    scheduled = await trigger_now(provider_id, None)
    if not scheduled:
        raise HTTPException(
            status_code=409,
            detail="provider is disabled; enable it before triggering a probe",
        )
    return ApiResponse(data={"scheduled": True})
