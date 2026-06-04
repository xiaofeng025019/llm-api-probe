"""Model service: upsert discovered models, patch enabled/favorite, prune stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, ModelType
from app.probers.types import DiscoveredModel
from app.schemas.api import ModelPatch


async def list_models(session: AsyncSession, provider_id: int) -> list[Model]:
    res = await session.execute(
        select(Model).where(Model.provider_id == provider_id).order_by(Model.model_id)
    )
    return list(res.scalars().all())


async def get_model(session: AsyncSession, model_id: int) -> Model | None:
    return await session.get(Model, model_id)


async def patch_model(session: AsyncSession, model_id: int, data: ModelPatch) -> Model | None:
    m = await session.get(Model, model_id)
    if m is None:
        return None
    for field_, value in data.model_dump(exclude_unset=True).items():
        setattr(m, field_, value)
    await session.commit()
    await session.refresh(m)
    return m


async def set_favorites(session: AsyncSession, provider_id: int, model_ids: list[str]) -> int:
    """Replace the favorite set for one provider. Any existing favorite
    not in model_ids is cleared, every model_id in the list is set
    favorite.

    If a model_id is not yet in the models table (e.g. a freshly
    imported provider that hasn't had /v1/models synced yet), we
    create a placeholder row with the default type so the favorite
    can be restored. The probe pipeline will overwrite the placeholder
    fields (type, display_name, last_seen_at) on the next sync.
    Returns the number of rows actually changed (set or cleared)."""
    desired = set(model_ids)
    changed = 0
    # Ensure every desired model_id has a row.
    for mid in desired:
        existing_row = await session.scalar(
            select(Model).where(Model.provider_id == provider_id, Model.model_id == mid)
        )
        if existing_row is None:
            session.add(
                Model(
                    provider_id=provider_id,
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
        (await session.execute(select(Model).where(Model.provider_id == provider_id))).scalars().all()
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
    session: AsyncSession, provider_id: int, discovered: list[DiscoveredModel]
) -> list[Model]:
    """Insert new models; update last_seen_at for existing; do not delete yet."""
    now = datetime.now(UTC)
    existing_res = await session.execute(select(Model).where(Model.provider_id == provider_id))
    existing = {m.model_id: m for m in existing_res.scalars().all()}
    result: list[Model] = []
    for d in discovered:
        m = existing.get(d.model_id)
        if m is None:
            m = Model(
                provider_id=provider_id,
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
    # SQLite returns naive datetimes; compare with naive "now".
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    res = await session.execute(select(Model).where(Model.last_seen_at < cutoff, Model.enabled))
    count = 0
    for m in res.scalars().all():
        m.enabled = False
        count += 1
    await session.commit()
    return count
