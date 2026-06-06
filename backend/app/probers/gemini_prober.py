"""Google Gemini Prober. GET /v1beta/models?key=… + POST :streamGenerateContent."""

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
    parse_retry_after,
)
from app.probers.model_classify import classify_model_id
from app.probers.types import MAX_UPSTREAM_ERROR_BODY_CHARS, DiscoveredModel, ProbeOutcome


class GeminiProber:
    name = "gemini"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def classify_model_type(self, model_id: str) -> ModelType:
        return classify_model_id(model_id)

    def _headers(self, provider: Provider) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if provider.headers_json:
            try:
                extra = json.loads(provider.headers_json)
                if isinstance(extra, dict):
                    h.update({str(k): str(v) for k, v in extra.items()})
            except json.JSONDecodeError:
                pass
        return h

    async def list_models(self, provider: Provider) -> ProbeOutcome:
        url = provider.base_url.rstrip("/") + f"/v1beta/models?key={provider.api_key}"
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
                error_message=resp.text[:MAX_UPSTREAM_ERROR_BODY_CHARS] or None,
                retry_after_seconds=(
                    parse_retry_after(resp.headers.get("Retry-After")) if resp.status_code == 429 else None
                ),
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            return ProbeOutcome(
                success=False,
                http_status=resp.status_code,
                latency_ms=latency,
                error_code=ErrorCode.other,
                error_message=f"json decode: {e}",
            )

        models: list[DiscoveredModel] = []
        # Gemini returns either {models: [...]} or top-level list
        items = data.get("models") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("id")
            if not name:
                continue
            # Gemini model names look like "models/gemini-1.5-pro"
            mid = str(name).split("/")[-1]
            models.append(
                DiscoveredModel(
                    model_id=mid,
                    display_name=item.get("displayName"),
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
        action = "streamGenerateContent" if stream else "generateContent"
        url = provider.base_url.rstrip("/") + f"/v1beta/models/{model_id}:{action}?key={provider.api_key}"
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        # Gemini SSE streams end with b'"finishReason"'. Pass the byte
        # sequence and let the shared stream_chat helper walk the stream.
        return await stream_chat(
            client=self._client,
            method="POST",
            url=url,
            headers=self._headers(provider),
            body=body,
            timeout=provider.timeout_seconds,
            terminator=b'"finishReason"',
            non_stream=not stream,
        )
