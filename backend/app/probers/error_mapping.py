"""Map httpx exceptions and HTTP status codes to our ErrorCode enum.

User-facing messages: the `outcome.error_message` field is surfaced on
the error history page in the UI, so the probers' return value here
must be safe to show a human. Keep the verbose form for the log via
`map_exception_to_log_message` — that's the one for the `repr()`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.db.models import ErrorCode

# Stable, user-facing strings keyed off exception class. The error
# history page renders these verbatim so they must be short and
# safe (no Python class names, no exception args).
_USER_FRIENDLY: dict[type[BaseException], tuple[ErrorCode, str]] = {
    httpx.TimeoutException: (ErrorCode.timeout, "Request timed out"),
    httpx.ConnectError: (ErrorCode.network, "Could not connect to provider"),
    httpx.RemoteProtocolError: (ErrorCode.network, "Provider closed the connection unexpectedly"),
    httpx.ReadError: (ErrorCode.network, "Connection lost while reading response"),
    httpx.WriteError: (ErrorCode.network, "Connection lost while sending request"),
    httpx.PoolTimeout: (ErrorCode.network, "Timed out waiting for a free connection"),
    httpx.LocalProtocolError: (ErrorCode.network, "Protocol error talking to provider"),
    httpx.DecodingError: (ErrorCode.other, "Provider returned an undecodable response"),
    httpx.TooManyRedirects: (ErrorCode.other, "Provider redirected too many times"),
    httpx.UnsupportedProtocol: (ErrorCode.network, "Provider uses an unsupported URL scheme"),
}


def map_status_to_error(http_status: int | None) -> ErrorCode | None:
    if http_status is None:
        return None
    if http_status in (401, 403):
        return ErrorCode.auth
    if http_status == 408:
        return ErrorCode.timeout
    if http_status == 429:
        return ErrorCode.rate_limit
    if 500 <= http_status < 600:
        return ErrorCode.server
    if 400 <= http_status < 500:
        return ErrorCode.other
    return None


def map_exception_to_user_message(exc: BaseException) -> tuple[ErrorCode, str]:
    """Return (ErrorCode, short user-facing string) for an exception.

    Used as `outcome.error_message` which is rendered verbatim in
    the error history page. Must not contain Python class names or
    exception reprs.
    """
    for cls, (code, msg) in _USER_FRIENDLY.items():
        if isinstance(exc, cls):
            return code, msg
    if isinstance(exc, httpx.HTTPStatusError):
        # HTTP status error: rely on the status-based mapping; the
        # upstream's body is captured separately as `error_message` on
        # the failure path of the prober.
        code = map_status_to_error(exc.response.status_code) or ErrorCode.other
        return code, f"Upstream returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return ErrorCode.network, "HTTP error talking to provider"
    return ErrorCode.other, "Probe failed"


def map_exception_to_log_message(exc: BaseException) -> str:
    """Verbose form for loguru — `repr(exc)` preserves the exception
    class, message, and any chained context for debugging.
    """
    return repr(exc)


def map_exception_to_error(exc: BaseException) -> tuple[ErrorCode, str]:
    """Backward-compatible shim. Returns user-facing message.

    Probers that want a separate log message (with full `repr`)
    can call `map_exception_to_log_message(exc)` directly.
    """
    return map_exception_to_user_message(exc)


# Cap how long we'll respect a Retry-After value. Upstreams sometimes
# return absurdly large numbers (some proxies return 86400 on every
# 429); sleeping a day is never what a monitoring loop wants.
MAX_RETRY_AFTER_SECONDS = 300


def parse_retry_after(value: str | None) -> int | None:
    """Parse an HTTP `Retry-After` header into seconds.

    Per RFC 7231 the header can be either a delta-seconds (a
    non-negative integer) or an HTTP-date. Returns None for empty /
    unparseable input. Caps the result at MAX_RETRY_AFTER_SECONDS
    so a misbehaving upstream can't park the scheduler for hours.
    """
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    # delta-seconds form
    if s.isdigit():
        return min(int(s), MAX_RETRY_AFTER_SECONDS)
    # HTTP-date form (e.g. "Wed, 21 Oct 2015 07:28:00 GMT")
    try:
        target = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    # parsedate_to_datetime can return naive or aware; normalize.
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(UTC)).total_seconds()
    if delta <= 0:
        return 0
    return min(int(delta), MAX_RETRY_AFTER_SECONDS)
