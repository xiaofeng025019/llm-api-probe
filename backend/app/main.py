"""FastAPI application entrypoint with lifespan and static file mount."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.http import aclose_client
from app.core.scheduler import (
    daily_cleanup,
    get_scheduler,
    sync_all_jobs,
)
from app.db import init_db
from app.db.session import get_session_maker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    sm = get_session_maker()
    await init_db(session_maker=sm)
    # Schedule periodic cleanup job (daily 03:30) + start scheduler + sync jobs
    sched = get_scheduler()
    from apscheduler.triggers.cron import CronTrigger

    settings = get_settings()
    with contextlib.suppress(Exception):
        sched.add_job(
            daily_cleanup,
            CronTrigger(hour=3, minute=30, timezone=settings.tz),
            id="daily_cleanup",
            replace_existing=True,
        )
    if not sched.running:
        sched.start()
    await sync_all_jobs()
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
async def readyz() -> dict[str, str]:
    sched = get_scheduler()
    return {"status": "ok" if sched.running else "starting"}


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
            from starlette.exceptions import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        # If the URL has a file extension (e.g. .js, .css, .png), the browser
        # expects that exact asset. Fallback would return 200 text/html for a
        # missing JS file and the app would silently break with no status-code
        # hint. Only return index.html for extension-less paths.
        if "." in path.rsplit("/", 1)[-1]:
            from starlette.exceptions import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
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
