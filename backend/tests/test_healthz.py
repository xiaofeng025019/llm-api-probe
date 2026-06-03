"""Smoke test for the /healthz endpoint."""
from __future__ import annotations

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_healthz_returns_ok() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
