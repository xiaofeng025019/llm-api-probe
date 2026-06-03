"""Prober factory by ProviderKind."""

from __future__ import annotations

import httpx

from app.db.models import ProviderKind
from app.probers.anthropic_prober import AnthropicProber
from app.probers.gemini_prober import GeminiProber
from app.probers.openai_base import OpenAICompatProber, OpenAIProber
from app.probers.types import Prober


def get_prober(kind: ProviderKind, client: httpx.AsyncClient) -> Prober:
    if kind in (ProviderKind.openai, ProviderKind.openai_compat):
        cls = OpenAIProber if kind == ProviderKind.openai else OpenAICompatProber
        return cls(client)
    if kind == ProviderKind.anthropic:
        return AnthropicProber(client)
    if kind == ProviderKind.gemini:
        return GeminiProber(client)
    raise ValueError(f"unsupported provider kind: {kind!r}")


__all__ = [
    "AnthropicProber",
    "GeminiProber",
    "OpenAICompatProber",
    "OpenAIProber",
    "Prober",
    "get_prober",
]
