"""Tests for start_workers() concurrency safety.

If the .env is misconfigured with MAX_CONCURRENCY=0, the system
should NOT silently disable the entire monitoring pipeline. A bad
env value should at minimum be clamped to 1 worker so a misconfig
shows up as "slow" rather than "completely dead".

We assert on observable side effects (number of workers actually
created) rather than the log message — the log goes through
loguru's interceptor which doesn't propagate to pytest's caplog.
The implementation MUST also emit a warning, but the user-visible
contract is "the system keeps working with 1 worker".
"""

from __future__ import annotations

import pytest

from app.core import scheduler as sched_mod
from app.core.scheduler import start_workers, stop_workers


@pytest.fixture(autouse=True)
def _reset_workers():
    sched_mod._worker_tasks.clear()
    yield
    # Make sure the test doesn't leak worker tasks into the next test
    sched_mod._worker_tasks.clear()


@pytest.mark.asyncio
async def test_start_workers_clamps_zero_to_one() -> None:
    """MAX_CONCURRENCY=0 should be treated as 1 worker, not 0.

    The exact behavior we want to avoid: 0 workers + periodic
    triggers enqueueing → queue fills to 1000 → user sees no probes
    ever run, and the error is impossible to diagnose from the UI.
    """
    await start_workers(0)
    # Exactly one worker is created when n=0
    assert len(sched_mod._worker_tasks) == 1
    await stop_workers(timeout=0.5)


@pytest.mark.asyncio
async def test_start_workers_clamps_negative_to_one() -> None:
    """Defensive: any n < 1 is treated as 1."""
    await start_workers(-3)
    assert len(sched_mod._worker_tasks) == 1
    await stop_workers(timeout=0.5)


@pytest.mark.asyncio
async def test_start_workers_with_positive_n_unchanged() -> None:
    """Sanity: a normal n=4 still creates exactly 4 workers (no regression)."""
    await start_workers(4)
    assert len(sched_mod._worker_tasks) == 4
    await stop_workers(timeout=0.5)
