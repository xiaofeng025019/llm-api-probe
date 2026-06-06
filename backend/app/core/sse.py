"""SSE event broker. In-process pub/sub over asyncio queues."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

log = logging.getLogger(__name__)


class SseManager:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()
        # Lifecycle hooks fired when the subscriber count crosses 0↔1.
        # Used by the scheduler to enter/exit "idle throttling" — see
        # app/core/scheduler.py:init_sse_hooks. Wrapped in try/except at
        # the call site so a buggy handler can't break SSE delivery.
        self._on_first_subscriber: Callable[[], None] | None = None
        self._on_last_unsubscribe: Callable[[], None] | None = None

    def set_lifecycle_hooks(
        self,
        on_first_subscriber: Callable[[], None] | None = None,
        on_last_unsubscribe: Callable[[], None] | None = None,
    ) -> None:
        """Install (or clear) the 0↔1 transition callbacks.
        Pass ``None`` for either to unregister."""
        self._on_first_subscriber = on_first_subscriber
        self._on_last_unsubscribe = on_last_unsubscribe

    def active_subscribers(self) -> int:
        """Number of currently-connected SSE clients.

        Lock-free read — `len()` on a set is atomic in CPython, and a
        caller that wants exact-at-this-instant accuracy is already
        racing the network. Used by the scheduler's `_idle_multiplier`
        to pick active vs idle cadence."""
        return len(self._subscribers)

    async def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        was_empty = False
        async with self._lock:
            was_empty = len(self._subscribers) == 0
            self._subscribers.add(q)
        if was_empty and self._on_first_subscriber is not None:
            try:
                self._on_first_subscriber()
            except Exception:
                log.exception("SSE on_first_subscriber hook raised; ignoring")
        return q

    async def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        became_empty = False
        async with self._lock:
            self._subscribers.discard(q)
            became_empty = len(self._subscribers) == 0
        if became_empty and self._on_last_unsubscribe is not None:
            try:
                self._on_last_unsubscribe()
            except Exception:
                log.exception("SSE on_last_unsubscribe hook raised; ignoring")

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        async with self._lock:
            if not self._subscribers:
                return
            payload = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
            stale: list[asyncio.Queue[str]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    stale.append(q)
            for q in stale:
                self._subscribers.discard(q)

    async def stream(self) -> AsyncIterator[str]:
        q = await self.subscribe()
        try:
            # initial ping
            yield ": ping\n\n"
            while True:
                payload = await q.get()
                yield payload
                if payload.startswith("event: ping"):
                    continue
        finally:
            await self.unsubscribe(q)


_manager: SseManager | None = None


def get_sse() -> SseManager:
    global _manager
    if _manager is None:
        _manager = SseManager()
    return _manager
