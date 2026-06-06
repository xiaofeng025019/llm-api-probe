"""Probe result service: write/read + dashboard summary + retention cleanup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import ErrorCode, Model, ProbeResult, ProbeTarget, Provider
from app.db.uuid import uuid_equals
from app.probers.types import ProbeOutcome
from app.schemas.api import DashboardFavoriteModel, DashboardOut, DashboardProvider
from app.services import settings as settings_svc

ERROR_HISTORY_LIMIT = 100
ERROR_HISTORY_TTL = timedelta(minutes=15)


# How much of the prober-side error message we keep when persisting
# to the DB. Larger than MAX_UPSTREAM_ERROR_BODY_CHARS so the
# services layer has room to add context (e.g. "after N retries")
# without losing the upstream's actual error text.
MAX_PERSISTED_ERROR_MESSAGE_CHARS = 1000


# Multiplier applied to a model's effective interval to decide when
# a still-"online" model should be downgraded to "stale" in the
# dashboard view. Picked at 2× so:
#   - one missed probe (jitter, sweep overlap) never trips it;
#   - two consecutive missed probes do.
# 1× would be too twitchy (the first scheduled probe is *exactly*
# interval seconds after the previous one; jitter alone can push
# it past 1× × interval).
STALE_THRESHOLD_MULTIPLIER = 2


def _is_model_stale(
    status: str | None,
    last_checked_at: datetime | None,
    interval_seconds: int,
) -> bool:
    """True if a model that *claims* to be online is actually too
    old to trust.

    Only "online" models can become stale — anything else (offline,
    unauthorized, not_found, suspect, rate_limited, disabled) is a
    known state that we don't want to paper over with a "stale"
    label. And `last_checked_at is None` is treated as stale so
    the UI surfaces never-probed models as a candidate for manual
    probing instead of silently calling them "online".
    """
    if status != "online":
        return False
    if last_checked_at is None:
        return True
    if interval_seconds <= 0:
        # Defensive: avoid silent flip on degenerate inputs.
        return False
    threshold = timedelta(seconds=interval_seconds * STALE_THRESHOLD_MULTIPLIER)
    # SQLite via SQLAlchemy returns naive datetimes; treat naive
    # values as UTC (which is what we always store). Mixing naive
    # and aware in a subtraction raises TypeError, so normalize.
    now = datetime.now(UTC)
    checked = last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    age = now - checked
    return age > threshold


async def _model_base_interval_for_dashboard(
    session: AsyncSession, is_favorite: bool
) -> int:
    """Resolve the base (un-multiplied) interval for a model from the
    favorite/regular settings. Mirrors `_model_base_interval` in the
    scheduler so the dashboard's stale check uses the same number
    the scheduler actually schedules against.
    """
    if is_favorite:
        return await settings_svc.get_int_setting(
            session,
            settings_svc.FAVORITE_MODEL_INTERVAL_KEY,
            settings_svc.DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
        )
    return await settings_svc.get_int_setting(
        session,
        settings_svc.REGULAR_MODEL_INTERVAL_KEY,
        settings_svc.DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
    )


def _model_status_from_failure(outcome: ProbeOutcome, consecutive_failures: int, threshold: int) -> str:
    if outcome.error_code == ErrorCode.auth or outcome.http_status in (401, 403):
        return "unauthorized"
    if outcome.error_code == ErrorCode.rate_limit or outcome.http_status == 429:
        return "rate_limited"
    message = (outcome.error_message or "").lower()
    if outcome.http_status == 404 or "model_not_found" in message or "model not found" in message:
        return "not_found"
    return "offline" if consecutive_failures >= threshold else "suspect"


async def _update_model_status(session: AsyncSession, model: Model, outcome: ProbeOutcome) -> None:
    now = datetime.now(UTC)
    previous_status = model.status
    model.status_checked_at = now

    if outcome.success:
        model.status = "online"
        model.status_reason = None
        model.consecutive_failures = 0
        model.last_success_at = now
        if previous_status != model.status or model.status_confirmed_at is None:
            model.status_confirmed_at = now
        return

    model.consecutive_failures = int(model.consecutive_failures or 0) + 1
    threshold_key = (
        settings_svc.FAVORITE_MODEL_FAILURE_CONFIRMATIONS_KEY
        if model.is_favorite
        else settings_svc.REGULAR_MODEL_FAILURE_CONFIRMATIONS_KEY
    )
    threshold_default = (
        settings_svc.DEFAULT_FAVORITE_MODEL_FAILURE_CONFIRMATIONS
        if model.is_favorite
        else settings_svc.DEFAULT_REGULAR_MODEL_FAILURE_CONFIRMATIONS
    )
    threshold = await settings_svc.get_int_setting(session, threshold_key, threshold_default, minimum=1)
    model.status = _model_status_from_failure(outcome, model.consecutive_failures, threshold)
    model.status_reason = (
        outcome.error_code.value
        if outcome.error_code is not None
        else f"HTTP {outcome.http_status}"
        if outcome.http_status is not None
        else "probe_failed"
    )
    if previous_status != model.status or model.status_confirmed_at is None:
        model.status_confirmed_at = now


async def record_outcome(
    session: AsyncSession,
    provider: Provider,
    model_db_id: int | None,
    target: ProbeTarget,
    outcome: ProbeOutcome,
) -> ProbeResult:
    """Record a probe outcome with snapshot fields for historical integrity.

    The row stores two kinds of cross-references:

    1. **Integer FKs** (`provider_id`, `model_id`) — fast JOINs for
       live lookups, but they go NULL or get reassigned when rows
       are hard-deleted or schemas are reseeded.
    2. **UUID snapshots** (`provider_uuid_at_probe`,
       `model_uuid_at_probe`) plus **string snapshots**
       (`provider_name_at_probe`, `model_id_at_probe`) — stable
       identifiers that survive hard-deletes, renames, and
       cross-database restores.

    Use the UUID snapshots for any historical reporting or
    cross-referencing that must stay valid across the lifecycle of the
    provider/model.
    """
    model = await session.get(Model, model_db_id) if model_db_id is not None else None
    row = ProbeResult(
        provider_id=provider.id,
        model_id=model_db_id,
        target=target,
        success=outcome.success,
        http_status=outcome.http_status,
        latency_ms=outcome.latency_ms,
        ttfb_ms=outcome.ttfb_ms,
        error_code=outcome.error_code,
        error_message=(outcome.error_message[:MAX_PERSISTED_ERROR_MESSAGE_CHARS] if outcome.error_message else None),
        retry_after_seconds=outcome.retry_after_seconds,
        # String snapshots — the upstream-visible identity at probe time.
        provider_name_at_probe=provider.name,
        model_id_at_probe=model.model_id if model else None,
        # UUID snapshots — the stable internal identifier of the target.
        # `provider_uuid_at_probe` is always set (every probe has a
        # provider). `model_uuid_at_probe` is NULL for probes without
        # a model (e.g. list_models probes when no model row matched).
        provider_uuid_at_probe=provider.uuid_id,
        model_uuid_at_probe=model.uuid_id if model else None,
        # Explicit timestamp so we don't rely on the DB DEFAULT
        # (which can be lost during SQLite table-rebuild migrations).
        checked_at=datetime.now(UTC),
    )
    session.add(row)
    if target == ProbeTarget.chat_completion and model is not None:
        await _update_model_status(session, model, outcome)
    await session.commit()
    await session.refresh(row)
    # Note: cleanup_error_history used to run here on every failed probe.
    # It now runs on its own periodic job (scheduler.cleanup_error_history_loop)
    # to keep the probe hot path free of two DELETE round-trips per failure.
    return row


async def list_results(
    session: AsyncSession,
    provider_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 200,
) -> list[ProbeResult]:
    """List probe results, optionally filtered by provider/model UUID."""
    stmt = (
        select(ProbeResult)
        .options(joinedload(ProbeResult.model), joinedload(ProbeResult.provider_rel))
        .order_by(ProbeResult.checked_at.desc())
        .limit(limit)
    )

    if provider_id is not None:
        # Join with Provider to filter by UUID
        stmt = stmt.join(Provider, ProbeResult.provider_id == Provider.id).where(
            uuid_equals(Provider.uuid_id, provider_id)
        )
    if model_id is not None:
        # Join with Model to filter by UUID
        stmt = stmt.join(Model, ProbeResult.model_id == Model.id).where(uuid_equals(Model.uuid_id, model_id))
    if since is not None:
        stmt = stmt.where(ProbeResult.checked_at >= since)

    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_recent_errors(
    session: AsyncSession,
    limit: int = ERROR_HISTORY_LIMIT,
) -> list[ProbeResult]:
    """Return failed probe results for the error history page.

    Pinned rows float to the top regardless of recency, ordered
    by `checked_at DESC` among themselves; non-pinned rows are
    ordered by `checked_at DESC` below them. The list is bounded
    by `limit` total rows and the short error-history window.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - ERROR_HISTORY_TTL
    # SQLite's CASE expression maps to ORDER BY.
    pinned_first = (ProbeResult.pinned.is_(True)).desc()
    stmt = (
        select(ProbeResult)
        .options(joinedload(ProbeResult.model), joinedload(ProbeResult.provider_rel))
        .where(ProbeResult.success.is_(False), ProbeResult.checked_at >= cutoff)
        .order_by(pinned_first, ProbeResult.checked_at.desc())
        .limit(min(limit, ERROR_HISTORY_LIMIT))
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def cleanup_error_history(session: AsyncSession) -> int:
    """Keep the visible error history short-lived and bounded.

    Error history is an operator-facing notification log, not the long-term
    metrics table. Failed rows older than 15 minutes are removed, then the
    newest 100 failed rows are retained.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - ERROR_HISTORY_TTL
    removed = 0
    old = await session.execute(
        delete(ProbeResult).where(
            ProbeResult.success.is_(False),
            ProbeResult.checked_at < cutoff,
        )
    )
    removed += getattr(old, "rowcount", 0) or 0

    keep_ids = (
        select(ProbeResult.id)
        .where(ProbeResult.success.is_(False))
        .order_by(ProbeResult.checked_at.desc())
        .limit(ERROR_HISTORY_LIMIT)
    )
    excess = await session.execute(
        delete(ProbeResult).where(
            ProbeResult.success.is_(False),
            ProbeResult.id.not_in(keep_ids),
        )
    )
    removed += getattr(excess, "rowcount", 0) or 0
    await session.commit()
    return removed


async def pin_probe_result(session: AsyncSession, probe_uuid: uuid.UUID) -> bool:
    """Mark a probe_result as pinned. Only failure rows can be pinned
    (a "pinned success" is meaningless — there's nothing to track)."""
    res = await session.execute(
        select(ProbeResult).where(uuid_equals(ProbeResult.uuid_id, probe_uuid))
    )
    row = res.scalar_one_or_none()
    if row is None or row.success:
        return False
    row.pinned = True
    await session.commit()
    return True


async def unpin_probe_result(session: AsyncSession, probe_uuid: uuid.UUID) -> bool:
    """Remove the pinned flag. The row itself stays in the table; the
    regular retention cleanup will eventually delete it."""
    res = await session.execute(
        select(ProbeResult).where(uuid_equals(ProbeResult.uuid_id, probe_uuid))
    )
    row = res.scalar_one_or_none()
    if row is None:
        return False
    row.pinned = False
    await session.commit()
    return True


async def cleanup_old(session: AsyncSession, retention_days: int) -> int:
    # SQLite returns naive datetimes from the column; compare with naive "now"
    # to avoid offset-aware vs naive comparison errors.
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=retention_days)
    # Pinned rows are exempt from retention cleanup — the user
    # explicitly asked to keep them. Without this filter, a pinned
    # error the user cares about would silently disappear after
    # `retention_days`, and the pin becomes meaningless.
    res = await session.execute(
        delete(ProbeResult).where(
            ProbeResult.checked_at < cutoff,
            ProbeResult.pinned.is_(False),
        )
    )
    await session.commit()
    return getattr(res, "rowcount", 0)


def _provider_health_status(
    *,
    provider_enabled: bool,
    latest_list_models: ProbeResult | None,
    enabled_model_ids: list[int],
    recent_per_model: dict[int, bool],
) -> str | None:
    """Compute the rollup status shown in the dashboard.

    Semantics:
    - `disabled` if the provider itself is off.
    - `fail` if the most recent list_models probe failed (the upstream
      API is unreachable or unauthorized).
    - `ok` if every enabled model that has been probed at least once
      came back successful on its most recent probe.
    - `fail` if every probed model is currently failing.
    - `degraded` if the probed models are mixed (some online, some
      offline).
    - `None` if no enabled model has ever been probed yet and the
      list_models probe has not landed — we have no signal to report.

    Important: an enabled model that has *never* been probed is
    intentionally excluded from the ok/fail/degraded calculation.
    We only count models we have actual signal on. A 100%-online
    list_models probe with 0 chat probes yet is NOT "degraded"; it
    has no chat signal yet so we wait.
    """
    if not provider_enabled:
        return None
    if latest_list_models is not None and not latest_list_models.success:
        return "fail"

    if enabled_model_ids:
        known = [recent_per_model[m_id] for m_id in enabled_model_ids if m_id in recent_per_model]
        if known:
            online = sum(1 for ok in known if ok)
            if online == len(known):
                return "ok"
            if online == 0:
                return "fail"
            return "degraded"
        # No chat probes yet, but list_models succeeded → just hasn't
        # reached the per-model cadence yet. Don't report "degraded".
        if latest_list_models is not None and latest_list_models.success:
            return None
        return None

    if latest_list_models is not None:
        return "ok" if latest_list_models.success else "fail"
    return None


def _p95_ms(rows: list[ProbeResult], field: str) -> int | None:
    values = sorted(int(value) for row in rows if (value := getattr(row, field)) is not None)
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) * 0.95) + 0.999999) - 1))
    return values[index]


def _error_counts(rows: list[ProbeResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.success or row.error_code is None:
            continue
        key = row.error_code.value
        counts[key] = counts.get(key, 0) + 1
    return counts


async def dashboard(session: AsyncSession) -> DashboardOut:
    """Build the dashboard payload. Three independent hot-paths, all
    batched:

    1. Models across all providers — one SELECT (was N)
    2. "Latest probe per model" — one query with a window function
       that returns at most one row per model_id (was unbounded
       SELECT * ordered by checked_at DESC, materialised in Python).
       The window function uses the (model_id, checked_at) composite
       index added in `ix_probe_results_model_time`.
    3. Pre-24h snapshot — one SELECT (was unbounded SELECT, was
       3 of these).
    """
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # Only include non-deleted providers
    providers = list(
        (await session.execute(select(Provider).where(Provider.deleted_at.is_(None)).order_by(Provider.id)))
        .scalars()
        .all()
    )

    # One query: every model for every active provider, indexed by
    # provider_id for the per-provider loop below.
    models_by_provider: dict[int, list[Model]] = {}
    if providers:
        provider_ids = [p.id for p in providers]
        all_models = (
            (
                await session.execute(
                    select(Model).where(
                        Model.provider_id.in_(provider_ids),
                        Model.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for m in all_models:
            models_by_provider.setdefault(m.provider_id, []).append(m)

    # One query: 24h probe rows, bucketed by provider AND by model.
    # We still materialise the rowset in Python (it's bounded by
    # retention × fleet), but only once per dashboard render instead
    # of N times (one per provider).
    recent_per_model: dict[int, bool] = {}
    recent_counts_by_model: dict[int, tuple[int, int]] = {}
    recent_results_by_model: dict[int, list[ProbeResult]] = {}
    recent_results_by_provider: dict[int, list[ProbeResult]] = {}
    recent_rows = (
        (
            await session.execute(
                select(ProbeResult)
                .where(ProbeResult.checked_at >= cutoff_24h)
                .order_by(ProbeResult.checked_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in recent_rows:
        recent_results_by_provider.setdefault(row.provider_id, []).append(row)
        model_id = row.model_id
        if model_id is None:
            continue
        total, succeeded = recent_counts_by_model.get(model_id, (0, 0))
        recent_counts_by_model[model_id] = (total + 1, succeeded + (1 if row.success else 0))
        recent_results_by_model.setdefault(model_id, []).append(row)
        if model_id not in recent_per_model:
            recent_per_model[model_id] = bool(row.success)

    # One query: the latest probe result per model, using a window
    # function. Returns at most N rows (one per model), regardless of
    # how big the probe_results table is. The composite index
    # `ix_probe_results_model_time` is leftmost-prefixed by model_id so
    # SQLite uses it for both the window and the order.
    #
    # The previous version of this code pulled SELECT * from
    # probe_results ordered by checked_at DESC into Python and then
    # deduped by model_id — at 30-day retention that was the worst
    # hot path in the dashboard.
    from sqlalchemy import func as _sa_func

    latest_by_model: dict[int, ProbeResult] = {}
    latest_rows = (
        (
            await session.execute(
                select(
                    ProbeResult,
                    _sa_func.row_number()
                    .over(partition_by=ProbeResult.model_id, order_by=ProbeResult.checked_at.desc())
                    .label("rn"),
                ).where(ProbeResult.model_id.is_not(None))
            )
        )
        .all()
    )
    for row, rn in latest_rows:
        if rn != 1:
            continue
        assert row.model_id is not None  # narrowed by WHERE; satisfy type checker
        latest_by_model[row.model_id] = row

    # Pre-24h snapshot for the Favorite Models delta: which favorites
    # were online (latest success) at the time the 24h window started?
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
        "favorites_online_24h_ago": 0,
    }

    for p in providers:
        models = models_by_provider.get(p.id, [])
        last_row = recent_results_by_provider.get(p.id, [None])[0] if recent_results_by_provider.get(p.id) else None
        # Avoid the per-provider "latest row" round-trip — the per-provider
        # recent_results_by_provider is already sorted by checked_at desc.
        latest_list_models = None
        for r in recent_results_by_provider.get(p.id, []):
            if r.target == ProbeTarget.list_models:
                latest_list_models = r
                break
        # 24h aggregate — push the count/sum/avg into SQL, no row materialise.
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
        provider_recent_results = recent_results_by_provider.get(p.id, [])
        enabled_models = [m for m in models if m.enabled]
        provider_status = _provider_health_status(
            provider_enabled=p.enabled,
            latest_list_models=latest_list_models,
            enabled_model_ids=[m.id for m in enabled_models],
            recent_per_model=recent_per_model,
        )

        favorites = [m for m in models if m.is_favorite]
        available_models_online = sum(1 for m in models if recent_per_model.get(m.id))
        online = sum(1 for m in favorites if recent_per_model.get(m.id)) if favorites else 0
        favorite_models = []
        for m in favorites:
            latest = latest_by_model.get(m.id)
            recent_total, recent_success = recent_counts_by_model.get(m.id, (0, 0))
            model_recent_results = recent_results_by_model.get(m.id, [])
            availability_24h = round(recent_success / recent_total * 100, 2) if recent_total > 0 else None
            favorite_models.append(
                DashboardFavoriteModel(
                    id=m.uuid_id,
                    model_id=m.model_id,
                    display_name=m.display_name,
                    type=m.type,
                    enabled=m.enabled,
                    # "stale" is a *display* status — the DB still says
                    # "online", we just demote it in the API response
                    # if the last probe is older than
                    # STALE_THRESHOLD_MULTIPLIER × interval. This way
                    # the dashboard can tell the user "this hasn't
                    # been checked in a while" without us writing a
                    # derived state to the model table.
                    status=(
                        "stale"
                        if _is_model_stale(
                            m.status,
                            latest.checked_at if latest else None,
                            await _model_base_interval_for_dashboard(session, m.is_favorite),
                        )
                        else m.status
                    ),
                    status_reason=m.status_reason,
                    status_checked_at=m.status_checked_at,
                    status_confirmed_at=m.status_confirmed_at,
                    last_success_at=m.last_success_at,
                    last_checked_at=latest.checked_at if latest else None,
                    latency_ms=latest.latency_ms if latest else None,
                    ttfb_ms=latest.ttfb_ms if latest else None,
                    error_code=latest.error_code if latest else None,
                    error_message=latest.error_message if latest else None,
                    availability_24h=availability_24h,
                    samples_24h=recent_total,
                    p95_latency_ms_24h=_p95_ms(model_recent_results, "latency_ms"),
                    p95_ttfb_ms_24h=_p95_ms(model_recent_results, "ttfb_ms"),
                    consecutive_failures=m.consecutive_failures,
                )
            )

        out.append(
            DashboardProvider(
                provider_id=p.uuid_id,
                name=p.name,
                kind=p.kind,
                base_url=p.base_url,
                enabled=p.enabled,
                interval_seconds=p.interval_seconds,
                timeout_seconds=p.timeout_seconds,
                proxy=p.proxy,
                headers_json=p.headers_json,
                model_count=len(models),
                last_checked_at=last_row.checked_at if last_row else None,
                last_status=provider_status,
                availability_24h=avail,
                avg_latency_ms_24h=avg_lat_ms,
                p95_latency_ms_24h=_p95_ms(provider_recent_results, "latency_ms"),
                p95_ttfb_ms_24h=_p95_ms(provider_recent_results, "ttfb_ms"),
                samples_24h=int(cnt or 0),
                failures_24h=int((cnt or 0) - (succ or 0)),
                error_counts_24h=_error_counts(provider_recent_results),
                list_models_status=(
                    "ok"
                    if latest_list_models and latest_list_models.success
                    else "fail"
                    if latest_list_models
                    else None
                ),
                list_models_latency_ms=latest_list_models.latency_ms if latest_list_models else None,
                list_models_checked_at=latest_list_models.checked_at if latest_list_models else None,
                list_models_error_code=latest_list_models.error_code if latest_list_models else None,
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
        for m in models:
            if recent_per_model.get(m.id):
                totals["available_models"] += 1
        for m in favorites:
            if pre_24h_per_model.get(m.id):
                totals["favorites_online_24h_ago"] += 1
        totals["favorites_total"] += len(favorites)
        totals["favorites_online"] += online

    return DashboardOut(providers=out, totals=totals)
