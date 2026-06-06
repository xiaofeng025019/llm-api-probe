"""Shared Prober types and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.db.models import ErrorCode, ModelType, Provider


# How much of an upstream error body we keep in the in-memory
# `ProbeOutcome.error_message` field. Probers slice the response body
# to this length before returning so a runaway upstream that dumps
# 10MB of HTML doesn't blow up the dashboard render.
MAX_UPSTREAM_ERROR_BODY_CHARS = 500


@dataclass(slots=True)
class DiscoveredModel:
    model_id: str
    display_name: str | None = None
    type: ModelType = ModelType.unknown


@dataclass(slots=True)
class ProbeOutcome:
    success: bool
    http_status: int | None = None
    latency_ms: int = 0
    ttfb_ms: int | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    models: list[DiscoveredModel] = field(default_factory=list)
    # If the upstream told us to back off (HTTP 429 + Retry-After), the
    # parsed seconds go here. The scheduler uses this to set a per-
    # provider cooldown so we stop hammering a rate-limited upstream.
    # `None` for non-429 outcomes and for 429s without a Retry-After.
    retry_after_seconds: int | None = None


class Prober(Protocol):
    name: str

    async def list_models(self, provider: Provider) -> ProbeOutcome: ...

    async def probe_chat(
        self,
        provider: Provider,
        model_id: str,
        prompt: str = "hi",
        max_tokens: int = 1,
        stream: bool = True,
    ) -> ProbeOutcome: ...

    def classify_model_type(self, model_id: str) -> ModelType: ...

