"""stream_chat() must log failures at WARNING, not ERROR, and must not
include a traceback. Probe failure is the steady-state, not an
exceptional condition — every per-model-per-interval failure
otherwise floods the log with full Python tracebacks (10-30 KB per
record → 3.5 MB over 35 hours of running). The DB row and the
returned ProbeOutcome already carry the structured error; the log
line is just a breadcrumb.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_stream_chat_failure_logs_at_warning_not_error() -> None:
    """A failed probe must emit a WARNING log, not ERROR.

    Why not ERROR: every 5-12 minutes the scheduler probes each model
    and any unreachable/failing one logs ERROR. Over a day that is
    hundreds of ERRORs, none actionable — the DB row is the source of
    truth, the dashboard surfaces it, and the log is a breadcrumb.
    """
    from loguru import logger

    from app.probers._streaming import stream_chat

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated upstream timeout")

    captured: list[dict] = []

    def _sink(message):  # type: ignore[no-untyped-def]
        rec = message.record
        captured.append(
            {
                "level": rec["level"].name,
                "exc": rec.get("exception") is not None,
                "text": rec["message"],
            }
        )

    sink_id = logger.add(_sink, level="DEBUG", format="{message}")
    try:
        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            outcome = await stream_chat(
                client=client,
                method="POST",
                url="https://example.com/v1/chat/completions",
                headers={"Authorization": "Bearer x"},
                body={"model": "m", "messages": []},
                timeout=1.0,
                terminator=b"[DONE]",
            )
    finally:
        logger.remove(sink_id)

    assert outcome.success is False
    # We must have at least one log line for the failure
    assert captured, "expected at least one log record for the failed probe"
    # None of the failure breadcrumbs may be ERROR
    failure_logs = [c for c in captured if "probe chat failed" in c["text"]]
    assert failure_logs, f"no probe-failure log captured: {captured}"
    for rec in failure_logs:
        assert rec["level"] == "WARNING", f"probe failure must log at WARNING, got {rec['level']}: {rec}"
        # WARNING with no exception → no traceback in the output.
        # loguru attaches ``exception`` to the record only when called
        # from an except block; we don't want that path here.
        assert rec["exc"] is False, f"probe failure must not include a traceback: {rec}"


@pytest.mark.asyncio
async def test_stream_chat_success_does_not_log_failure_warning() -> None:
    """A successful stream must not produce a 'probe chat failed' log."""
    from loguru import logger

    from app.probers._streaming import stream_chat

    sse_body = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:
        transport = httpx.MockTransport(_handler)
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
    finally:
        logger.remove(sink_id)

    assert outcome.success is True
    assert not any("probe chat failed" in line for line in captured), captured
