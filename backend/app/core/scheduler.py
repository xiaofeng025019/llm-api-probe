"""APScheduler bootstrap, job sync, and probe task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
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
    JobState,
    Model,
    ProbeTarget,
    Provider,
)
from app.db.session import get_session_maker
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
            result = await session.execute(select(Provider).where(Provider.uuid_id == provider_uuid))
            provider = result.scalar_one_or_none()
            if provider is None or not provider.enabled:
                return

            # Look up model by UUID if provided
            model = None
            if model_uuid:
                result = await session.execute(select(Model).where(Model.uuid_id == model_uuid))
                model = result.scalar_one_or_none()

            if target == ProbeTarget.chat_completion:
                if model is None or not model.enabled:
                    return
                model_id_str: str = model.model_id
            else:
                model_id_str = None  # type: ignore[assignment]

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
        result = await session.execute(select(Provider).where(Provider.uuid_id == provider_uuid))
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
    trigger = IntervalTrigger(seconds=interval, jitter=5)
    sched.add_job(
        _run_probe,
        trigger=trigger,
        args=[provider_uuid, model_uuid, target],
        id=job_id,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=interval * 2,
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


async def trigger_now(provider_uuid: uuid.UUID, model_uuid: uuid.UUID | None, session_maker=None) -> bool:
    """Run a probe right now. Returns True if a probe was actually scheduled,
    False if the provider/model is disabled (in which case the caller's API
    endpoint should surface a 409 instead of pretending to schedule)."""
    sm = session_maker or get_session_maker()
    sched = get_scheduler()
    target = ProbeTarget.list_models if model_uuid is None else ProbeTarget.chat_completion

    async with sm() as session:
        # Look up provider by UUID
        result = await session.execute(select(Provider).where(Provider.uuid_id == provider_uuid))
        provider = result.scalar_one_or_none()
        if provider is None or not provider.enabled:
            return False
        if target == ProbeTarget.chat_completion:
            if model_uuid:
                result = await session.execute(select(Model).where(Model.uuid_id == model_uuid))
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


# set of in-flight probe tasks created by trigger_now; lets asyncio.discard them.
_background_tasks: set[asyncio.Task[None]] = set()


# ---------- daily cleanup ---------------------------------------------------


async def daily_cleanup() -> None:
    sm = get_session_maker()
    settings = get_settings()
    async with sm() as session:
        removed = await results_svc.cleanup_old(session, retention_days=settings.retention_days)
        disabled = await disable_stale(session, days=7)
    log.info("cleanup: removed=%d results, disabled=%d models", removed, disabled)
