"""Pydantic schemas for API request/response."""

from __future__ import annotations

import uuid
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
    # Optional on import: a secrets-less export can re-import without
    # overwriting keys on an existing provider. New providers still
    # need this — the import endpoint validates it before insert.
    api_key: str | None = Field(default=None, min_length=1, max_length=500)
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
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: uuid.UUID = Field(validation_alias="uuid_id", serialization_alias="id")
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
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: uuid.UUID = Field(validation_alias="uuid_id", serialization_alias="id")
    provider_id: uuid.UUID = Field(validation_alias="provider_uuid", serialization_alias="provider_id")
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
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: uuid.UUID = Field(validation_alias="uuid_id", serialization_alias="id")
    provider_id: uuid.UUID = Field(validation_alias="provider_uuid", serialization_alias="provider_id")
    model_id: uuid.UUID | None = Field(validation_alias="model_uuid", serialization_alias="model_id")
    target: ProbeTarget
    success: bool
    http_status: int | None
    latency_ms: int | None
    ttfb_ms: int | None
    error_code: ErrorCode | None
    error_message: str | None
    checked_at: datetime


class DashboardFavoriteModel(BaseModel):
    id: uuid.UUID
    model_id: str
    display_name: str | None
    type: ModelType
    enabled: bool
    status: str | None
    last_checked_at: datetime | None
    latency_ms: int | None
    ttfb_ms: int | None
    error_code: ErrorCode | None
    error_message: str | None
    availability_24h: float | None


class DashboardProvider(BaseModel):
    provider_id: uuid.UUID
    name: str
    kind: ProviderKind
    enabled: bool
    model_count: int
    last_checked_at: datetime | None
    last_status: str | None
    availability_24h: float | None
    avg_latency_ms_24h: int | None
    available_models_online: int
    favorite_models_online: int
    favorite_models_total: int
    favorite_models: list[DashboardFavoriteModel] = Field(default_factory=list)


class DashboardOut(BaseModel):
    providers: list[DashboardProvider]
    totals: dict[str, int]


# ---------- Settings --------------------------------------------------------


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    key: str
    value: str
    updated_at: datetime


class SettingPut(BaseModel):
    items: dict[str, str]


# ---------- Import/Export ---------------------------------------------------


class ImportPayload(BaseModel):
    providers: list[ProviderCreate] = Field(default_factory=list)
    settings: dict[str, str] = Field(default_factory=dict)
    # provider name (case-sensitive match against the providers above)
    # → list of model_id strings to mark as is_favorite on import. New
    # providers are imported first; favorite restoration happens after
    # so model lookups don't race.
    favorites_by_provider: dict[str, list[str]] = Field(default_factory=dict)


class ExportPayload(BaseModel):
    providers: list[dict[str, Any]]
    # Same shape as ImportPayload.favorites_by_provider: a flat map
    # so the export is self-describing and older clients (without
    # favorite support) can still load it.
    favorites_by_provider: dict[str, list[str]] = Field(default_factory=dict)
    settings: dict[str, str]
