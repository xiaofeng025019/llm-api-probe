"""APScheduler bootstrap, job sync, and probe task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.config import get_settings
from app.core.http import get_client
from app.core.sse import get_sse
from app.db.models import (
    ErrorCode,
    JobState,
    Model,
    ProbeResult,
    ProbeTarget,
    Provider,
)
from app.db.session import get_session_maker
from app.db.uuid import uuid_equals
from app.probers import get_prober
from app.probers.types import ProbeOutcome
from app.services import results as results_svc
from app.services import settings as settings_svc
from app.services.models import disable_stale, upsert_discovered

log = logging.getLogger(__name__)


_scheduler: AsyncIOScheduler | None = None
_root_sem: asyncio.Semaphore | None = None
_provider_sems: dict[uuid.UUID, asyncio.Semaphore] = {}


def _job_key(provider_uuid: uuid.UUID, model_uuid: uuid.UUID | None, target: ProbeTarget) -> str:
    model_part = str(model_uuid) if model_uuid else "-"
    return f"p{provider_uuid}:{target.value}:m{model_part}"


def _make_job_id(provider_uuid: uuid.UUID, model_uuid: uuid.UUID | None, target: ProbeTarget) -> str:
    return _job_key(provider_uuid, model_uuid, target)


def get_root_sem() -> asyncio.Semaphore:
    global _root_sem
    if _root_sem is None:
        _root_sem = asyncio.Semaphore(get_settings().max_concurrency)
    return _root_sem


def _get_provider_sem(provider_uuid: uuid.UUID) -> asyncio.Semaphore:
    sem = _provider_sems.get(provider_uuid)
    if sem is None:
        # default: permit=1 (sequential per provider)
        sem = asyncio.Semaphore(1)
        _provider_sems[provider_uuid] = sem
    return sem


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        settings = get_settings()
        # APScheduler SQLAlchemyJobStore wants a sync engine. We build a small
        # sync engine with busy_timeout so it waits on the writer lock held by
        # the async app connections instead of raising immediately.
        from sqlalchemy import create_engine as _create_sync_engine
        from sqlalchemy import event as _sa_event

        sync_engine = _create_sync_engine(
            settings.sync_database_url,
            connect_args={"check_same_thread": False, "timeout": 5},
        )

        @_sa_event.listens_for(sync_engine, "connect")
        def _set_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        _scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(engine=sync_engine)},
            timezone=settings.tz,
        )
    return _scheduler


# ---------- probe job task --------------------------------------------------

# Per-provider sliding-window rate limiter. Each provider gets its own
# deque of request timestamps; the deque is pruned to the last 60s on
# each call, and the request is allowed iff the deque has fewer than
# the configured limit entries.
#
# Why sliding-window instead of token-bucket: a sliding window gives
# the user a precise "no more than N in any 60s" guarantee. A token
# bucket at 20/minute with a 60s refill would let a brief burst exceed
# 20 in some 60s windows.
#
# Why not a third-party lib: the implementation is ~10 lines, and
# the in-memory dict means rate limit state doesn't survive a
# process restart (intentional — counts should reset, not carry over).
_provider_rate_buckets: dict[uuid.UUID, deque[float]] = {}


def _check_provider_rate_limit(provider_uuid: uuid.UUID, limit_per_minute: int) -> tuple[bool, float]:
    """Sliding-window rate limit per provider.

    Returns (allowed, retry_after_seconds). When allowed, the current
    timestamp is appended to the bucket. When not allowed,
    retry_after is how long until the oldest timestamp in the window
    falls out (>= 0).
    """
    now = time.monotonic()
    cutoff = now - 60.0
    bucket = _provider_rate_buckets.get(provider_uuid)
    if bucket is None:
        bucket = deque()
        _provider_rate_buckets[provider_uuid] = bucket
    # Prune timestamps older than 60s
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit_per_minute:
        retry_after = (bucket[0] + 60.0) - now
        return False, max(0.0, retry_after)
    bucket.append(now)
    return True, 0.0


async def _run_probe(
    provider_uuid: uuid.UUID,
    model_uuid: uuid.UUID | None,
    target: ProbeTarget,
    session_maker=None,
) -> None:
    """Single probe run: fetch provider/model from DB, call prober, persist result."""
    sm = session_maker or get_session_maker()
    try:
        async with sm() as session:
            # Look up provider by UUID
            result = await session.execute(
                select(Provider).where(uuid_equals(Provider.uuid_id, provider_uuid))
            )
            provider = result.scalar_one_or_none()
            if provider is None or not provider.enabled:
                return

            # Look up model by UUID if provided
            model: Model | None = None
            if model_uuid:
                model_result = await session.execute(
                    select(Model).where(uuid_equals(Model.uuid_id, model_uuid))
                )
                model = model_result.scalar_one_or_none()

            if target == ProbeTarget.chat_completion:
                if model is None or not model.enabled:
                    return
                model_id_str: str = model.model_id
            else:
                model_id_str = None  # type: ignore[assignment]

            # Per-provider rate limit (sliding window). When the
            # limit is hit, we don't actually call upstream — we
            # record a synthetic `rate_limited` outcome so the
            # dashboard can show what happened. This protects against
            # the bursty "Refresh all" + random sweep + user-triggered
            # probes from overwhelming upstream APIs that already
            # rate-limit aggressively.
            rate_limit = await settings_svc.get_int_setting(
                session,
                settings_svc.PROVIDER_RATE_LIMIT_KEY,
                settings_svc.DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE,
                minimum=1,
                maximum=100,
            )
            allowed, retry_after = _check_provider_rate_limit(provider_uuid, rate_limit)
            if not allowed:
                outcome = ProbeOutcome(
                    success=False,
                    latency_ms=0,
                    error_code=ErrorCode.rate_limit,
                    error_message=(
                        f"local rate limit {rate_limit}/min exceeded; "
                        f"retry after {retry_after:.1f}s"
                    ),
                )
                row = await results_svc.record_outcome(
                    session, provider, model.id if model else None, target, outcome
                )
                sse = get_sse()
                await sse.broadcast(
                    "probe.completed",
                    {
                        "id": row.uuid_id,
                        "provider_id": str(provider_uuid),
                        "model_id": str(model_uuid) if model_uuid else None,
                        "target": target.value,
                        "success": False,
                        "http_status": None,
                        "latency_ms": 0,
                        "ttfb_ms": None,
                        "error_code": ErrorCode.rate_limit.value,
                        "checked_at": row.checked_at.isoformat(),
                    },
                )
                return

            # Acquire semaphores
            root = get_root_sem()
            psem = _get_provider_sem(provider_uuid)
            async with root, psem:
                client = get_client()
                prober = get_prober(provider.kind, client)
                try:
                    if target == ProbeTarget.list_models:
                        outcome = await prober.list_models(provider)
                    else:
                        outcome = await prober.probe_chat(
                            provider,
                            model_id_str,
                            prompt=get_settings().probe_prompt,
                            max_tokens=get_settings().probe_max_tokens,
                            stream=True,
                        )
                except Exception as e:
                    log.exception("probe task crashed: %s", e)
                    await results_svc.record_outcome(
                        session,
                        provider,
                        model.id if model else None,
                        target,
                        _outcome_from_exception(e),
                    )
                else:
                    row = await results_svc.record_outcome(
                        session, provider, model.id if model else None, target, outcome
                    )
                    # On successful list_models, upsert discovered models
                    if target == ProbeTarget.list_models and outcome.success and outcome.models:
                        await upsert_discovered(session, provider.uuid_id, outcome.models)
                    # Broadcast SSE
                    sse = get_sse()
                    await sse.broadcast(
                        "probe.completed",
                        {
                            "id": row.uuid_id,
                            "provider_id": str(provider_uuid),
                            "model_id": str(model_uuid) if model_uuid else None,
                            "target": target.value,
                            "success": row.success,
                            "http_status": row.http_status,
                            "latency_ms": row.latency_ms,
                            "ttfb_ms": row.ttfb_ms,
                            "error_code": row.error_code.value if row.error_code else None,
                            "checked_at": row.checked_at.isoformat(),
                        },
                    )
                    if not row.success:
                        await sse.broadcast(
                            "job.error",
                            {
                                "provider_id": str(provider_uuid),
                                "model_id": str(model_uuid) if model_uuid else None,
                                "message": row.error_message or row.error_code.value
                                if row.error_code
                                else "fail",
                            },
                        )

                # Update JobState
                job_key = _job_key(provider_uuid, model_uuid, target)
                js = await session.get(JobState, job_key)
                if js is None:
                    js = JobState(job_key=job_key)
                    session.add(js)
                js.last_run_at = datetime.now(UTC)
                js.last_status = "ok" if (locals().get("outcome") and outcome.success) else "fail"
                await session.commit()
    except Exception as e:
        log.exception("probe run outer failure: %s", e)


def _outcome_from_exception(exc: BaseException) -> Any:
    from app.probers.error_mapping import map_exception_to_error
    from app.probers.types import ProbeOutcome

    code, msg = map_exception_to_error(exc)
    return ProbeOutcome(success=False, latency_ms=0, error_code=code, error_message=msg)


# ---------- job management --------------------------------------------------


async def sync_jobs_for_provider(provider_uuid: uuid.UUID, session_maker=None) -> None:
    """Reconcile APScheduler jobs for a single provider based on current DB state."""
    sm = session_maker or get_session_maker()
    sched = get_scheduler()
    async with sm() as session:
        # Look up provider by UUID
        result = await session.execute(select(Provider).where(uuid_equals(Provider.uuid_id, provider_uuid)))
        provider = result.scalar_one_or_none()
        if provider is None:
            # remove all jobs for this provider
            for job in sched.get_jobs():
                if job.id.startswith(f"p{provider_uuid}:"):
                    sched.remove_job(job.id)
            return
        enabled = provider.enabled
        list_interval = max(10, provider.interval_seconds)
        favorite_model_interval = await settings_svc.get_int_setting(
            session,
            settings_svc.FAVORITE_MODEL_INTERVAL_KEY,
            settings_svc.DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
        )
        regular_model_interval = await settings_svc.get_int_setting(
            session,
            settings_svc.REGULAR_MODEL_INTERVAL_KEY,
            settings_svc.DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
        )
        # ensure list_models job
        list_id = _make_job_id(provider_uuid, None, ProbeTarget.list_models)
        _upsert_job(sched, list_id, provider_uuid, None, ProbeTarget.list_models, list_interval, enabled)
        # per-model chat_completion jobs
        models = list(
            (
                await session.execute(
                    select(Model).where(
                        Model.provider_id == provider.id, Model.enabled, Model.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for m in models:
            cid = _make_job_id(provider_uuid, m.uuid_id, ProbeTarget.chat_completion)
            model_interval = favorite_model_interval if m.is_favorite else regular_model_interval
            _upsert_job(
                sched, cid, provider_uuid, m.uuid_id, ProbeTarget.chat_completion, model_interval, enabled
            )
        # remove jobs for models that disappeared (deleted or disabled)
        keep = {_make_job_id(provider_uuid, m.uuid_id, ProbeTarget.chat_completion) for m in models}
        keep.add(list_id)
        for job in sched.get_jobs():
            if job.id.startswith(f"p{provider_uuid}:") and job.id not in keep:
                sched.remove_job(job.id)


def _upsert_job(
    sched: AsyncIOScheduler,
    job_id: str,
    provider_uuid: uuid.UUID,
    model_uuid: uuid.UUID | None,
    target: ProbeTarget,
    interval: int,
    enabled: bool,
) -> None:
    if not enabled:
        with contextlib.suppress(Exception):
            sched.remove_job(job_id)
        return
    # First-time add: run once right now, then on the interval. This
    # way the dashboard sees signal for every enabled model within
    # seconds of a fresh import (or app restart) instead of waiting
    # the full interval (potentially 10+ minutes for non-favorites).
    # On a re-upsert (job already exists, e.g. interval changed),
    # pass next_run_time=None to leave the current schedule alone.
    try:
        is_new = sched.get_job(job_id) is None
    except Exception:
        is_new = True
    next_run = datetime.now(UTC) if is_new else None
    # Scale jitter to the interval (10%) so 50 models with the same
    # 120s cadence don't all fire within a 5s window — that creates
    # bursts and makes the load look obviously scripted to upstream
    # APIs. 10% jitter on 120s = ±12s, which is enough to spread
    # them out and short enough that dashboards stay accurate.
    jitter = max(5, interval // 10)
    trigger = IntervalTrigger(seconds=interval, jitter=jitter)
    sched.add_job(
        _run_probe,
        trigger=trigger,
        args=[provider_uuid, model_uuid, target],
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=interval * 2,
        next_run_time=next_run,
    )


async def sync_all_jobs() -> None:
    sm = get_session_maker()
    sched = get_scheduler()
    async with sm() as session:
        # Only sync non-deleted providers
        providers = list(
            (await session.execute(select(Provider).where(Provider.deleted_at.is_(None)))).scalars().all()
        )
    for p in providers:
        await sync_jobs_for_provider(p.uuid_id)
    # remove orphan jobs (no matching provider)
    valid_prefixes = {f"p{p.uuid_id}:" for p in providers}
    for job in sched.get_jobs():
        head = job.id.split(":", 1)[0] + ":"
        if head not in valid_prefixes:
            sched.remove_job(job.id)
    # Always have the background random-sweep job running, even if
    # there are zero providers configured.
    _ensure_random_sweep_job(sched)


async def trigger_now(provider_uuid: uuid.UUID, model_uuid: uuid.UUID | None, session_maker=None) -> bool:
    """Run a probe right now. Returns True if a probe was actually scheduled,
    False if the provider/model is disabled (in which case the caller's API
    endpoint should surface a 409 instead of pretending to schedule)."""
    sm = session_maker or get_session_maker()
    sched = get_scheduler()
    target = ProbeTarget.list_models if model_uuid is None else ProbeTarget.chat_completion

    async with sm() as session:
        # Look up provider by UUID
        result = await session.execute(select(Provider).where(uuid_equals(Provider.uuid_id, provider_uuid)))
        provider = result.scalar_one_or_none()
        if provider is None or not provider.enabled:
            return False
        if target == ProbeTarget.chat_completion:
            if model_uuid:
                result = await session.execute(select(Model).where(uuid_equals(Model.uuid_id, model_uuid)))
                model = result.scalar_one_or_none()
            else:
                model = None
            if model is None or not model.enabled:
                return False

    job_id = _make_job_id(provider_uuid, model_uuid, target)
    try:
        sched.modify_job(job_id, next_run_time=datetime.now(UTC))
        return True
    except Exception:
        # Job doesn't exist yet (e.g. before any sync_all_jobs). Run inline.
        _background_tasks.add(asyncio.create_task(_run_probe(provider_uuid, model_uuid, target)))
        return True


async def trigger_all_models_now(
    session_maker=None,
) -> tuple[int, int]:
    """Schedule a fresh probe for every enabled provider+model.

    Returns (scheduled, skipped):
    - scheduled: number of probes that were actually enqueued
    - skipped: number of (provider, model) pairs that were skipped
      (provider disabled, model disabled, model soft-deleted, etc.)

    Used by the dashboard "Refresh all" button and the page-mount
    auto-refresh, so the user sees fresh availability numbers instead
    of a stale snapshot from whenever the periodic scheduler last ran.

    Concurrency: rely on APScheduler's max_instances=1 + coalesce=True
    on each job to merge rapid duplicate triggers into a single run.
    Calling this 3 times in a row from 3 browser tabs won't queue 3
    probe runs per model — it queues at most 1.
    """
    sm = session_maker or get_session_maker()
    scheduled = 0
    skipped = 0
    async with sm() as session:
        providers = list(
            (await session.execute(
                select(Provider).where(Provider.deleted_at.is_(None), Provider.enabled.is_(True))
            )).scalars().all()
        )
        for p in providers:
            # One list_models probe per provider
            if await trigger_now(p.uuid_id, None, session_maker=sm):
                scheduled += 1
            else:
                skipped += 1
            # One chat_completion probe per enabled model
            models = list(
                (await session.execute(
                    select(Model).where(
                        Model.provider_id == p.id,
                        Model.enabled.is_(True),
                        Model.deleted_at.is_(None),
                    )
                )).scalars().all()
            )
            for m in models:
                if await trigger_now(p.uuid_id, m.uuid_id, session_maker=sm):
                    scheduled += 1
                else:
                    skipped += 1
    return scheduled, skipped


# set of in-flight probe tasks created by trigger_now; lets asyncio.discard them.
_background_tasks: set[asyncio.Task[None]] = set()


# ---------- random continuous probes ----------------------------------------


# Cap on probes the random sweep schedules per tick. With a 20s
# interval this means ~9 probes/minute in the background on top of
# the periodic schedule — gentle enough for upstream APIs but enough
# to keep the dashboard fresh without any explicit "Refresh all".
RANDOM_SWEEP_PROBES_PER_TICK = 3
RANDOM_SWEEP_INTERVAL_SECONDS = 20
# Avoid re-probing a model that was probed very recently (random
# sweep is a supplement, not a replacement for the periodic schedule).
RANDOM_SWEEP_MIN_AGE_SECONDS = 30


async def _random_probe_sweep() -> None:
    """Background tick: pick a few random enabled models that haven't
    been probed in the last few seconds and probe them.

    The periodic schedule already handles every model on its own
    interval. The random sweep is a *supplement* that:
    - Spreads the load continuously instead of in bursts at
      interval multiples.
    - Looks more like human traffic to upstream APIs (which often
      rate-limit or flag perfectly-periodic probes).
    - Keeps the dashboard's "Available Models" counter fresh so
      the user doesn't need to click "Refresh all" on page open.
    """
    import random

    sm = get_session_maker()
    cutoff = datetime.now(UTC).timestamp() - RANDOM_SWEEP_MIN_AGE_SECONDS
    async with sm() as session:
        # Build candidate list: enabled (provider, model) pairs.
        rows = (
            await session.execute(
                select(Provider.uuid_id, Model.uuid_id, Model.status_checked_at)
                .join(Model, Model.provider_id == Provider.id)
                .where(
                    Provider.deleted_at.is_(None),
                    Provider.enabled.is_(True),
                    Model.deleted_at.is_(None),
                    Model.enabled.is_(True),
                )
            )
        ).all()
        if not rows:
            return
        # Filter: skip models probed very recently — the periodic job
        # already covers them, no need to double up.
        candidates: list[tuple[uuid.UUID, uuid.UUID]] = []
        for provider_uuid, model_uuid, last_checked in rows:
            if last_checked is None or last_checked.timestamp() <= cutoff:
                candidates.append((provider_uuid, model_uuid))
        if not candidates:
            return
        sample_size = min(RANDOM_SWEEP_PROBES_PER_TICK, len(candidates))
        chosen = random.sample(candidates, sample_size)
        for provider_uuid, model_uuid in chosen:
            # trigger_now does its own enabled/deleted checks. If a
            # model was disabled between the SELECT and the call, it
            # just returns False and we move on.
            await trigger_now(provider_uuid, model_uuid, session_maker=sm)


def _ensure_random_sweep_job(sched: AsyncIOScheduler) -> None:
    """Add the random sweep job to the scheduler if it's not already
    there. Called from sync_all_jobs so settings changes and provider
    re-syncs don't lose it."""
    job_id = "background:random_probe_sweep"
    if sched.get_job(job_id) is not None:
        return
    sched.add_job(
        _random_probe_sweep,
        trigger=IntervalTrigger(seconds=RANDOM_SWEEP_INTERVAL_SECONDS, jitter=5),
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


# ---------- daily cleanup ---------------------------------------------------


async def daily_cleanup() -> None:
    sm = get_session_maker()
    settings = get_settings()
    async with sm() as session:
        removed = await results_svc.cleanup_old(session, retention_days=settings.retention_days)
        disabled = await disable_stale(session, days=7)
    log.info("cleanup: removed=%d results, disabled=%d models", removed, disabled)
