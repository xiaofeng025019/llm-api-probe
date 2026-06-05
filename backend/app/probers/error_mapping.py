"""Map httpx exceptions and HTTP status codes to our ErrorCode enum."""

from __future__ import annotations

import httpx

from app.db.models import ErrorCode


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


def map_exception_to_error(exc: BaseException) -> tuple[ErrorCode, str]:
    if isinstance(exc, httpx.TimeoutException):
        return ErrorCode.timeout, repr(exc)
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
        return ErrorCode.network, repr(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        code = map_status_to_error(exc.response.status_code) or ErrorCode.other
        return code, repr(exc)
    if isinstance(exc, httpx.HTTPError):
        return ErrorCode.network, repr(exc)
    return ErrorCode.other, repr(exc)
