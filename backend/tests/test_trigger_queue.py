"""Tests for the unified TriggerQueue.

The queue sits between trigger sources (periodic, sweep, SSE wake,
manual API) and the worker pool that runs probes. The contract:

  - **Priority**: MANUAL > SWEEP > PERIODIC. Higher priority is
    dequeued first.
  - **Dedup (pending)**: same (provider, model, target) already
    pending → drop new request (DEDUPED) UNLESS new priority is
    higher, in which case the existing item is "bumped" (we
    enqueue a new item at the higher priority and mark the old
    one superseded).
  - **Dedup (in-flight)**: same key currently being probed →
    PERIODIC/SWEEP requests are dropped (the in-flight probe
    satisfies the need); MANUAL requests are queued (user
    explicitly asked, will run after current finishes).
  - **Overflow**: total pending >= 1000 → drop PERIODIC first,
    then SWEEP; MANUAL never evicted. If MANUAL can't fit, return
    DROPPED_FULL (caller turns into 503 + Retry-After).
  - **In-order priority**: dequeue always picks the highest
    priority non-empty tier.
  - **Stats**: pending count by priority, in_flight count, drop
    counts by reason.

These tests pin the contract from the outside in. The
implementation is free to choose the internal data structure
(3 queues vs heap vs sorted list) as long as the behavior
matches.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.trigger_queue import (
    EnqueueResult,
    TriggerPriority,
    TriggerQueue,
    TriggerRequest,
)
from app.db.models import ProbeTarget

# ---------- helpers ---------------------------------------------------------


def _req(
    prio: TriggerPriority,
    provider: uuid.UUID | None = None,
    model: uuid.UUID | None = None,
    target: ProbeTarget = ProbeTarget.chat_completion,
    source: str = "test",
) -> TriggerRequest:
    return TriggerRequest(
        provider_uuid=provider or uuid.uuid4(),
        model_uuid=model,
        target=target,
        priority=prio,
        source=source,
        requested_at=0.0,
    )


# ---------- enqueue dedup ---------------------------------------------------


class TestEnqueueDedup:
    @pytest.mark.asyncio
    async def test_first_enqueue_succeeds(self):
        q = TriggerQueue()
        result = await q.enqueue(_req(TriggerPriority.PERIODIC))
        assert result == EnqueueResult.ENQUEUED
        assert q.stats().pending_by_priority == {"periodic": 1, "sweep": 0, "manual": 0}

    @pytest.mark.asyncio
    async def test_duplicate_periodic_is_deduped(self):
        q = TriggerQueue()
        prov = uuid.uuid4()
        model = uuid.uuid4()
        first = await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        second = await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        assert first == EnqueueResult.ENQUEUED
        assert second == EnqueueResult.DEDUPED
        # Only one item in the queue
        assert sum(q.stats().pending_by_priority.values()) == 1

    @pytest.mark.asyncio
    async def test_sweep_deduped_against_pending_periodic(self):
        """A SWEEP request for a key already pending as PERIODIC
        is dropped — running the existing PERIODIC is good enough
        for SWEEP's purpose.
        """
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        result = await q.enqueue(_req(TriggerPriority.SWEEP, provider=prov, model=model))
        assert result == EnqueueResult.DEDUPED

    @pytest.mark.asyncio
    async def test_manual_bumps_pending_periodic(self):
        """A MANUAL request for a key already pending as PERIODIC
        must bump priority so it runs first.
        """
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        result = await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=model))
        assert result == EnqueueResult.ENQUEUED
        # After bump, the MANUAL request should come out first
        first_out = await q.dequeue()
        assert first_out.priority == TriggerPriority.MANUAL
        # The original PERIODIC was superseded
        assert q.stats().drops.get("superseded", 0) >= 1

    @pytest.mark.asyncio
    async def test_different_keys_do_not_dedup(self):
        q = TriggerQueue()
        r1 = await q.enqueue(_req(TriggerPriority.PERIODIC))
        r2 = await q.enqueue(_req(TriggerPriority.PERIODIC))
        assert r1 == EnqueueResult.ENQUEUED
        assert r2 == EnqueueResult.ENQUEUED


# ---------- in-flight handling ---------------------------------------------


class TestInFlightDedup:
    @pytest.mark.asyncio
    async def test_periodic_dropped_when_in_flight(self):
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        req = _req(TriggerPriority.PERIODIC, provider=prov, model=model)
        await q.enqueue(req)
        out = await q.dequeue()
        q.mark_in_flight(out)
        # Now a new PERIODIC for same key — should drop
        result = await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        assert result == EnqueueResult.DEDUPED

    @pytest.mark.asyncio
    async def test_manual_queued_when_in_flight(self):
        """A MANUAL request for a key currently being probed
        must still be enqueued — the user explicitly asked.
        """
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        req = _req(TriggerPriority.PERIODIC, provider=prov, model=model)
        await q.enqueue(req)
        out = await q.dequeue()
        q.mark_in_flight(out)
        result = await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=model))
        assert result == EnqueueResult.ENQUEUED

    @pytest.mark.asyncio
    async def test_mark_done_releases_in_flight(self):
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        req = _req(TriggerPriority.PERIODIC, provider=prov, model=model)
        await q.enqueue(req)
        out = await q.dequeue()
        q.mark_in_flight(out)
        q.mark_done(out)
        # Now a new request for the same key is accepted (no in-flight block)
        result = await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        assert result == EnqueueResult.ENQUEUED


# ---------- priority ordering ----------------------------------------------


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_manual_dequeued_before_periodic(self):
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=model))
        out = await q.dequeue()
        assert out.priority == TriggerPriority.MANUAL

    @pytest.mark.asyncio
    async def test_sweep_dequeued_before_periodic(self):
        q = TriggerQueue()
        # Different keys so both land (same key would dedup per
        # the spec; that's covered in TestEnqueueDedup).
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=uuid.uuid4(), model=uuid.uuid4()))
        await q.enqueue(_req(TriggerPriority.SWEEP, provider=uuid.uuid4(), model=uuid.uuid4()))
        out = await q.dequeue()
        assert out.priority == TriggerPriority.SWEEP

    @pytest.mark.asyncio
    async def test_dequeue_blocks_when_empty(self):
        """A worker that calls dequeue on an empty queue should
        block until something arrives (we don't want workers
        busy-looping)."""
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        task = asyncio.create_task(q.dequeue())
        await asyncio.sleep(0.01)
        assert not task.done()
        # Now enqueue and check task completes
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        out = await asyncio.wait_for(task, timeout=1.0)
        assert out.priority == TriggerPriority.PERIODIC


# ---------- overflow handling ----------------------------------------------


class TestOverflow:
    @pytest.mark.asyncio
    async def test_periodic_evicted_when_full(self):
        q = TriggerQueue(max_pending=2)
        prov = uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=uuid.uuid4()))
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=uuid.uuid4()))
        # Queue is full; a 3rd PERIODIC should be evicted
        result = await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=uuid.uuid4()))
        assert result == EnqueueResult.DEDUPED  # counted as dedup/drop

    @pytest.mark.asyncio
    async def test_manual_returns_dropped_full_when_no_room(self):
        """When the queue is full of MANUALs (nothing lower to
        evict), a new MANUAL gets DROPPED_FULL — the caller turns
        this into 503 + Retry-After.
        """
        q = TriggerQueue(max_pending=2)
        prov = uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=uuid.uuid4()))
        await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=uuid.uuid4()))
        # Third MANUAL for a different key — nothing to evict
        result = await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=uuid.uuid4()))
        assert result == EnqueueResult.DROPPED_FULL

    @pytest.mark.asyncio
    async def test_manual_evicts_periodic_then_lands(self):
        """When the queue is full of PERIODIC, a MANUAL should
        evict one PERIODIC and take its place — MANUAL never
        itself gets evicted, but it can displace lower-priority
        items.
        """
        q = TriggerQueue(max_pending=2)
        prov = uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=uuid.uuid4()))
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=uuid.uuid4()))
        result = await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=uuid.uuid4()))
        assert result == EnqueueResult.ENQUEUED
        # Next dequeue should be the MANUAL
        out = await q.dequeue()
        assert out.priority == TriggerPriority.MANUAL


# ---------- stats ----------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_reflects_state(self):
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        await q.enqueue(_req(TriggerPriority.SWEEP, provider=prov, model=uuid.uuid4()))
        stats = q.stats()
        assert stats.pending_by_priority == {"periodic": 1, "sweep": 1, "manual": 0}
        assert stats.in_flight_count == 0

    @pytest.mark.asyncio
    async def test_in_flight_count_tracked(self):
        q = TriggerQueue()
        req = _req(TriggerPriority.PERIODIC)
        await q.enqueue(req)
        out = await q.dequeue()
        q.mark_in_flight(out)
        assert q.stats().in_flight_count == 1
        q.mark_done(out)
        assert q.stats().in_flight_count == 0


# ---------- superseded items -----------------------------------------------


class TestSuperseded:
    @pytest.mark.asyncio
    async def test_superseded_items_are_skipped(self):
        """When a bump happens, the original item is marked
        superseded. The worker dequeue path must skip it
        transparently and return the next live item.
        """
        q = TriggerQueue()
        prov, model = uuid.uuid4(), uuid.uuid4()
        # Enqueue PERIODIC, then bump to MANUAL
        await q.enqueue(_req(TriggerPriority.PERIODIC, provider=prov, model=model))
        await q.enqueue(_req(TriggerPriority.MANUAL, provider=prov, model=model))
        # First dequeue must be the MANUAL, not the superseded PERIODIC
        first = await q.dequeue()
        assert first.priority == TriggerPriority.MANUAL
        # The superseded item should be visible in the drop counter
        assert q.stats().drops.get("superseded", 0) >= 1
