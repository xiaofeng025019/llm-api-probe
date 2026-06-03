"""Probe result service: write/read + dashboard summary + retention cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, ProbeResult, ProbeTarget, Provider
from app.probers.types import ProbeOutcome
from app.schemas.api import DashboardOut, DashboardProvider


async def record_outcome(
    session: AsyncSession,
    provider: Provider,
    model_db_id: int | None,
    target: ProbeTarget,
    outcome: ProbeOutcome,
) -> ProbeResult:
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
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_results(
    session: AsyncSession,
    provider_id: int | None = None,
    model_id: int | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[ProbeResult]:
    stmt = select(ProbeResult).order_by(ProbeResult.checked_at.desc()).limit(limit)
    if provider_id is not None:
        stmt = stmt.where(ProbeResult.provider_id == provider_id)
    if model_id is not None:
        stmt = stmt.where(ProbeResult.model_id == model_id)
    if since is not None:
        stmt = stmt.where(ProbeResult.checked_at >= since)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def cleanup_old(session: AsyncSession, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    res = await session.execute(delete(ProbeResult).where(ProbeResult.checked_at < cutoff))
    await session.commit()
    return getattr(res, "rowcount", 0)


async def dashboard(session: AsyncSession) -> DashboardOut:
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    providers = list((await session.execute(select(Provider).order_by(Provider.id))).scalars().all())
    out: list[DashboardProvider] = []
    totals = {"providers": 0, "models": 0, "ok": 0, "failing": 0, "favorites_online": 0, "favorites_total": 0}

    for p in providers:
        models = list((await session.execute(select(Model).where(Model.provider_id == p.id))).scalars().all())
        last = await session.execute(
            select(ProbeResult)
            .where(ProbeResult.provider_id == p.id)
            .order_by(ProbeResult.checked_at.desc())
            .limit(1)
        )
        last_row = last.scalars().first()
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

        favorites = [m for m in models if m.is_favorite]
        if favorites:
            fav_ids = [m.id for m in favorites]
            # A favorite is "online" if it had at least one successful probe in 24h.
            online_count_q = (
                select(ProbeResult.model_id)
                .where(
                    ProbeResult.model_id.in_(fav_ids),
                    ProbeResult.checked_at >= cutoff_24h,
                    ProbeResult.success.is_(True),
                )
                .distinct()
            )
            online_ids = {row[0] for row in (await session.execute(online_count_q)).all()}
            online = len(online_ids)
        else:
            online = 0

        out.append(
            DashboardProvider(
                provider_id=p.id,
                name=p.name,
                kind=p.kind,
                enabled=p.enabled,
                model_count=len(models),
                last_checked_at=last_row.checked_at if last_row else None,
                last_status=("ok" if last_row and last_row.success else "fail" if last_row else None),
                availability_24h=avail,
                avg_latency_ms_24h=avg_lat_ms,
                favorite_models_online=online,
                favorite_models_total=len(favorites),
            )
        )
        totals["providers"] += 1
        totals["models"] += len(models)
        if last_row and last_row.success:
            totals["ok"] += 1
        elif last_row:
            totals["failing"] += 1
        totals["favorites_total"] += len(favorites)
        totals["favorites_online"] += online

    return DashboardOut(providers=out, totals=totals)
