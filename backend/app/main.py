"""FastAPI application entrypoint with lifespan and static file mount."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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
    if sched.running:
        sched.shutdown(wait=False)
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


# Static frontend mount (must be last; catch-all)
_settings = get_settings()
_dist = _settings.frontend_dist.resolve()
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
else:

    @app.get("/")
    async def root_placeholder() -> dict[str, str]:
        return {
            "name": "llm-usability",
            "frontend": "not built",
            "docs": "/docs",
        }
