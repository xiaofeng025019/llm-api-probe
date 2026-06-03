"""OpenAI / OpenAI-compatible Prober.

Both `openai` and `openai_compat` use the same HTTP API: GET /v1/models and
POST /v1/chat/completions with `Authorization: Bearer …`.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.db.models import ErrorCode, ModelType, Provider
from app.probers.error_mapping import (
    map_exception_to_error,
    map_status_to_error,
)
from app.probers.model_classify import classify_model_id
from app.probers.types import DiscoveredModel, ProbeOutcome


class OpenAIBaseProber:
    name = "openai"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def classify_model_type(self, model_id: str) -> ModelType:
        return classify_model_id(model_id)

    def _headers(self, provider: Provider) -> dict[str, str]:
        h = {"Authorization": f"Bearer {provider.api_key}"}
        if provider.headers_json:
            try:
                extra = json.loads(provider.headers_json)
                if isinstance(extra, dict):
                    h.update({str(k): str(v) for k, v in extra.items()})
            except json.JSONDecodeError:
                pass
        return h

    async def list_models(self, provider: Provider) -> ProbeOutcome:
        url = provider.base_url.rstrip("/") + "/v1/models"
        t0 = time.perf_counter()
        try:
            resp = await self._client.get(
                url, headers=self._headers(provider), timeout=provider.timeout_seconds
            )
            latency = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            code, msg = map_exception_to_error(e)
            return ProbeOutcome(success=False, latency_ms=0, error_code=code, error_message=msg)

        if resp.status_code != 200:
            return ProbeOutcome(
                success=False,
                http_status=resp.status_code,
                latency_ms=latency,
                error_code=map_status_to_error(resp.status_code),
                error_message=resp.text[:500] or None,
            )

        try:
            data: dict[str, Any] = resp.json()
        except json.JSONDecodeError as e:
            return ProbeOutcome(
                success=False,
                http_status=resp.status_code,
                latency_ms=latency,
                error_code=ErrorCode.other,
                error_message=f"json decode: {e}",
            )

        models: list[DiscoveredModel] = []
        for item in data.get("data", []):
            if not isinstance(item, dict) or "id" not in item:
                continue
            mid = str(item["id"])
            models.append(
                DiscoveredModel(
                    model_id=mid,
                    display_name=item.get("display_name") or item.get("name"),
                    type=self.classify_model_type(mid),
                )
            )
        return ProbeOutcome(
            success=True,
            http_status=resp.status_code,
            latency_ms=latency,
            models=models,
        )

    async def probe_chat(
        self,
        provider: Provider,
        model_id: str,
        prompt: str = "hi",
        max_tokens: int = 1,
        stream: bool = True,
    ) -> ProbeOutcome:
        url = provider.base_url.rstrip("/") + "/v1/chat/completions"
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
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
                ttfb = None
                ok = 200 <= resp.status_code < 300
                return ProbeOutcome(
                    success=ok,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    ttfb_ms=None,
                    error_code=None if ok else map_status_to_error(resp.status_code),
                    error_message=None if ok else resp.text[:500] or None,
                )

            async with self._client.stream(
                "POST",
                url,
                headers={**self._headers(provider), "Content-Type": "application/json"},
                json=body,
                timeout=provider.timeout_seconds,
            ) as resp:
                first_byte_at: float | None = None
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if first_byte_at is None:
                        first_byte_at = time.perf_counter()
                    # OpenAI streams end with b"data: [DONE]\n\n"
                    if b"[DONE]" in chunk:
                        break
                latency = int((time.perf_counter() - t0) * 1000)
                if first_byte_at is not None:
                    ttfb = int((first_byte_at - t0) * 1000)
                ok = 200 <= resp.status_code < 300
                return ProbeOutcome(
                    success=ok,
                    http_status=resp.status_code,
                    latency_ms=latency,
                    ttfb_ms=ttfb,
                    error_code=None if ok else map_status_to_error(resp.status_code),
                    error_message=None if ok else (await resp.aread()).decode(errors="replace"),
                )
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            code, msg = map_exception_to_error(e)
            return ProbeOutcome(
                success=False, latency_ms=latency, ttfb_ms=ttfb, error_code=code, error_message=msg
            )


class OpenAIProber(OpenAIBaseProber):
    name = "openai"


class OpenAICompatProber(OpenAIBaseProber):
    name = "openai_compat"
