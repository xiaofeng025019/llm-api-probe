"""Model routes (PATCH only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scheduler import sync_jobs_for_provider
from app.db.session import get_session
from app.schemas.api import ApiResponse, ModelOut, ModelPatch
from app.services import models as models_svc

router = APIRouter(prefix="/models", tags=["models"])


@router.patch("/{model_id}", response_model=ApiResponse)
async def patch(model_id: int, body: ModelPatch, session: AsyncSession = Depends(get_session)) -> ApiResponse:
    m = await models_svc.patch_model(session, model_id, body)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    # enabled/disabled toggling affects job set
    await sync_jobs_for_provider(m.provider_id)
    return ApiResponse(data=ModelOut.model_validate(m))
