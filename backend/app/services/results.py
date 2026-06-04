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
    # SQLite returns naive datetimes from the column; compare with naive "now"
    # to avoid offset-aware vs naive comparison errors.
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=retention_days)
    res = await session.execute(delete(ProbeResult).where(ProbeResult.checked_at < cutoff))
    await session.commit()
    return getattr(res, "rowcount", 0)


async def dashboard(session: AsyncSession) -> DashboardOut:
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    providers = list((await session.execute(select(Provider).order_by(Provider.id))).scalars().all())

    # Single query to compute "most recent probe result per model" in the
    # past 24h. ORDER BY checked_at DESC, then we keep the first row seen
    # per model_id. Done in Python instead of a window function so it works
    # on every SQLite version without ROW_NUMBER().
    recent_per_model: dict[int, bool] = {}
    rows = (
        await session.execute(
            select(ProbeResult.model_id, ProbeResult.success, ProbeResult.checked_at)
            .where(ProbeResult.model_id.is_not(None), ProbeResult.checked_at >= cutoff_24h)
            .order_by(ProbeResult.checked_at.desc())
        )
    ).all()
    for model_id, success, _ in rows:
        if model_id is None or model_id in recent_per_model:
            continue
        recent_per_model[model_id] = bool(success)

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
        "failing": 0,
        "available_models": 0,
        "favorites_online": 0,
        "favorites_total": 0,
        "favorites_online_24h_ago": 0,  # for delta display
    }

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
        # Use the same "most recent probe per model" semantics as
        # available_models: a favorite is "online" iff its latest
        # 24h probe succeeded. The previous distinct() logic counted
        # any successful probe in the window, which double-counted
        # a model that flipped from success → failure.
        online = sum(1 for m in favorites if recent_per_model.get(m.id)) if favorites else 0

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
