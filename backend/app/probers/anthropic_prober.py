"""Anthropic Prober. POST {base_url}/v1/messages with x-api-key."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.db.models import ModelType, Provider
from app.probers.error_mapping import (
    map_exception_to_error,
    map_status_to_error,
)
from app.probers.model_classify import classify_model_id
from app.probers.types import MAX_UPSTREAM_ERROR_BODY_CHARS, ProbeOutcome

ANTHROPIC_DEFAULT_VERSION = "2023-06-01"


class AnthropicProber:
    name = "anthropic"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def classify_model_type(self, model_id: str) -> ModelType:
        return classify_model_id(model_id)

    def _headers(self, provider: Provider) -> dict[str, str]:
        h = {
            "x-api-key": provider.api_key,
            "anthropic-version": ANTHROPIC_DEFAULT_VERSION,
        }
        if provider.headers_json:
            try:
                extra = json.loads(provider.headers_json)
                if isinstance(extra, dict):
                    h.update({str(k): str(v) for k, v in extra.items()})
            except json.JSONDecodeError:
                pass
        return h

    async def list_models(self, provider: Provider) -> ProbeOutcome:
        # Anthropic has no public list endpoint; we cannot discover models automatically.
        # Return success=True with empty list to avoid marking provider as broken.
        return ProbeOutcome(success=True, models=[])

    async def probe_chat(
        self,
        provider: Provider,
        model_id: str,
        prompt: str = "hi",
        max_tokens: int = 1,
        stream: bool = True,
    ) -> ProbeOutcome:
        url = provider.base_url.rstrip("/") + "/v1/messages"
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        t0 = time.perf_counter()
        ttfb: int | None = None
        try:
            if not stream:
                resp = await self._client.post(
                    url,
                    headers={**self._headers(provider), "Content-Type": "application/json"},
                    json=body,
                    timeout=provider.timeout_seconds,
                )
                latency = int((time.perf_counter() - t0) * 1000)
                ok = 200 <= resp.status_code < 300
                return ProbeOutcome(
                    success=ok,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    error_code=None if ok else map_status_to_error(resp.status_code),
                    error_message=None if ok else resp.text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None,
                )

            async with self._client.stream(
                "POST",
                url,
                headers={**self._headers(provider), "Content-Type": "application/json"},
                json=body,
                timeout=provider.timeout_seconds,
            ) as resp:
                if not (200 <= resp.status_code < 300):
                    err_text = (await resp.aread()).decode(errors="replace")
                    return ProbeOutcome(
                        success=False,
                        http_status=resp.status_code,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        error_code=map_status_to_error(resp.status_code),
                        error_message=err_text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None,
                    )
                first_byte_at: float | None = None
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if first_byte_at is None:
                        first_byte_at = time.perf_counter()
                    if b"message_stop" in chunk or b'"type":"message_stop"' in chunk:
                        break
                latency = int((time.perf_counter() - t0) * 1000)
                if first_byte_at is not None:
                    ttfb = int((first_byte_at - t0) * 1000)
                return ProbeOutcome(
                    success=True,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    ttfb_ms=ttfb,
                )
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            code, msg = map_exception_to_error(e)
            return ProbeOutcome(
                success=False, latency_ms=latency, ttfb_ms=ttfb, error_code=code, error_message=msg
            )
