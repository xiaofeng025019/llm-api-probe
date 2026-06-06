"""Tests for stream_chat error classification.

A streaming probe whose connection drops mid-stream (the upstream
closes the socket, the response is truncated, etc.) must be
classified as a network failure, not a generic "other" — so the
dashboard's network-vs-other breakdown is accurate and the user
sees the right kind of error in the history page.
"""

from __future__ import annotations

import httpx
import pytest

from app.probers._streaming import stream_chat
from app.probers.error_mapping import ErrorCode


@pytest.mark.asyncio
async def test_stream_chat_midstream_remote_protocol_error_is_network() -> None:
    """Upstream closes the connection mid-stream → must be network, not other."""

    async def handler(request: httpx.Request) -> httpx.Response:
        # httpx.MockTransport raises if the response's stream would
        # raise — simulate a peer reset by raising RemoteProtocolError
        # from inside the stream body.
        raise httpx.RemoteProtocolError("peer closed connection without sending terminator")

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
    assert outcome.error_code == ErrorCode.network


@pytest.mark.asyncio
async def test_stream_chat_read_error_is_network() -> None:
    """httpx.ReadError → network (not other)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("read error")

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
    assert outcome.error_code == ErrorCode.network
