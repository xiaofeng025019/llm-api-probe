"""Shared streaming + TTFB + error-mapping logic for Prober.probe_chat.

The 3 probers (`openai_base`, `anthropic_prober`, `gemini_prober`)
all had ~50 lines of copy-pasted code for the same pattern:

1. POST with stream=True
2. Short-circuit on upstream error (don't drain a stream without
   a terminator — `aread()` on a consumed response raises
   StreamConsumed)
3. Walk the byte stream, look for the prober-specific terminator
4. Measure TTFB (first byte) and total latency
5. Return success

The only prober-specific bits are the URL, body, headers, and the
terminator byte sequence. This helper takes those as arguments and
runs the rest.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.probers.error_mapping import (
    map_exception_to_log_message,
    map_exception_to_user_message,
    map_status_to_error,
    parse_retry_after,
)
from app.probers.types import MAX_UPSTREAM_ERROR_BODY_CHARS, ErrorCode, ProbeOutcome


async def stream_chat(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    terminator: bytes,
    *,
    non_stream: bool = False,
) -> ProbeOutcome:
    """Run a single chat probe (streamed by default) and return the
    outcome with latency, ttfb, status, and (on failure) error
    fields already populated.

    The `terminator` byte sequence is the marker the prober uses to
    know the stream has completed (e.g. `b"[DONE]"` for OpenAI,
    `b'"type":"message_stop"'` for Anthropic, `b'"finishReason"'`
    for Gemini). The stream is consumed until either the terminator
    is found or the stream ends.
    """
    t0 = time.perf_counter()
    ttfb: int | None = None
    try:
        if non_stream:
            return await _non_stream_chat(client, method, url, headers, body, timeout, t0)
        async with client.stream(method, url, headers=headers, json=body, timeout=timeout) as resp:
            # Short-circuit on upstream error: don't drain a stream
            # whose body will never include a terminator. Reading via
            # aread() on a response whose body was already iterated
            # raises StreamConsumed, which would mask the real status
            # with a misleading "other" error_code.
            if not (200 <= resp.status_code < 300):
                err_text = (await resp.aread()).decode(errors="replace")
                return ProbeOutcome(
                    success=False,
                    http_status=resp.status_code,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    error_code=map_status_to_error(resp.status_code),
                    error_message=err_text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None,
                    # 429s usually carry Retry-After; surface it so the
                    # scheduler can set a per-provider cooldown.
                    retry_after_seconds=(
                        parse_retry_after(resp.headers.get("Retry-After"))
                        if resp.status_code == 429
                        else None
                    ),
                )
            first_byte_at: float | None = None
            terminator_seen = False
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                if terminator in chunk:
                    terminator_seen = True
                    break
            latency = int((time.perf_counter() - t0) * 1000)
            if first_byte_at is not None:
                ttfb = int((first_byte_at - t0) * 1000)
            if not terminator_seen:
                # Stream ended cleanly (2xx + aiter_bytes exhausted) but
                # the prober-specific terminator never appeared. Upstream
                # may have closed mid-response, the JSON object may have
                # been split across a chunk boundary, or the model may
                # have stopped before producing the terminator. Report
                # as a failure so the dashboard surfaces a real error
                # instead of a green light on a broken model.
                return ProbeOutcome(
                    success=False,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    ttfb_ms=ttfb,
                    error_code=ErrorCode.other,
                    error_message="stream ended without terminator",
                )
            return ProbeOutcome(
                success=True,
                http_status=resp.status_code,
                latency_ms=latency,
                ttfb_ms=ttfb,
            )
    except Exception as e:
        # A failed probe is steady-state, not exceptional: with
        # multiple models being probed on a short interval, every
        # transient timeout or 5xx would log ERROR with a full
        # traceback — flooding the log with non-actionable noise
        # (10-30 KB per record). The DB row + returned ProbeOutcome
        # already carry the structured error; this is just a
        # breadcrumb at WARNING level, no traceback.
        from loguru import logger

        logger.warning("probe chat failed: {}", map_exception_to_log_message(e))
        code, user_msg = map_exception_to_user_message(e)
        return ProbeOutcome(
            success=False,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error_code=code,
            error_message=user_msg,
        )


async def _non_stream_chat(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    t0: float,
) -> ProbeOutcome:
    """Non-streaming variant — single POST, no TTFB measurement.
    Used by probers that need to support `stream=False` (e.g. for
    model types that don't support streaming).
    """
    resp = await client.request(method, url, headers=headers, json=body, timeout=timeout)
    latency = int((time.perf_counter() - t0) * 1000)
    ok = 200 <= resp.status_code < 300
    return ProbeOutcome(
        success=ok,
        http_status=resp.status_code,
        latency_ms=latency,
        ttfb_ms=None,
        error_code=None if ok else map_status_to_error(resp.status_code),
        error_message=(None if ok else resp.text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None),
    )
