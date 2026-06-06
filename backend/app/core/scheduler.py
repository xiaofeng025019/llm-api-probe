"""APScheduler bootstrap, job sync, and probe task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import exc as sa_exc
from sqlalchemy import select

from app.core.config import get_settings
from app.core.http import get_client
from app.core.sse import get_sse
from app.db.models import (
    JobState,
    Model,
    ModelType,
    ProbeTarget,
    Provider,
)
from app.db.session import get_session_maker
from app.db.uuid import uuid_equals
from app.probers import get_prober
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


# ---------- adaptive backoff + idle throttling ------------------------------
#
# Two complementary cost-saving mechanisms, both purely in-memory:
#
#   A) Adaptive backoff: a model that succeeds N times in a row gets
#      probed less often (1× → 2× → 4× → 8× of its base interval).
#      A single failure resets the streak — fast detection of new
#      problems matters more than perfectly-smooth backoff.
#
#   B) Idle throttling: when no SSE subscriber is connected (nobody is
#      watching the dashboard), all chat-completion probes use a 5×
#      interval. The moment a subscriber connects, the multiplier
#      snaps to 1 and a one-shot full sweep refreshes everything.
#
# Effective interval = base × backoff_mult × idle_mult.
# State is reset on process restart — safer than persisting a
# "looks-stable-yesterday" assumption across a possibly-long downtime.

BACKOFF_MAX_MULTIPLIER = 8
IDLE_MULTIPLIER = 5

# Per-model success counter. Cleared on probe failure (snap to 1× tier),
# bumped on success, popped when the model is removed from active probing
# (see sync_jobs_for_provider's cleanup at the bottom).
_model_success_streak: dict[uuid.UUID, int] = {}

# When False, the toggle in Settings turns the multiplier off for one
# or both mechanisms — we cache the bool on the module to avoid a DB
# read on every probe. Refreshed in init_sse_hooks() and whenever
# sync_jobs_for_provider is called (so a Settings save propagates
# without restart).
_adaptive_backoff_enabled: bool = True
_idle_throttle_enabled: bool = True


def _backoff_multiplier(streak: int) -> int:
    """Map a consecutive-success count to its interval multiplier.

    Curve (per design — gentle "1×→2×→4×→8×" cap at 8×):
      streak <  3 → 1×   (base interval; recent fresh signal)
      streak <  10 → 2×  (mildly stable)
      streak <  30 → 4×  (very stable)
      streak >= 30 → 8×  (rock-solid; capped here regardless of streak)
    """
    if streak < 3:
        return 1
    if streak < 10:
        return 2
    if streak < 30:
        return 4
    return BACKOFF_MAX_MULTIPLIER


def _idle_multiplier() -> int:
    """1 when at least one SSE subscriber is connected, ``IDLE_MULTIPLIER``
    otherwise. Toggleable via the ``idle_throttle_enabled`` setting."""
    if not _idle_throttle_enabled:
        return 1
    return 1 if get_sse().active_subscribers() > 0 else IDLE_MULTIPLIER


def _effective_interval(model_uuid: uuid.UUID, base_interval: int) -> int:
    """Compute the next-probe gap for a chat-completion model in seconds."""
    backoff = (
        _backoff_multiplier(_model_success_streak.get(model_uuid, 0))
        if _adaptive_backoff_enabled
        else 1
    )
    return base_interval * backoff * _idle_multiplier()


def _bump_streak(model_uuid: uuid.UUID, success: bool) -> None:
    """Update success streak after a probe result. Snap to 0 on failure
    (back to 1× base interval); +1 on success. Logs only on tier crossings
    so probe logs don't get noisy."""
    if success:
        prev = _model_success_streak.get(model_uuid, 0)
        new = prev + 1
        _model_success_streak[model_uuid] = new
        if _backoff_multiplier(prev) != _backoff_multiplier(new):
            log.debug(
                "backoff tier up: model=%s streak=%d→%d mult=%dx",
                model_uuid,
                prev,
                new,
                _backoff_multiplier(new),
            )
    else:
        prev = _model_success_streak.pop(model_uuid, 0)
        if _backoff_multiplier(prev) != 1:
            log.debug(
                "backoff reset on failure: model=%s streak=%d→0 mult→1x",
                model_uuid,
                prev,
            )


async def _model_base_interval(session, model: Model) -> int:
    """Resolve the base (un-multiplied) interval for a model from the
    favorite/regular settings. Mirrors the logic in
    ``sync_jobs_for_provider`` so post-probe reschedules use the same
    base the trigger was originally built with."""
    if model.is_favorite:
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


# ---------- SSE lifecycle hooks (idle wake/sleep) ---------------------------


def _on_sse_wake() -> None:
    """SSE went 0→1: someone opened the dashboard. Schedule a one-shot
    full-sweep probe so the dashboard sees fresh data within seconds
    instead of waiting up to ``IDLE_MULTIPLIER × base_interval``."""
    log.info("sse wake: 1 subscriber, scheduling full probe sweep")

    async def _do_wake() -> None:
        try:
            scheduled, skipped = await trigger_all_models_now()
            log.info(
                "sse wake sweep: scheduled=%d skipped=%d", scheduled, skipped
            )
        except Exception:
            log.exception("sse wake sweep failed")

    # Fire-and-forget — the SSE handler must not block on a probe sweep.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. unit test calling subscribe outside an
        # asyncio context). Skip the sweep — the test code can drive
        # trigger_all_models_now directly if it needs to.
        return
    _background_tasks.add(loop.create_task(_do_wake()))


def _on_sse_sleep() -> None:
    """SSE went 1→0: dashboard closed. Push every queued chat probe out
    by ``(IDLE_MULTIPLIER−1) × current_gap`` so jobs already scheduled
    for the next few seconds don't fire at active cadence right after
    the user walked away.

    List-models jobs are left alone — they're free on most providers
    and keeping the model catalog fresh has no extra cost."""
    log.info("sse idle: 0 subscribers, throttling to %dx interval", IDLE_MULTIPLIER)
    sched = get_scheduler()
    pushed = 0
    now = datetime.now(UTC)
    for job in sched.get_jobs():
        # job ids are "p{provider_uuid}:{target}:m{model_uuid_or_-}"
        if ":chat_completion:" not in job.id:
            continue
        next_run = getattr(job, "next_run_time", None)
        if next_run is None:
            continue
        # Existing gap; multiply remaining gap (not absolute time-since-now)
        # so a job already in the past stays in the past.
        gap_seconds = (next_run - now).total_seconds()
        if gap_seconds <= 0:
            continue
        new_gap = gap_seconds * IDLE_MULTIPLIER
        new_run = now + timedelta(seconds=new_gap)
        with contextlib.suppress(Exception):
            sched.modify_job(job.id, next_run_time=new_run)
            pushed += 1
    if pushed:
        log.info("sse idle: pushed %d chat probes further out", pushed)


def init_sse_hooks() -> None:
    """Wire the SSE 0↔1 transition callbacks into the scheduler.

    Called once from ``main.py:lifespan``. Idempotent — re-calling it
    just replaces the existing handlers with the same functions."""
    get_sse().set_lifecycle_hooks(
        on_first_subscriber=_on_sse_wake,
        on_last_unsubscribe=_on_sse_sleep,
    )


async def _resolve_probe_target(
    session: AsyncSession,
    provider_uuid: uuid.UUID,
    model_uuid: uuid.UUID | None,
    target: ProbeTarget,
) -> tuple[Provider | None, Model | None, str | None]:
    """Look up the provider + model for this probe. Returns the
    resolved objects, plus the `model_id` string to send to the
    prober (or None for list_models). Returns `(None, None, None)`
    when the probe should be skipped (provider disabled / missing,
    model disabled / wrong type, etc.).
    """
    result = await session.execute(
        select(Provider).where(uuid_equals(Provider.uuid_id, provider_uuid))
    )
    provider = result.scalar_one_or_none()
    if provider is None or not provider.enabled:
        return None, None, None

    model: Model | None = None
    if model_uuid:
        model_result = await session.execute(
            select(Model).where(uuid_equals(Model.uuid_id, model_uuid))
        )
        model = model_result.scalar_one_or_none()

    if target == ProbeTarget.chat_completion:
        if model is None or not model.enabled:
            return None, None, None
        # Only chat and vision models support the chat completions
        # endpoint; image/audio/embedding models should not be
        # probed this way (they return 400 and create noise).
        if model.type not in (ModelType.chat, ModelType.vision):
            return None, None, None
        return provider, model, model.model_id
    return provider, model, None  # type: ignore[return-value]


async def _execute_probe(
    session: AsyncSession,
    provider: Provider,
    model: Model | None,
    model_id_str: str | None,
    target: ProbeTarget,
) -> ProbeResult:
    """Call the prober with a hard timeout, record the outcome in the
    DB, and broadcast SSE events. Returns the recorded `ProbeResult`
    row (single source of truth for the post-probe admin steps).
    """
    client = get_client()
    prober = get_prober(provider.kind, client)
    # Watchdog: enforce a hard upper bound on the probe call
    # (provider.timeout_seconds is the configured probe-level
    # timeout, but a misbehaving upstream that returns headers
    # and never a body can keep the streaming iterator alive
    # past it; `+ 5` is slack for connection setup + first byte).
    # Without this, a single hung probe blocks APScheduler's
    # `shutdown(wait=True)` indefinitely.
    probe_timeout_s = max(5, provider.timeout_seconds + 5)
    try:
        if target == ProbeTarget.list_models:
            outcome = await asyncio.wait_for(
                prober.list_models(provider),
                timeout=probe_timeout_s,
            )
        else:
            outcome = await asyncio.wait_for(
                prober.probe_chat(
                    provider,
                    model_id_str,
                    prompt=get_settings().probe_prompt,
                    max_tokens=get_settings().probe_max_tokens,
                    stream=True,
                ),
                timeout=probe_timeout_s,
            )
    except (asyncio.TimeoutError, Exception) as e:
        log.exception("probe task crashed: %s", e)
        return await results_svc.record_outcome(
            session,
            provider,
            model.id if model else None,
            target,
            _outcome_from_exception(e),
        )
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
            "provider_id": str(provider.uuid_id),
            "model_id": str(model.uuid_id) if model else None,
            "target": target.value,
            "success": row.success,
            "http_status": row.http_status,
            "latency_ms": row.latency_ms,
            "ttfb_ms": row.ttfb_ms,
            "error_code": row.error_code.value if row.error_code else None,
            "checked_at": row.checked_at.isoformat(),
        },
    )
    if not row.success and model is not None and model.is_favorite:
        await sse.broadcast(
            "job.error",
            {
                "provider_id": str(provider.uuid_id),
                "provider_name": provider.name,
                "model_id": str(model.uuid_id) if model else None,
                "model_name": model.model_id if model else None,
                "model_is_favorite": True,
                "error_code": row.error_code.value if row.error_code else None,
                "message": row.error_message or row.error_code.value
                if row.error_code
                else "fail",
            },
        )
    return row


async def _post_probe_admin(
    session: AsyncSession,
    provider: Provider,
    model: Model | None,
    model_uuid: uuid.UUID | None,
    target: ProbeTarget,
    row: ProbeResult,
) -> None:
    """Update JobState, snap the success streak, and reschedule the
    next firing. The static trigger interval is a floor — the
    post-probe `modify_job` is what drives the dynamic multiplier
    curve (adaptive backoff + idle throttling).
    """
    provider_uuid = provider.uuid_id
    job_key = _job_key(provider_uuid, model_uuid, target)
    js = await session.get(JobState, job_key)
    if js is None:
        js = JobState(job_key=job_key)
        session.add(js)
    js.last_run_at = datetime.now(UTC)
    ok = bool(row.success)
    js.last_status = "ok" if ok else "fail"
    await session.commit()

    if target == ProbeTarget.chat_completion and model_uuid is not None and model is not None:
        _bump_streak(model_uuid, ok)
        base_interval = await _model_base_interval(session, model)
        effective = _effective_interval(model_uuid, base_interval)
        # Small jitter (10% of effective, min 5s) so a batch of
        # post-probe reschedules from a "Refresh all" don't
        # land on the same instant in the future.
        import random as _random

        jitter = max(5, effective // 10)
        next_at = datetime.now(UTC) + timedelta(seconds=effective + _random.randint(0, jitter))
        with contextlib.suppress(JobLookupError):
            # modify_job raises JobLookupError if the job was
            # removed between probe start and now (e.g. provider
            # deleted mid-probe). The next sync_jobs cycle
            # will rebuild it, so swallowing is fine. Other
            # exceptions (e.g. SQLite "database is locked") are
            # genuine and should be logged.
            get_scheduler().modify_job(job_key, next_run_time=next_at)


async def _refresh_cached_settings(session: AsyncSession) -> int:
    """Refresh the cached scheduler toggles + the per-provider rate
    limit. Cheap (single keyed reads on a tiny table); we don't gate
    this on dirty-ness because a Settings save should propagate
    within one probe interval.
    """
    global _adaptive_backoff_enabled, _idle_throttle_enabled
    _adaptive_backoff_enabled = await settings_svc.get_bool_setting(
        session,
        settings_svc.ADAPTIVE_BACKOFF_ENABLED_KEY,
        settings_svc.DEFAULT_ADAPTIVE_BACKOFF_ENABLED,
    )
    _idle_throttle_enabled = await settings_svc.get_bool_setting(
        session,
        settings_svc.IDLE_THROTTLE_ENABLED_KEY,
        settings_svc.DEFAULT_IDLE_THROTTLE_ENABLED,
    )
    return await settings_svc.get_int_setting(
        session,
        settings_svc.PROVIDER_RATE_LIMIT_KEY,
        settings_svc.DEFAULT_PROVIDER_RATE_LIMIT_PER_MINUTE,
        minimum=1,
        maximum=100,
    )


async def _run_probe(
    provider_uuid: uuid.UUID,
    model_uuid: uuid.UUID | None,
    target: ProbeTarget,
    session_maker=None,
) -> None:
    """Orchestrator. The four sub-steps are now independent helpers
    (each unit-testable in isolation):

    1. _resolve_probe_target   — DB lookup + skip predicates
    2. _refresh_cached_settings — per-probe cache refresh
    3. _execute_probe          — prober call + recording + SSE
    4. _post_probe_admin       — JobState + streak + reschedule
    """
    sm = session_maker or get_session_maker()
    try:
        async with sm() as session:
            # 1. Resolve the target (provider, model, model_id_str).
            provider, model, model_id_str = await _resolve_probe_target(
                session, provider_uuid, model_uuid, target
            )
            if provider is None:
                return

            # 2. Acquire semaphores + refresh cached settings + check
            #    rate limit. Skipping the probe here is a no-op (the
            #    rate limit is enforced by the scheduler, not the
            #    prober).
            root = get_root_sem()
            psem = _get_provider_sem(provider_uuid)
            async with root, psem:
                rate_limit = await _refresh_cached_settings(session)
                allowed, retry_after = _check_provider_rate_limit(provider_uuid, rate_limit)
                if not allowed:
                    log.info(
                        "provider rate limit reached: provider=%s limit=%d/min retry_after=%.1fs",
                        provider_uuid,
                        rate_limit,
                        retry_after,
                    )
                    return

                # 3. Execute the prober call (with hard timeout), record
                #    the outcome, broadcast SSE events.
                row = await _execute_probe(
                    session, provider, model, model_id_str, target
                )

                # 4. Post-probe admin: JobState, streak, reschedule.
                await _post_probe_admin(
                    session, provider, model, model_uuid, target, row
                )
    except (sa_exc.SQLAlchemyError, asyncio.CancelledError) as e:
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
        # per-model chat_completion jobs — only for model types that
        # actually support the /v1/chat/completions endpoint.
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
        probeable_models = [m for m in models if m.type in (ModelType.chat, ModelType.vision)]
        for m in probeable_models:
            cid = _make_job_id(provider_uuid, m.uuid_id, ProbeTarget.chat_completion)
            model_interval = favorite_model_interval if m.is_favorite else regular_model_interval
            _upsert_job(
                sched, cid, provider_uuid, m.uuid_id, ProbeTarget.chat_completion, model_interval, enabled
            )
        # remove jobs for models that disappeared (deleted, disabled, or
        # changed to a non-probeable type like image/audio)
        keep = {_make_job_id(provider_uuid, m.uuid_id, ProbeTarget.chat_completion) for m in probeable_models}
        keep.add(list_id)
        for job in sched.get_jobs():
            if job.id.startswith(f"p{provider_uuid}:") and job.id not in keep:
                sched.remove_job(job.id)
        # Streak-dict pruning is centralised in sync_all_jobs (which has
        # the union of all providers' active models) — doing it here
        # would falsely drop entries for models that belong to other
        # providers being processed in the same sync pass.


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

    # Full prune of orphaned success-streak entries. After every
    # provider has been synced, anything still in the dict whose uuid
    # isn't a current probeable model uuid is dead weight from a
    # previous provider/model that's gone.
    async with sm() as session:
        rows = (
            await session.execute(
                select(Model.uuid_id).where(
                    Model.deleted_at.is_(None),
                    Model.enabled.is_(True),
                    Model.type.in_((ModelType.chat, ModelType.vision)),
                )
            )
        ).all()
    live_uuids = {r[0] for r in rows}
    for stale in [u for u in list(_model_success_streak.keys()) if u not in live_uuids]:
        _model_success_streak.pop(stale, None)

    # Prune per-provider state (sems, rate-limit buckets) for any
    # provider UUIDs that no longer exist. Without this, the dicts
    # grow unboundedly across provider soft-delete + recreate cycles
    # — the UUIDs change in the soft-delete case but the old semaphores
    # would otherwise stick around forever.
    provider_rows = (
        await session.execute(
            select(Provider.uuid_id).where(Provider.deleted_at.is_(None))
        )
    ).all()
    live_provider_uuids = {r[0] for r in provider_rows}
    for stale in [u for u in list(_provider_sems.keys()) if u not in live_provider_uuids]:
        _provider_sems.pop(stale, None)
    for stale in [u for u in list(_provider_rate_buckets.keys()) if u not in live_provider_uuids]:
        _provider_rate_buckets.pop(stale, None)


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


async def trigger_provider_now(provider_uuid: uuid.UUID, session_maker=None) -> tuple[int, int]:
    """Schedule status probes for one enabled provider's probeable models.

    This is intentionally separate from the model-list refresh path:
    `/sync-models` calls upstream list_models and updates the model catalog,
    while this function checks availability/latency for current models.
    """
    sm = session_maker or get_session_maker()
    scheduled = 0
    skipped = 0
    async with sm() as session:
        result = await session.execute(select(Provider).where(uuid_equals(Provider.uuid_id, provider_uuid)))
        provider = result.scalar_one_or_none()
        if provider is None or not provider.enabled:
            return 0, 1
        models = list(
            (
                await session.execute(
                    select(Model).where(
                        Model.provider_id == provider.id,
                        Model.enabled.is_(True),
                        Model.deleted_at.is_(None),
                        Model.type.in_((ModelType.chat, ModelType.vision)),
                    )
                )
            )
            .scalars()
            .all()
        )
    for model in models:
        if await trigger_now(provider_uuid, model.uuid_id, session_maker=sm):
            scheduled += 1
        else:
            skipped += 1
    return scheduled, skipped


async def trigger_all_models_now(
    session_maker=None,
) -> tuple[int, int]:
    """Schedule status checks for every enabled provider's probeable models.

    Returns (scheduled, skipped):
    - scheduled: number of model status checks that were actually enqueued
    - skipped: number of disabled/unavailable targets skipped
      (provider disabled, model disabled, model soft-deleted, etc.)

    Used by the dashboard "Check all model status" button, so the user
    sees fresh availability numbers instead
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
            provider_scheduled, provider_skipped = await trigger_provider_now(p.uuid_id, session_maker=sm)
            scheduled += provider_scheduled
            skipped += provider_skipped
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

    Backoff-aware: a model currently at high backoff (e.g. 8× because
    it has succeeded 30 times in a row) is skipped here until the
    matching fraction of its effective interval has elapsed —
    otherwise the sweep would undo the cost-saving by re-probing
    stable models every 30s.
    """
    import random

    sm = get_session_maker()
    now_ts = datetime.now(UTC).timestamp()
    async with sm() as session:
        # Need is_favorite to compute the per-model base interval.
        rows = (
            await session.execute(
                select(
                    Provider.uuid_id,
                    Model.uuid_id,
                    Model.status_checked_at,
                    Model.is_favorite,
                )
                .join(Model, Model.provider_id == Provider.id)
                .where(
                    Provider.deleted_at.is_(None),
                    Provider.enabled.is_(True),
                    Model.deleted_at.is_(None),
                    Model.enabled.is_(True),
                    Model.type.in_((ModelType.chat, ModelType.vision)),
                )
            )
        ).all()
        if not rows:
            return
        # Resolve the two base intervals once per tick.
        fav_base = await settings_svc.get_int_setting(
            session,
            settings_svc.FAVORITE_MODEL_INTERVAL_KEY,
            settings_svc.DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
        )
        reg_base = await settings_svc.get_int_setting(
            session,
            settings_svc.REGULAR_MODEL_INTERVAL_KEY,
            settings_svc.DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
        )

    candidates: list[tuple[uuid.UUID, uuid.UUID]] = []
    for provider_uuid, model_uuid, last_checked, is_fav in rows:
        # Compute this model's effective interval and require
        # half of it to have elapsed before the sweep is allowed
        # to touch it. Hard floor at RANDOM_SWEEP_MIN_AGE_SECONDS
        # so a brand-new model (no checked_at) still gets probed
        # quickly on the first sweep.
        base = fav_base if is_fav else reg_base
        eff = _effective_interval(model_uuid, base)
        min_age = max(RANDOM_SWEEP_MIN_AGE_SECONDS, eff // 2)
        age_cutoff = now_ts - min_age
        if last_checked is None or last_checked.timestamp() <= age_cutoff:
            candidates.append((provider_uuid, model_uuid))
    if not candidates:
        return
    # Scale per-tick count by fleet size. The old fixed 3 was
    # ~3x over-probe on single-provider setups (3 random on top of
    # the 1 periodic) and a rounding error at 500 models (3/500).
    # Aim: cover ~5% of the fleet per tick, clamped to [3, 50].
    fleet = len(candidates)
    sample_size = max(3, min(50, fleet // 20, fleet))
    sample_size = min(sample_size, fleet)
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


# ---------- periodic cleanup jobs -----------------------------------------

# How often to run the error-history trim. 60 s is short enough that
# the bounded error list never grows past the 15-min TTL by much, and
# long enough that we don't churn the SQLite WAL on every failed probe.
CLEANUP_ERROR_HISTORY_INTERVAL_SECONDS = 60


async def cleanup_error_history_loop() -> None:
    """Trim the operator-facing error history (failed ProbeResult rows).

    Runs on its own periodic job (see `cleanup_error_history_interval_seconds`)
    rather than from the per-failure hot path in `record_outcome`. The hot
    path is now one INSERT + UPDATE per probe; the cleanup happens in batch
    here so a burst of failures doesn't trigger 2× DELETEs each.
    """
    sm = get_session_maker()
    async with sm() as session:
        removed = await results_svc.cleanup_error_history(session)
    if removed:
        log.info("error history cleanup: removed=%d rows", removed)


async def daily_cleanup() -> None:
    sm = get_session_maker()
    settings = get_settings()
    async with sm() as session:
        removed = await results_svc.cleanup_old(session, retention_days=settings.retention_days)
        disabled = await disable_stale(session, days=7)
    log.info("cleanup: removed=%d results, disabled=%d models", removed, disabled)
