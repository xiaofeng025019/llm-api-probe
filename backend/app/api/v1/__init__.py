"""V1 API routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import dashboard, models, providers

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(providers.router)
api_router.include_router(models.router)
api_router.include_router(dashboard.router)

__all__ = ["api_router"]
