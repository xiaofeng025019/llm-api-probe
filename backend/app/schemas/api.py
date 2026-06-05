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
    # Optional stable identifier. When the import payload comes from a
    # prior export, this is the same UUID the export had — preserving
    # it lets downstream cross-references (favorites with provider_uuid,
    # probe_results.provider_uuid_at_probe) survive export/import
    # roundtrips. When omitted (e.g. a brand-new provider being
    # imported by hand), the DB layer auto-generates a fresh UUID.
    uuid_id: uuid.UUID | None = None


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
    status: str = "unknown"
    status_reason: str | None = None
    status_checked_at: datetime | None = None
    status_confirmed_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0


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
    # UUID snapshots — stable target identifiers captured at probe time.
    # Use these for historical reporting that must survive provider/model
    # rename, soft-delete, hard-delete (the row is gone but the snapshot
    # remains on this probe), or cross-database restore.
    provider_uuid_at_probe: uuid.UUID
    model_uuid_at_probe: uuid.UUID | None = None


class DashboardFavoriteModel(BaseModel):
    id: uuid.UUID
    model_id: str
    display_name: str | None
    type: ModelType
    enabled: bool
    status: str | None
    status_reason: str | None = None
    status_checked_at: datetime | None = None
    status_confirmed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_checked_at: datetime | None
    latency_ms: int | None
    ttfb_ms: int | None
    error_code: ErrorCode | None
    error_message: str | None
    availability_24h: float | None
    samples_24h: int = 0
    p95_latency_ms_24h: int | None = None
    p95_ttfb_ms_24h: int | None = None
    consecutive_failures: int = 0


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
    p95_latency_ms_24h: int | None = None
    p95_ttfb_ms_24h: int | None = None
    samples_24h: int = 0
    failures_24h: int = 0
    error_counts_24h: dict[str, int] = Field(default_factory=dict)
    list_models_status: str | None = None
    list_models_latency_ms: int | None = None
    list_models_checked_at: datetime | None = None
    list_models_error_code: ErrorCode | None = None
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


class FavoriteEntry(BaseModel):
    """One provider's favorites in an export/import payload.

    Carries BOTH the provider's stable UUID and its (possibly renamed)
    display name so the import side can:

    1. Resolve the current provider row by UUID (survives rename).
    2. Fall back to name-based lookup if the UUID is missing (older
       export files, or UUID never exported).
    """

    provider_uuid: uuid.UUID | None = None
    provider_name: str
    model_ids: list[str] = Field(default_factory=list)


class ImportPayload(BaseModel):
    providers: list[ProviderCreate] = Field(default_factory=list)
    settings: dict[str, str] = Field(default_factory=dict)
    # New (preferred) format: list of {provider_uuid, provider_name, model_ids}.
    # Import looks up by uuid first, falls back to name. Survives rename.
    favorites: list[FavoriteEntry] = Field(default_factory=list)
    # Legacy format: { provider_name: [model_id, ...] }.
    # Kept for backward compat with older export files. New exports
    # populate the `favorites` field instead. On import, both fields
    # are processed; the `favorites` field takes precedence.
    favorites_by_provider: dict[str, list[str]] = Field(default_factory=dict)


class ExportPayload(BaseModel):
    providers: list[dict[str, Any]]
    # New (preferred) format. Each entry carries the provider's UUID
    # so an import on a renamed or re-created provider still works.
    favorites: list[FavoriteEntry] = Field(default_factory=list)
    # Legacy format. Emitted alongside the new field for backward compat
    # with older importers. May be removed in a future major version.
    favorites_by_provider: dict[str, list[str]] = Field(default_factory=dict)
    settings: dict[str, str]
