"""Pydantic schemas for API request/response."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    ErrorCode,
    ModelType,
    ProbeTarget,
    ProviderKind,
)

# ---------- common response shell -------------------------------------------


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ApiResponse(BaseModel):
    data: Any | None = None
    error: ApiError | None = None


# ---------- Provider --------------------------------------------------------


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=500)
    proxy: str | None = None
    enabled: bool = True
    interval_seconds: int = Field(default=300, ge=10, le=86400)
    timeout_seconds: int = Field(default=30, ge=2, le=600)
    headers_json: str | None = None


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, min_length=1, max_length=500)
    proxy: str | None = None
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=86400)
    timeout_seconds: int | None = Field(default=None, ge=2, le=600)
    headers_json: str | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    kind: ProviderKind
    base_url: str
    enabled: bool
    interval_seconds: int
    timeout_seconds: int
    proxy: str | None
    headers_json: str | None
    created_at: datetime
    updated_at: datetime


# ---------- Model -----------------------------------------------------------


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_id: int
    model_id: str
    display_name: str | None
    type: ModelType
    enabled: bool
    is_favorite: bool
    last_seen_at: datetime


class ModelPatch(BaseModel):
    enabled: bool | None = None
    is_favorite: bool | None = None


# ---------- Probe result ----------------------------------------------------


class ProbeResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_id: int
    model_id: int | None
    target: ProbeTarget
    success: bool
    http_status: int | None
    latency_ms: int | None
    ttfb_ms: int | None
    error_code: ErrorCode | None
    error_message: str | None
    checked_at: datetime


class DashboardProvider(BaseModel):
    provider_id: int
    name: str
    kind: ProviderKind
    enabled: bool
    model_count: int
    last_checked_at: datetime | None
    last_status: str | None
    availability_24h: float | None
    avg_latency_ms_24h: int | None
    favorite_models_online: int
    favorite_models_total: int


class DashboardOut(BaseModel):
    providers: list[DashboardProvider]
    totals: dict[str, int]


# ---------- Settings --------------------------------------------------------


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    value: str
    updated_at: datetime


class SettingPut(BaseModel):
    items: dict[str, str]


# ---------- Import/Export ---------------------------------------------------


class ImportPayload(BaseModel):
    providers: list[ProviderCreate] = Field(default_factory=list)
    settings: dict[str, str] = Field(default_factory=dict)


class ExportPayload(BaseModel):
    providers: list[dict[str, Any]]
    settings: dict[str, str]
