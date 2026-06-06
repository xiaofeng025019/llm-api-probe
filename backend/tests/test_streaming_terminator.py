"""Tests for stream_chat terminator detection.

A streaming probe that ends without producing its prober-specific
terminator (e.g. `[DONE]`, `message_stop`, `finishReason`) must NOT
be reported as success — the upstream may have closed mid-stream
(deadlock, premature close, mid-chunk JSON parse failure) and we
should record it as a failure with a clear error_code.
"""

from __future__ import annotations

import httpx
import pytest

from app.probers._streaming import stream_chat
from app.probers.error_mapping import ErrorCode


@pytest.mark.asyncio
async def test_stream_chat_returns_failure_when_terminator_missing() -> None:
    """Stream starts a 200, emits some bytes, then closes cleanly
    without ever including the terminator. The probe must report
    success=False, error_code=other, with a message naming the
    issue so the user can see why their 'green' model is flaky."""

    def handler(request: httpx.Request) -> httpx.Response:
        # 200 + a valid-looking stream chunk followed by close.
        # No [DONE] terminator in the body.
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await stream_chat(
            client=client,
            method="POST",
            url="https://example.com/v1/chat/completions",
            headers={"Authorization": "Bearer x"},
            body={"model": "m", "messages": []},
            timeout=5.0,
            terminator=b"[DONE]",
        )

    assert outcome.success is False
    assert outcome.error_code == ErrorCode.other
    assert outcome.error_message is not None
    assert "terminator" in outcome.error_message.lower()


@pytest.mark.asyncio
async def test_stream_chat_returns_success_when_terminator_present() -> None:
    """Sanity: the new failure path doesn't regress the happy case."""
    body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await stream_chat(
            client=client,
            method="POST",
            url="https://example.com/v1/chat/completions",
            headers={"Authorization": "Bearer x"},
            body={"model": "m", "messages": []},
            timeout=5.0,
            terminator=b"[DONE]",
        )

    assert outcome.success is True
    assert outcome.error_code is None
    assert outcome.error_message is None
