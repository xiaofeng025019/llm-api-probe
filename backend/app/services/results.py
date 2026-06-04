"""Probe result service: write/read + dashboard summary + retention cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, ProbeResult, ProbeTarget, Provider
from app.probers.types import ProbeOutcome
from app.schemas.api import DashboardFavoriteModel, DashboardOut, DashboardProvider


async def record_outcome(
    session: AsyncSession,
    provider: Provider,
    model_db_id: int | None,
    target: ProbeTarget,
    outcome: ProbeOutcome,
) -> ProbeResult:
    """Record a probe outcome with snapshot fields for historical integrity."""
    # Get model_id string for snapshot (if applicable)
    model_id_str = None
    if model_db_id is not None:
        model = await session.get(Model, model_db_id)
        if model is not None:
            model_id_str = model.model_id

    row = ProbeResult(
        provider_id=provider.id,
        model_id=model_db_id,
        target=target,
        success=outcome.success,
        http_status=outcome.http_status,
        latency_ms=outcome.latency_ms,
        ttfb_ms=outcome.ttfb_ms,
        error_code=outcome.error_code,
        error_message=(outcome.error_message[:1000] if outcome.error_message else None),
        # Snapshot fields - capture state at probe time
        provider_name_at_probe=provider.name,
        model_id_at_probe=model_id_str,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_results(
    session: AsyncSession,
    provider_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[ProbeResult]:
    """List probe results, optionally filtered by provider/model UUID."""
    stmt = select(ProbeResult).order_by(ProbeResult.checked_at.desc()).limit(limit)

    if provider_id is not None:
        # Join with Provider to filter by UUID
        stmt = stmt.join(Provider, ProbeResult.provider_id == Provider.id).where(
            Provider.uuid_id == provider_id
        )
    if model_id is not None:
        # Join with Model to filter by UUID
        stmt = stmt.join(Model, ProbeResult.model_id == Model.id).where(Model.uuid_id == model_id)
    if since is not None:
        stmt = stmt.where(ProbeResult.checked_at >= since)

    res = await session.execute(stmt)
    return list(res.scalars().all())


async def cleanup_old(session: AsyncSession, retention_days: int) -> int:
    # SQLite returns naive datetimes from the column; compare with naive "now"
    # to avoid offset-aware vs naive comparison errors.
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=retention_days)
    res = await session.execute(delete(ProbeResult).where(ProbeResult.checked_at < cutoff))
    await session.commit()
    return getattr(res, "rowcount", 0)


def _provider_health_status(
    *,
    provider_enabled: bool,
    latest_list_models: ProbeResult | None,
    enabled_model_ids: list[int],
    recent_per_model: dict[int, bool],
) -> str | None:
    if not provider_enabled:
        return None
    if latest_list_models is not None and not latest_list_models.success:
        return "fail"

    if enabled_model_ids:
        known = [recent_per_model[m_id] for m_id in enabled_model_ids if m_id in recent_per_model]
        if known:
            online = sum(1 for ok in known if ok)
            if online == len(enabled_model_ids):
                return "ok"
            if online == 0 and len(known) == len(enabled_model_ids):
                return "fail"
            return "degraded"
        if latest_list_models is not None and latest_list_models.success:
            return "degraded"
        return None

    if latest_list_models is not None:
        return "ok" if latest_list_models.success else "fail"
    return None


async def dashboard(session: AsyncSession) -> DashboardOut:
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # Only include non-deleted providers
    providers = list(
        (await session.execute(select(Provider).where(Provider.deleted_at.is_(None)).order_by(Provider.id)))
        .scalars()
        .all()
    )

    # Single query to compute "most recent probe result per model" in the
    # past 24h. ORDER BY checked_at DESC, then we keep the first row seen
    # per model_id. Done in Python instead of a window function so it works
    # on every SQLite version without ROW_NUMBER().
    recent_per_model: dict[int, bool] = {}
    recent_counts_by_model: dict[int, tuple[int, int]] = {}
    rows = (
        await session.execute(
            select(ProbeResult.model_id, ProbeResult.success, ProbeResult.checked_at)
            .where(ProbeResult.model_id.is_not(None), ProbeResult.checked_at >= cutoff_24h)
            .order_by(ProbeResult.checked_at.desc())
        )
    ).all()
    for model_id, success, _ in rows:
        if model_id is None:
            continue
        total, succeeded = recent_counts_by_model.get(model_id, (0, 0))
        recent_counts_by_model[model_id] = (total + 1, succeeded + (1 if success else 0))
        if model_id not in recent_per_model:
            recent_per_model[model_id] = bool(success)

    latest_by_model: dict[int, ProbeResult] = {}
    latest_rows = (
        (
            await session.execute(
                select(ProbeResult)
                .where(ProbeResult.model_id.is_not(None))
                .order_by(ProbeResult.checked_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in latest_rows:
        if row.model_id is None or row.model_id in latest_by_model:
            continue
        latest_by_model[row.model_id] = row

    # Pre-24h snapshot for the Favorite Models delta: which favorites
    # were online (latest success) at the time the 24h window started?
    # Same per-model dedup trick as recent_per_model, but bounded above
    # by cutoff_24h instead of below.
    pre_24h_per_model: dict[int, bool] = {}
    pre_rows = (
        await session.execute(
            select(ProbeResult.model_id, ProbeResult.success, ProbeResult.checked_at)
            .where(
                ProbeResult.model_id.is_not(None),
                ProbeResult.checked_at < cutoff_24h,
            )
            .order_by(ProbeResult.checked_at.desc())
        )
    ).all()
    for model_id, success, _ in pre_rows:
        if model_id is None or model_id in pre_24h_per_model:
            continue
        pre_24h_per_model[model_id] = bool(success)

    out: list[DashboardProvider] = []
    totals = {
        "providers": 0,
        "models": 0,
        "ok": 0,
        "degraded": 0,
        "failing": 0,
        "available_models": 0,
        "favorites_online": 0,
        "favorites_total": 0,
        "favorites_online_24h_ago": 0,  # for delta display
    }

    for p in providers:
        # Only include non-deleted models
        models = list(
            (
                await session.execute(
                    select(Model).where(Model.provider_id == p.id, Model.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        last = await session.execute(
            select(ProbeResult)
            .where(ProbeResult.provider_id == p.id)
            .order_by(ProbeResult.checked_at.desc())
            .limit(1)
        )
        last_row = last.scalars().first()
        latest_list_models = (
            (
                await session.execute(
                    select(ProbeResult)
                    .where(
                        ProbeResult.provider_id == p.id,
                        ProbeResult.target == ProbeTarget.list_models,
                    )
                    .order_by(ProbeResult.checked_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        recent = await session.execute(
            select(
                func.count(ProbeResult.id),
                func.sum(func.iif(ProbeResult.success.is_(True), 1, 0)),
                func.avg(ProbeResult.latency_ms),
            ).where(ProbeResult.provider_id == p.id, ProbeResult.checked_at >= cutoff_24h)
        )
        cnt, succ, avg_lat = recent.one()
        avail: float | None = None
        if cnt and cnt > 0:
            avail = round((succ or 0) / cnt * 100, 2)
        avg_lat_ms = int(avg_lat) if avg_lat is not None else None
        enabled_models = [m for m in models if m.enabled]
        provider_status = _provider_health_status(
            provider_enabled=p.enabled,
            latest_list_models=latest_list_models,
            enabled_model_ids=[m.id for m in enabled_models],
            recent_per_model=recent_per_model,
        )

        favorites = [m for m in models if m.is_favorite]
        available_models_online = sum(1 for m in models if recent_per_model.get(m.id))
        # Use the same "most recent probe per model" semantics as
        # available_models: a favorite is "online" iff its latest
        # 24h probe succeeded. The previous distinct() logic counted
        # any successful probe in the window, which double-counted
        # a model that flipped from success → failure.
        online = sum(1 for m in favorites if recent_per_model.get(m.id)) if favorites else 0
        favorite_models = []
        for m in favorites:
            latest = latest_by_model.get(m.id)
            recent_total, recent_success = recent_counts_by_model.get(m.id, (0, 0))
            availability_24h = round(recent_success / recent_total * 100, 2) if recent_total > 0 else None
            favorite_models.append(
                DashboardFavoriteModel(
                    id=m.uuid_id,
                    model_id=m.model_id,
                    display_name=m.display_name,
                    type=m.type,
                    enabled=m.enabled,
                    status=("ok" if latest and latest.success else "fail" if latest else None),
                    last_checked_at=latest.checked_at if latest else None,
                    latency_ms=latest.latency_ms if latest else None,
                    ttfb_ms=latest.ttfb_ms if latest else None,
                    error_code=latest.error_code if latest else None,
                    error_message=latest.error_message if latest else None,
                    availability_24h=availability_24h,
                )
            )

        out.append(
            DashboardProvider(
                provider_id=p.uuid_id,
                name=p.name,
                kind=p.kind,
                enabled=p.enabled,
                model_count=len(models),
                last_checked_at=last_row.checked_at if last_row else None,
                last_status=provider_status,
                availability_24h=avail,
                avg_latency_ms_24h=avg_lat_ms,
                available_models_online=available_models_online,
                favorite_models_online=online,
                favorite_models_total=len(favorites),
                favorite_models=favorite_models,
            )
        )
        totals["providers"] += 1
        totals["models"] += len(models)
        if provider_status == "ok":
            totals["ok"] += 1
        elif provider_status == "degraded":
            totals["degraded"] += 1
        elif provider_status == "fail":
            totals["failing"] += 1
        # Model-level: count how many of this provider's models had a
        # successful probe in the last 24h.
        for m in models:
            if recent_per_model.get(m.id):
                totals["available_models"] += 1
        # Pre-24h favorites online (used to compute the delta vs current).
        for m in favorites:
            if pre_24h_per_model.get(m.id):
                totals["favorites_online_24h_ago"] += 1
        totals["favorites_total"] += len(favorites)
        totals["favorites_online"] += online

    return DashboardOut(providers=out, totals=totals)
