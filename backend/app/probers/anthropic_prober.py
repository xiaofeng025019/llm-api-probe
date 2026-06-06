"""Anthropic Prober. POST {base_url}/v1/messages with x-api-key."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.db.models import ModelType, Provider
from app.probers._streaming import stream_chat
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
        # Anthropic SSE events end with b"message_stop" or the JSON
        # form b'"type":"message_stop"'. Pass the byte sequence and let
        # the shared stream_chat helper walk the stream.
        return await stream_chat(
            client=self._client,
            method="POST",
            url=url,
            headers={**self._headers(provider), "Content-Type": "application/json"},
            body=body,
            timeout=provider.timeout_seconds,
            terminator=b"message_stop",
            non_stream=not stream,
        )
