"""Import / export service.

Extracted from `app/api/v1/dashboard.py:import_` so the per-spec
merge logic is unit-testable independent of the HTTP route.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Provider
from app.schemas.api import ImportPayload, ProviderPatch
from app.services import models as models_svc
from app.services import providers as providers_svc
from app.services import settings as settings_svc


async def apply_provider_import(
    session: AsyncSession,
    spec: Any,  # ImportPayload.providers entry — typed as Any to avoid circular import
    existing_active: dict[str, Provider],
    existing_deleted: dict[str, Provider],
    by_uuid: dict[uuid.UUID, Provider],
) -> str:
    """Apply a single provider spec to the DB. Returns one of:
    - "created": brand-new provider was inserted
    - "updated": existing active provider was patched
    - "restored": soft-deleted provider was reactivated

    Lookup precedence:
    1. UUID match (stable across renames)
    2. Name match against active providers
    3. Name match against soft-deleted providers
    """
    existing_p = existing_active.get(spec.name)
    deleted_p = existing_deleted.get(spec.name)
    existing_uuid = by_uuid.get(spec.uuid_id) if spec.uuid_id is not None else None
    if existing_uuid is not None and existing_uuid not in (existing_p, deleted_p):
        # Treat the uuid match as the canonical "existing" row.
        if existing_uuid.deleted_at is None:
            existing_p = existing_uuid
        else:
            deleted_p = existing_uuid

    if existing_p is not None:
        # Update existing active provider. The import spec is the
        # source of truth — overwrite name too, since the user may
        # have renamed the provider between export and import.
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
        await providers_svc.patch_provider(
            session, existing_p.uuid_id, ProviderPatch(**patch_data)
        )
        return "updated"

    if deleted_p is not None:
        # Restore soft-deleted provider directly (patch_provider can't
        # reach soft-deleted rows since it filters by include_deleted
        # =False, and ProviderPatch has no deleted_at field).
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
        return "restored"

    # Brand-new provider
    if not spec.api_key:
        raise HTTPException(
            status_code=400,
            detail=(f"provider {spec.name!r} is new; an api_key is required."),
        )
    await providers_svc.create_provider(session, spec)
    return "created"


async def apply_provider_specs(
    session: AsyncSession, payload: ImportPayload
) -> tuple[int, int]:
    """Apply every provider spec in `payload.providers`. Returns
    (created_count, updated_count). Note: restored providers count
    under updated.
    """
    existing_active = {p.name: p for p in await providers_svc.list_providers(session)}
    existing_deleted = {
        p.name: p
        for p in await providers_svc.list_providers(session, include_deleted=True)
        if p.deleted_at is not None
    }
    all_providers = await providers_svc.list_providers(session, include_deleted=True)
    by_uuid: dict[uuid.UUID, Provider] = {p.uuid_id: p for p in all_providers}

    created = 0
    updated = 0
    for spec in payload.providers:
        outcome = await apply_provider_import(
            session, spec, existing_active, existing_deleted, by_uuid
        )
        if outcome == "created":
            created += 1
        else:
            # "updated" or "restored" both count as updates
            updated += 1
    return created, updated


async def apply_favorites_import(
    session: AsyncSession, payload: ImportPayload
) -> int:
    """Apply favorites entries from the import payload. Returns the
    total number of model rows that became favorites.

    Lookup strategy: try uuid first (stable across renames), fall
    back to name (works for legacy exports that didn't include uuid).
    Dedupe by (provider_uuid, name) so the same provider isn't
    processed twice when the export has both formats pointing to it.
    """
    if not (payload.favorites or payload.favorites_by_provider):
        return 0
    all_providers = await providers_svc.list_providers(session, include_deleted=True)
    providers_by_uuid: dict[uuid.UUID, Provider] = {p.uuid_id: p for p in all_providers}
    providers_by_name: dict[str, Provider] = {p.name: p for p in all_providers}
    seen: set[tuple[str, str]] = set()
    total = 0

    # 1. New (rich) format first
    for entry in payload.favorites:
        prov: Provider | None = None
        if entry.provider_uuid is not None and entry.provider_uuid in providers_by_uuid:
            prov = providers_by_uuid[entry.provider_uuid]
        elif entry.provider_name in providers_by_name:
            prov = providers_by_name[entry.provider_name]
        if prov is None:
            # Provider declared in favorites but not in providers list —
            # skip silently. The export's `providers` field is the
            # source of truth; favorites alone can't recreate a
            # provider.
            continue
        key = (str(prov.uuid_id), prov.name)
        if key in seen:
            continue
        seen.add(key)
        total += await models_svc.set_favorites(
            session, prov.uuid_id, entry.model_ids
        )

    # 2. Legacy format for any provider not seen yet
    for prov_name, model_ids in payload.favorites_by_provider.items():
        prov = providers_by_name.get(prov_name)
        if prov is None:
            continue
        key = (str(prov.uuid_id), prov.name)
        if key in seen:
            continue
        seen.add(key)
        total += await models_svc.set_favorites(session, prov.uuid_id, model_ids)
    return total


async def apply_settings_import(
    session: AsyncSession, payload: ImportPayload
) -> None:
    """Persist any `payload.settings` into the settings table."""
    if payload.settings:
        await settings_svc.upsert_settings(session, payload.settings)
