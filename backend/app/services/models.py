"""Model service: upsert discovered models, patch enabled/favorite, prune stale."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import Model, ModelType, Provider
from app.probers.types import DiscoveredModel
from app.schemas.api import ModelPatch


async def list_models(
    session: AsyncSession, provider_id: uuid.UUID, include_deleted: bool = False
) -> list[Model]:
    """List all models for a provider (by provider UUID), optionally including soft-deleted."""
    # First get the provider to get its integer ID
    provider = await session.scalar(select(Provider).where(Provider.uuid_id == provider_id))
    if provider is None:
        return []

    query = (
        select(Model)
        .where(Model.provider_id == provider.id)
        .order_by(Model.model_id)
        .options(joinedload(Model.provider))
    )
    if not include_deleted:
        query = query.where(Model.deleted_at.is_(None))
    res = await session.execute(query)
    return list(res.scalars().all())


async def get_model(
    session: AsyncSession, model_id: uuid.UUID, include_deleted: bool = False
) -> Model | None:
    """Get a model by UUID, optionally including soft-deleted."""
    query = select(Model).where(Model.uuid_id == model_id)
    if not include_deleted:
        query = query.where(Model.deleted_at.is_(None))
    res = await session.execute(query)
    return res.scalar_one_or_none()


async def patch_model(session: AsyncSession, model_id: uuid.UUID, data: ModelPatch) -> Model | None:
    """Update a model by UUID (only active models)."""
    m = await get_model(session, model_id, include_deleted=False)
    if m is None:
        return None
    for field_, value in data.model_dump(exclude_unset=True).items():
        setattr(m, field_, value)
    await session.commit()
    await session.refresh(m)
    return m


async def set_favorites(session: AsyncSession, provider_id: uuid.UUID, model_ids: list[str]) -> int:
    """Replace the favorite set for one provider (by provider UUID). Any existing favorite
    not in model_ids is cleared, every model_id in the list is set
    favorite.

    If a model_id is not yet in the models table (e.g. a freshly
    imported provider that hasn't had /v1/models synced yet), we
    create a placeholder row with the default type so the favorite
    can be restored. The probe pipeline will overwrite the placeholder
    fields (type, display_name, last_seen_at) on the next sync.
    Returns the number of rows actually changed (set or cleared)."""
    # First get the provider to get its integer ID
    provider = await session.scalar(select(Provider).where(Provider.uuid_id == provider_id))
    if provider is None:
        return 0

    desired = set(model_ids)
    changed = 0
    # Ensure every desired model_id has a row (restore if soft-deleted).
    for mid in desired:
        # Check for active model first
        existing_row = await session.scalar(
            select(Model).where(
                Model.provider_id == provider.id, Model.model_id == mid, Model.deleted_at.is_(None)
            )
        )
        if existing_row is not None:
            # Model exists and is active, just set favorite
            if not existing_row.is_favorite:
                existing_row.is_favorite = True
                changed += 1
        else:
            # Check for soft-deleted model
            deleted_row = await session.scalar(
                select(Model).where(
                    Model.provider_id == provider.id, Model.model_id == mid, Model.deleted_at.is_not(None)
                )
            )
            if deleted_row is not None:
                # Restore the soft-deleted model
                deleted_row.deleted_at = None
                deleted_row.is_favorite = True
                changed += 1
            else:
                # Create new placeholder model
                session.add(
                    Model(
                        provider_id=provider.id,
                        model_id=mid,
                        type=ModelType.chat,  # placeholder; sync will fix
                        enabled=True,
                        is_favorite=True,
                    )
                )
                changed += 1
    await session.flush()
    # Now re-read to get the merged set (existing + new placeholders)
    # with stable ORM identity.
    all_models = list(
        (
            await session.execute(
                select(Model).where(Model.provider_id == provider.id, Model.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for m in all_models:
        want = m.model_id in desired
        if want and not m.is_favorite:
            m.is_favorite = True
            changed += 1
        elif not want and m.is_favorite:
            m.is_favorite = False
            changed += 1
    await session.commit()
    # Return the total number of favorites after this call (capped at
    # the requested size), so callers can report 'favorites_restored'
    # even when the rows already had the right state. The +1 for new
    # placeholder rows above is already counted in 'changed' but only
    # reflects inserts — here we want the user-facing 'how many are
    # now favorited' number.
    return sum(1 for m in all_models if m.is_favorite)


async def upsert_discovered(
    session: AsyncSession, provider_id: uuid.UUID, discovered: list[DiscoveredModel]
) -> list[Model]:
    """Insert new models; update last_seen_at for existing; do not delete yet."""
    # First get the provider to get its integer ID
    provider = await session.scalar(select(Provider).where(Provider.uuid_id == provider_id))
    if provider is None:
        return []

    now = datetime.now(UTC)
    existing_res = await session.execute(
        select(Model).where(Model.provider_id == provider.id, Model.deleted_at.is_(None))
    )
    existing = {m.model_id: m for m in existing_res.scalars().all()}
    result: list[Model] = []
    for d in discovered:
        m = existing.get(d.model_id)
        if m is None:
            m = Model(
                provider_id=provider.id,
                model_id=d.model_id,
                display_name=d.display_name,
                type=d.type if d.type != ModelType.unknown else ModelType.chat,
                enabled=True,
                is_favorite=False,
                last_seen_at=now,
            )
            session.add(m)
        else:
            m.last_seen_at = now
            if d.display_name and not m.display_name:
                m.display_name = d.display_name
            if m.type == ModelType.unknown and d.type != ModelType.unknown:
                m.type = d.type
        result.append(m)
    await session.commit()
    for m in result:
        await session.refresh(m)
    return result


async def disable_stale(session: AsyncSession, days: int = 7) -> int:
    """Disable models not seen in the last N days (only active, non-deleted models)."""
    # SQLite returns naive datetimes; compare with naive "now".
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    res = await session.execute(
        select(Model).where(Model.last_seen_at < cutoff, Model.enabled, Model.deleted_at.is_(None))
    )
    count = 0
    for m in res.scalars().all():
        m.enabled = False
        count += 1
    await session.commit()
    return count
