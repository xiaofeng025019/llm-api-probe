"""Unified trigger queue for probe requests.

All four trigger sources (periodic APScheduler job, random sweep,
SSE wake sweep, manual API call) submit TriggerRequests here.
A worker pool drains the queue and runs the actual probes.

The queue provides:

  - **Priority**: MANUAL > SWEEP > PERIODIC. Higher priority items
    are dequeued first.
  - **Dedup against pending**: same (provider, model, target) key
    already pending is dropped unless the new request has higher
    priority (then it "bumps" — the old item is marked superseded
    and a fresh one lands at the higher priority).
  - **Dedup against in-flight**: same key currently being probed →
    PERIODIC/SWEEP are dropped (the in-flight probe satisfies the
    need); MANUAL is queued (user explicitly asked).
  - **Overflow**: total pending >= MAX_PENDING → evict lowest-
    priority items first. MANUAL is never evicted; if MANUAL
    itself can't fit, return DROPPED_FULL so the API layer can
    return 503 + Retry-After.

The implementation uses one `asyncio.Queue` per priority tier
plus a single `asyncio.Event` for cross-tier wakeups. The 3-queue
design is simpler than a heap for this access pattern (we always
pick the highest-priority non-empty tier) and keeps the dedup
map + in-flight set as the only mutable shared state.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from dataclasses import dataclass, field

from app.db.models import ProbeTarget


class TriggerPriority(enum.IntEnum):
    """Higher value = processed first."""

    PERIODIC = 1
    SWEEP = 2
    MANUAL = 3


@dataclass(slots=True)
class TriggerRequest:
    provider_uuid: uuid.UUID
    model_uuid: uuid.UUID | None
    target: ProbeTarget
    priority: TriggerPriority
    source: str
    requested_at: float
    # Set to True when a higher-priority request bumped this one.
    # The worker dequeue path skips superseded items transparently.
    superseded: bool = False

    @property
    def key(self) -> tuple[uuid.UUID, uuid.UUID | None, ProbeTarget]:
        return (self.provider_uuid, self.model_uuid, self.target)


class EnqueueResult(enum.Enum):
    ENQUEUED = "enqueued"
    DEDUPED = "deduped"  # pending or in-flight with priority >= new
    DROPPED_FULL = "dropped_full"  # queue full and this MANUAL couldn't fit


@dataclass
class QueueStats:
    pending_by_priority: dict[str, int] = field(
        default_factory=lambda: {"periodic": 0, "sweep": 0, "manual": 0}
    )
    in_flight_count: int = 0
    drops: dict[str, int] = field(default_factory=dict)


class TriggerQueue:
    """See module docstring for the contract."""

    DEFAULT_MAX_PENDING = 1000

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING):
        self._max = max_pending
        # One queue per priority tier. put_nowait / get_nowait
        # for non-blocking ops, plus a wake event for dequeue.
        self._queues: dict[TriggerPriority, asyncio.Queue[TriggerRequest]] = {
            p: asyncio.Queue() for p in TriggerPriority
        }
        # (provider, model, target) → the live TriggerRequest in
        # one of the queues. Popped on dequeue, also popped when
        # the request is evicted to make room.
        self._dedup: dict[tuple, TriggerRequest] = {}
        # Keys currently being probed. PERIODIC/SWEEP enqueues
        # against an in-flight key are dropped; MANUAL is not.
        self._in_flight: set[tuple] = set()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._drops: dict[str, int] = {}
        self._closed = False

    # ---------- enqueue -----------------------------------------------------

    async def enqueue(self, req: TriggerRequest) -> EnqueueResult:
        async with self._lock:
            if self._closed:
                self._bump_drop("closed")
                return EnqueueResult.DROPPED_FULL

            key = req.key

            # 1) In-flight dedup (PERIODIC/SWEEP drop, MANUAL passes)
            if key in self._in_flight:
                if req.priority == TriggerPriority.MANUAL:
                    pass  # fall through to capacity check
                else:
                    self._bump_drop("in_flight")
                    return EnqueueResult.DEDUPED

            # 2) Pending dedup — only MANUAL can bump; anything
            # else against a pending key is dropped (a second
            # PERIODIC or SWEEP for the same key is wasted work;
            # MANUAL is the one case where the user explicitly
            # asked and we must honor priority).
            existing = self._dedup.get(key)
            if existing is not None and not existing.superseded:
                if req.priority != TriggerPriority.MANUAL:
                    self._bump_drop("dedup")
                    return EnqueueResult.DEDUPED
                # MANUAL + MANUAL = dedup (one is enough)
                if existing.priority >= TriggerPriority.MANUAL:
                    self._bump_drop("dedup")
                    return EnqueueResult.DEDUPED
                # MANUAL bumps PERIODIC/SWEEP
                existing.superseded = True
                self._bump_drop("superseded")

            # 3) Capacity check — try to evict lower-priority first
            if self._total_pending() >= self._max:
                if req.priority == TriggerPriority.MANUAL:
                    # MANUAL can evict PERIODIC/SWEEP to make room
                    if not self._evict_lower_than(TriggerPriority.MANUAL):
                        self._bump_drop("full_manual")
                        return EnqueueResult.DROPPED_FULL
                else:
                    self._bump_drop("full")
                    return EnqueueResult.DEDUPED

            # 4) Land the request
            self._dedup[key] = req
            self._queues[req.priority].put_nowait(req)
            self._wake.set()
            return EnqueueResult.ENQUEUED

    # ---------- dequeue -----------------------------------------------------

    async def dequeue(self) -> TriggerRequest:
        """Pop the highest-priority live item. Blocks if empty.

        Skips superseded items transparently (they're effectively
        canceled and shouldn't be processed).
        """
        while True:
            # Try non-blocking first
            for prio in reversed(TriggerPriority):  # MANUAL → SWEEP → PERIODIC
                q = self._queues[prio]
                while not q.empty():
                    req = q.get_nowait()
                    if req.superseded:
                        # Drop and try the next item in this tier
                        async with self._lock:
                            self._dedup.pop(req.key, None)
                        continue
                    async with self._lock:
                        self._dedup.pop(req.key, None)
                    return req
            # All tiers empty → wait for a wake
            self._wake.clear()
            await self._wake.wait()
            # Loop and try again (in case what woke us was superseded)

    # ---------- in-flight lifecycle ----------------------------------------

    def mark_in_flight(self, req: TriggerRequest) -> None:
        self._in_flight.add(req.key)

    def mark_done(self, req: TriggerRequest) -> None:
        self._in_flight.discard(req.key)

    # ---------- stats & shutdown -------------------------------------------

    def stats(self) -> QueueStats:
        return QueueStats(
            pending_by_priority={
                "periodic": self._queues[TriggerPriority.PERIODIC].qsize(),
                "sweep": self._queues[TriggerPriority.SWEEP].qsize(),
                "manual": self._queues[TriggerPriority.MANUAL].qsize(),
            },
            in_flight_count=len(self._in_flight),
            drops=dict(self._drops),
        )

    def close(self) -> None:
        """Stop accepting new work (used during shutdown)."""
        self._closed = True
        # Wake any blocked dequeue so they can exit
        self._wake.set()

    # ---------- internals --------------------------------------------------

    def _total_pending(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    def _evict_lower_than(self, threshold: TriggerPriority) -> bool:
        """Drop the oldest item in the lowest-priority tier
        that's strictly less than `threshold`. Returns True if
        something was evicted.
        """
        for prio in TriggerPriority:  # PERIODIC first, then SWEEP
            if prio >= threshold:
                break
            q = self._queues[prio]
            if not q.empty():
                req = q.get_nowait()
                self._dedup.pop(req.key, None)
                self._bump_drop("evicted")
                return True
        return False

    def _bump_drop(self, reason: str) -> None:
        self._drops[reason] = self._drops.get(reason, 0) + 1


# ---------- singleton ------------------------------------------------------

_trigger_queue: TriggerQueue | None = None


def get_trigger_queue() -> TriggerQueue:
    """Process-wide singleton. Created lazily on first access so
    tests that don't touch the queue don't pay for it.
    """
    global _trigger_queue
    if _trigger_queue is None:
        _trigger_queue = TriggerQueue()
    return _trigger_queue


def reset_trigger_queue_for_tests() -> None:
    """Drop the singleton so a test can install a fresh one. Only
    used in test fixtures — production code never calls this.
    """
    global _trigger_queue
    _trigger_queue = None
