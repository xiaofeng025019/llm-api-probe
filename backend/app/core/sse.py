"""SSE event broker. In-process pub/sub over asyncio queues."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class SseManager:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

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
