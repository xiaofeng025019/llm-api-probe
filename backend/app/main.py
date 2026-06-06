"""FastAPI application entrypoint with lifespan and static file mount."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# Loguru must be configured before any other app module emits a log
# line, otherwise those records hit the stdlib defaults and never
# reach the rotating file sink. configure_logging() is idempotent so
# it's safe to call again from lifespan if a test spins us up twice.
from app.core.logging import configure_logging

configure_logging()

from app.api.v1 import api_router  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.http import aclose_client  # noqa: E402
from app.core.scheduler import (  # noqa: E402
    cleanup_error_history_loop,
    daily_cleanup,
    get_scheduler,
    init_sse_hooks,
    sync_all_jobs,
    trigger_all_models_now,
)
from app.db import init_db  # noqa: E402
from app.db.session import get_session_maker  # noqa: E402

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    sm = get_session_maker()
    await init_db(session_maker=sm)
    # Schedule periodic cleanup job (daily 03:30) + start scheduler + sync jobs
    sched = get_scheduler()
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    settings = get_settings()
    with contextlib.suppress(Exception):
        sched.add_job(
            daily_cleanup,
            CronTrigger(hour=3, minute=30, timezone=settings.tz),
            id="daily_cleanup",
            replace_existing=True,
        )
    # Error-history cleanup: was previously called from record_outcome's
    # hot path on every failed probe (2× DELETEs each). Now its own
    # periodic job so cleanup cost is amortized.
    from app.core.scheduler import CLEANUP_ERROR_HISTORY_INTERVAL_SECONDS
    with contextlib.suppress(Exception):
        sched.add_job(
            cleanup_error_history_loop,
            IntervalTrigger(seconds=CLEANUP_ERROR_HISTORY_INTERVAL_SECONDS, jitter=10),
            id="cleanup_error_history",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    if not sched.running:
        sched.start()
    await sync_all_jobs()

    # Install the SSE wake/sleep hooks so opening/closing the dashboard
    # triggers a full sweep / drops cadence respectively. See
    # app/core/scheduler.py:_on_sse_wake / _on_sse_sleep for behaviour.
    init_sse_hooks()

    # Catch-up probe on startup: if the process has been down (laptop
    # sleep, redeploy, crash + restart) for longer than a probe interval,
    # the dashboard would show data hours stale until the per-model
    # schedule catches up. Trigger one immediate probe-all so the user
    # sees fresh signal within seconds of the API coming up. Runs in
    # the background so it doesn't block startup; logs but never
    # crashes the lifespan handler.
    import asyncio as _asyncio

    async def _startup_probe_all() -> None:
        try:
            scheduled, skipped = await trigger_all_models_now()
            log.info("startup catch-up probe: scheduled=%d skipped=%d", scheduled, skipped)
        except Exception:
            log.exception("startup catch-up probe failed")

    _asyncio.create_task(_startup_probe_all())

    # If the database already has obvious test-data providers (from prior
    # manual curl-ing or scratch work), surface a one-line warning at boot so
    # the user can clean them up with `uv run python -m app.cli cleanup-test-data`.
    with contextlib.suppress(Exception):
        from sqlalchemy import select

        from app.cli import _looks_like_test_data
        from app.db.models import Provider

        async with sm() as session:
            rows = list((await session.execute(select(Provider))).scalars().all())
        suspects = [p for p in rows if _looks_like_test_data(p.name, p.api_key)]
        if suspects:
            log.warning(
                "found %d likely test-data provider(s); run "
                "`uv run python -m app.cli cleanup-test-data` to inspect/remove",
                len(suspects),
            )
    yield
    # Shutdown order matters:
    # 1) Stop accepting new job firings and wait for in-flight probes to
    #    finish (capped so a slow upstream can't block forever).
    # 2) Then close the shared httpx client.
    # The previous wait=False was racy: a probe mid-stream when the client
    # closed would raise StreamConsumed / ClientClosed and the outer
    # except in _run_probe swallowed it, leaving ProbeResult and JobState
    # uncommitted.
    if sched.running:
        try:
            sched.shutdown(wait=True)
        except TypeError:
            # APScheduler 3.x: shutdown(wait=True) blocks until done.
            sched.shutdown()
    await aclose_client()


app = FastAPI(title="LLM Usability", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"data": None, "error": {"code": "bad_request", "message": str(exc)}},
    )


@app.get("/api/v1/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/readyz")
async def readyz() -> JSONResponse:
    """Readiness probe — returns 200 only when the service is actually
    able to serve traffic. Per k8s convention this should be stricter
    than `/healthz` (which only proves the process is alive).

    Checks:
    1. Scheduler is running (job loop is up)
    2. DB responds to a trivial SELECT 1 (no broken schema/lock)
    3. Startup has completed (sync_all_jobs has run)

    Returns 503 with a structured error payload if any check fails —
    the orchestrator (k8s, supervisor) can decide what to do.
    """
    sched = get_scheduler()
    if not sched.running:
        return JSONResponse(status_code=503, content={"status": "starting", "checks": {"scheduler": "not_running"}})
    try:
        from sqlalchemy import text
        from app.db.session import get_session_maker
        sm = get_session_maker()
        async with sm() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "checks": {"db": f"{type(e).__name__}: {e}"}},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "checks": {"scheduler": "running", "db": "ok"}})


# Static frontend mount (must be last; catch-all) with SPA deep-link fallback.
# Real files in dist are served as-is; any other GET path falls back to index.html
# so React's BrowserRouter can take over. /api/* routes are matched before this
# mount, so they never reach here.
class SPAStaticFiles(StaticFiles):
    def __init__(self, *, index_path: Path, directory: str | Path | None = None, **kwargs):
        super().__init__(directory=directory, **kwargs)
        self.index_path = Path(index_path)

    async def get_response(self, path: str, scope):
        # API routes are registered as APIRouter on the same app; if a request
        # for /api/* reaches this mount it means there is no matching route —
        # surface a real 404 instead of the SPA index.
        if path.startswith("api/") or path.startswith("/api/"):
            raise StarletteHTTPException(status_code=404, detail="not found")
        # Try the real static file first. If it exists, return it.
        # If not, decide between a real 404 (asset) and SPA fallback (route):
        #   - has an extension AND isn't just "index.html" → 404 (asset missing)
        #   - no extension (or "index.html" for "/") → serve SPA shell
        from pathlib import PurePosixPath

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            name = PurePosixPath(path).name
            if "." in name and name != "index.html":
                # Missing asset: 404, don't fall back to HTML
                raise StarletteHTTPException(status_code=404, detail="not found") from None
            return FileResponse(self.index_path, media_type="text/html")


_settings = get_settings()
_dist = _settings.frontend_dist.resolve()
if _dist.exists():
    app.mount(
        "/",
        SPAStaticFiles(directory=str(_dist), html=False, index_path=_dist / "index.html"),
        name="frontend",
    )
else:

    @app.get("/")
    async def root_placeholder() -> dict[str, str]:
        return {
            "name": "llm-usability",
            "frontend": "not built",
            "docs": "/docs",
        }
