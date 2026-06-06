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
from app.probers._streaming import stream_chat
from app.probers.error_mapping import (
    map_exception_to_error,
    map_status_to_error,
)
from app.probers.model_classify import classify_model_id
from app.probers.types import MAX_UPSTREAM_ERROR_BODY_CHARS, DiscoveredModel, ProbeOutcome


# Some providers claim "OpenAI compatible" but do not implement the
# GET /v1/models endpoint (e.g. MiniMax).  When the upstream returns
# 404 we fall back to a static model list so the dashboard can still
# show models and schedule probes for them.
_STATIC_MODEL_LISTS: dict[str, list[DiscoveredModel]] = {
    "minimaxi.com": [
        DiscoveredModel(model_id="MiniMax-M3", display_name="MiniMax M3", type=ModelType.chat),
        DiscoveredModel(model_id="MiniMax-M2.1", display_name="MiniMax M2.1", type=ModelType.chat),
    ],
}


def _make_url(base_url: str, path: str) -> str:
    """Build an API URL, avoiding double /v1 when base_url already ends with it."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + path
    return base + "/v1" + path


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
        url = _make_url(provider.base_url, "/models")
        t0 = time.perf_counter()
        try:
            resp = await self._client.get(
                url, headers=self._headers(provider), timeout=provider.timeout_seconds
            )
            latency = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            code, msg = map_exception_to_error(e)
            return ProbeOutcome(success=False, latency_ms=0, error_code=code, error_message=msg)

        if resp.status_code == 200:
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

        # Fallback: some OpenAI-compatible providers (MiniMax, etc.) do
        # not expose /v1/models.  Serve a hard-coded list so the user
        # does not have to add models manually.
        for host, models in _STATIC_MODEL_LISTS.items():
            if host in provider.base_url:
                return ProbeOutcome(
                    success=True,
                    http_status=200,
                    latency_ms=latency,
                    models=list(models),
                )

        return ProbeOutcome(
            success=False,
            http_status=resp.status_code,
            latency_ms=latency,
            error_code=map_status_to_error(resp.status_code),
            error_message=resp.text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None,
        )

    async def probe_chat(
        self,
        provider: Provider,
        model_id: str,
        prompt: str = "hi",
        max_tokens: int = 1,
        stream: bool = True,
    ) -> ProbeOutcome:
        url = _make_url(provider.base_url, "/chat/completions")
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": stream,
        }
        # OpenAI streams end with b"data: [DONE]\n\n" — that's the
        # terminator byte sequence the shared stream_chat helper
        # uses to know the stream has completed. The helper handles
        # all the streaming + TTFB + error-mapping plumbing; the
        # prober is now just URL + body + terminator.
        return await stream_chat(
            client=self._client,
            method="POST",
            url=url,
            headers={**self._headers(provider), "Content-Type": "application/json"},
            body=body,
            timeout=provider.timeout_seconds,
            terminator=b"[DONE]",
            non_stream=not stream,
        )


class OpenAIProber(OpenAIBaseProber):
    name = "openai"


class OpenAICompatProber(OpenAIBaseProber):
    name = "openai_compat"
