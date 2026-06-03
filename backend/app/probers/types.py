"""Shared Prober types and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.db.models import ErrorCode, ModelType, Provider


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
