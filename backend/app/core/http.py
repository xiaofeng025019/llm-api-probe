"""Shared httpx.AsyncClient and proxy-aware factory."""

from __future__ import annotations

import httpx

from app.db.models import Provider

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=False,  # http2 is optional; disable to keep deps minimal
            follow_redirects=True,
        )
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def make_client_for_provider(provider: Provider) -> httpx.AsyncClient:
    """A short-lived client honoring provider.proxy (does not mutate the shared client)."""
    kwargs: dict = {"follow_redirects": True, "timeout": provider.timeout_seconds}
    if provider.proxy:
        kwargs["proxy"] = provider.proxy
    return httpx.AsyncClient(**kwargs)
