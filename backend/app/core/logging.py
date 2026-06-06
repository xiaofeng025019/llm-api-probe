"""Loguru-based logging setup.

The spec (§11) promised loguru with stdout + rotating file
(`data/app.log`, 10 MB × 5 keep). For most of dev we ran on stdlib
``logging`` instead — fine, but uvicorn's access spam and our own
`log.exception()` calls had no persistent record beyond whatever the
container runtime kept in stdout. This module finally hooks it up.

Design points:

* A single ``configure_logging()`` call wired from ``main.py`` lifespan.
  Idempotent: calling twice does nothing the second time.

* Existing ``logging.getLogger(__name__).info(...)`` calls everywhere
  in the codebase keep working. We install an
  ``InterceptHandler`` on the root stdlib logger that forwards records
  into loguru — so we don't have to rewrite every callsite.

* Uvicorn ships three named loggers (``uvicorn``, ``uvicorn.error``,
  ``uvicorn.access``). They are configured by uvicorn at startup with
  their own handlers, which would duplicate every log line once we
  also route them through loguru. We clear their handlers and let
  them propagate to the root handler we just installed.

* File rotation matches the spec: 10 MB max per file, keep 5
  generations. Loguru's ``retention=5`` is a count of *rotated*
  files, so total disk = 6 × 10 MB worst case.

* If the data dir is unwritable (read-only filesystem,
  container with no volume mounted), we log a warning to stdout and
  skip the file sink rather than crashing the app.
"""

from __future__ import annotations

import logging
import sys

from loguru import logger

from app.core.config import get_settings

_configured = False


class InterceptHandler(logging.Handler):
    """Bridge: stdlib ``logging`` records → loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        # Find loguru level matching the stdlib level name; fall back
        # to the numeric level if loguru doesn't know it.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk back the stack to find the original caller so loguru's
        # ``{name}:{function}:{line}`` reflects the real source rather
        # than this InterceptHandler. Two layers we want to skip:
        #
        #   1. This file (``app.core.logging``) — frames inside
        #      ``InterceptHandler.emit`` itself.
        #   2. The stdlib ``logging`` package — anything under
        #      ``.../python3.x/logging/``: ``callHandlers``, ``_log``,
        #      ``handle``, etc.
        #
        # The first frame we DON'T want to skip is the user code that
        # called ``logger.info(...)`` or ``log.exception(...)``.
        import os

        logging_dir = os.path.dirname(logging.__file__)
        this_file = os.path.abspath(__file__)
        frame, depth = logging.currentframe(), 2
        while frame is not None:
            fname = os.path.abspath(frame.f_code.co_filename)
            if fname == this_file or fname.startswith(logging_dir):
                frame = frame.f_back
                depth += 1
                continue
            break

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging() -> None:
    """Install loguru as the single logging output.

    Safe to call from app startup; subsequent calls are no-ops.
    Reads ``LOG_LEVEL`` and ``DATA_DIR`` from ``app.core.config``.
    """
    global _configured
    if _configured:
        return
    _configured = True

    settings = get_settings()
    level = settings.log_level.upper()

    # Wipe loguru's default stderr sink, then install our own.
    logger.remove()
    logger.add(
        sys.stdout,
        level=level,
        # Pretty colored output matching uvicorn's defaults.
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "- <level>{message}</level>"
        ),
        # Loguru is async-safe but enqueue=True buys us a thread-local
        # queue so a slow stdout (piped through `less`, for example)
        # doesn't backpressure into the event loop.
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    log_path = settings.data_dir / "app.log"
    try:
        # data_dir is mkdir'd by Settings.model_post_init; opening
        # the file is the real test. Touch+remove would be racier
        # than just letting loguru try and catching the failure.
        logger.add(
            log_path,
            level=level,
            rotation="10 MB",
            retention=5,
            compression=None,  # spec says 10MB×5 rotate, not 5 gz
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} "
                "{level: <8} "
                "{name}:{function}:{line} - {message}"
            ),
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
    except (OSError, PermissionError) as exc:
        # Container with read-only fs or no mounted volume: fall back
        # to stdout-only. One-line warning is plenty; nothing else to
        # do without crashing the whole app.
        logger.warning(
            "could not open log file at {}: {}; continuing with stdout only",
            log_path,
            exc,
        )

    # Route every stdlib logger through us. Setting level=0 on the
    # root logger means "pass everything down to the handler"; the
    # actual filtering happens at the loguru sink level.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Uvicorn installs its own handlers on these three loggers. We
    # clear them so records propagate up to the root, which our
    # InterceptHandler then forwards to loguru. Without this, every
    # request line is logged twice (once by uvicorn's handler in its
    # own format, once by ours via the intercept).
    #
    # alembic + apscheduler do the same in their own bootstrap paths
    # (they call ``logging.basicConfig`` if root has no handler at
    # the moment they import). Also clear them so the rotating file
    # sink captures their lines too, in our format.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "alembic",
        "alembic.runtime",
        "alembic.runtime.migration",
        "apscheduler",
        "apscheduler.scheduler",
        "apscheduler.executors.default",
        "apscheduler.jobstores.default",
        "watchfiles",
        "watchfiles.main",
        "httpx",
        "httpcore",
        "sqlalchemy",
        "sqlalchemy.engine",
    ):
        target = logging.getLogger(name)
        target.handlers = []
        target.propagate = True
