"""Prober adapter tests with respx-mocked httpx."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.db.models import ErrorCode, Provider, ProviderKind
from app.probers import (
    AnthropicProber,
    GeminiProber,
    OpenAICompatProber,
    OpenAIProber,
    get_prober,
)
from app.probers.error_mapping import map_status_to_error
from app.probers.model_classify import classify_model_id


def make_provider(
    kind: ProviderKind = ProviderKind.openai,
    base_url: str = "https://api.example.com",
    api_key: str = "sk-test",
) -> Provider:
    return Provider(
        id=1,
        name="test",
        kind=kind,
        base_url=base_url,
        api_key=api_key,
        enabled=True,
        interval_seconds=60,
        timeout_seconds=5,
    )


# ---------- classify_model_id ------------------------------------------------


def test_classify_model_id_known() -> None:
    assert classify_model_id("gpt-4o-realtime") == "audio"
    assert classify_model_id("dall-e-3") == "image"
    assert classify_model_id("text-embedding-3-small") == "embedding"
    assert classify_model_id("claude-3-opus") == "vision"
    assert classify_model_id("whisper-1") == "audio"
    assert classify_model_id("gpt-image-1") == "image"


def test_classify_model_id_default() -> None:
    assert classify_model_id("gpt-4o") == "chat"
    assert classify_model_id("unknown-model") == "chat"


# ---------- map_status_to_error ---------------------------------------------


def test_map_status_to_error() -> None:
    assert map_status_to_error(401) == ErrorCode.auth
    assert map_status_to_error(403) == ErrorCode.auth
    assert map_status_to_error(408) == ErrorCode.timeout
    assert map_status_to_error(429) == ErrorCode.rate_limit
    assert map_status_to_error(500) == ErrorCode.server
    assert map_status_to_error(503) == ErrorCode.server
    assert map_status_to_error(400) == ErrorCode.other
    assert map_status_to_error(404) == ErrorCode.other
    assert map_status_to_error(200) is None


# ---------- get_prober factory -----------------------------------------------


def test_get_prober_returns_correct_class() -> None:
    c = httpx.AsyncClient()
    try:
        assert isinstance(get_prober(ProviderKind.openai, c), OpenAIProber)
        assert isinstance(get_prober(ProviderKind.openai_compat, c), OpenAICompatProber)
        assert isinstance(get_prober(ProviderKind.anthropic, c), AnthropicProber)
        assert isinstance(get_prober(ProviderKind.gemini, c), GeminiProber)
    finally:
        # AsyncClient supports async close; just drop reference for sync tests
        c._transport = None  # type: ignore[attr-defined]


# ---------- OpenAI list_models + probe_chat ---------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_openai_list_models_success() -> None:
    p = make_provider(ProviderKind.openai, "https://api.openai.com")
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o", "display_name": "GPT-4o"},
                    {"id": "gpt-4o-realtime"},
                    {"id": "text-embedding-3-small"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as c:
        prober = OpenAIProber(c)
        out = await prober.list_models(p)
    assert out.success
    assert out.http_status == 200
    assert [m.model_id for m in out.models] == [
        "gpt-4o",
        "gpt-4o-realtime",
        "text-embedding-3-small",
    ]
    assert {m.type for m in out.models} == {"chat", "audio", "embedding"}


@pytest.mark.asyncio
@respx.mock
async def test_openai_list_models_401_maps_to_auth() -> None:
    p = make_provider()
    respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).list_models(p)
    assert not out.success
    assert out.http_status == 401
    assert out.error_code == ErrorCode.auth


@pytest.mark.asyncio
@respx.mock
async def test_openai_probe_chat_stream_records_ttfb() -> None:
    p = make_provider()
    body_chunk = b'data: {"choices":[{"delta":{"content":"h"}}]}\n\n'
    done_chunk = b"data: [DONE]\n\n"

    async def stream_handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        import asyncio

        async def gen():
            await asyncio.sleep(0)  # yield control so TTFB can be measured
            yield body_chunk
            await asyncio.sleep(0)
            yield done_chunk

        return httpx.Response(200, stream=gen())

    respx.post("https://api.example.com/v1/chat/completions").mock(side_effect=stream_handler)
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).probe_chat(p, "gpt-4o", prompt="hi", max_tokens=1, stream=True)
    assert out.success
    assert out.http_status == 200
    assert out.ttfb_ms is not None
    assert out.ttfb_ms >= 0
    assert out.latency_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_openai_probe_chat_429_maps_to_rate_limit() -> None:
    p = make_provider()
    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "rate"})
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).probe_chat(p, "gpt-4o", stream=False)
    assert not out.success
    assert out.error_code == ErrorCode.rate_limit


@pytest.mark.asyncio
@respx.mock
async def test_openai_probe_chat_stream_401_captures_error_body() -> None:
    """Stream path: 4xx/5xx must be detected before aiter_bytes drains the body
    and triggers StreamConsumed on the subsequent aread()."""
    p = make_provider()
    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "Incorrect API key"}}
        )
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).probe_chat(p, "gpt-4o", stream=True)
    assert not out.success
    assert out.http_status == 401
    assert out.error_code == ErrorCode.auth
    assert out.error_message is not None
    assert "Incorrect API key" in out.error_message


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_probe_chat_stream_500_captures_error_body() -> None:
    p = make_provider(ProviderKind.anthropic, "https://api.anthropic.com")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(500, text="internal error")
    )
    async with httpx.AsyncClient() as c:
        out = await AnthropicProber(c).probe_chat(p, "claude-3-haiku", stream=True)
    assert not out.success
    assert out.http_status == 500
    assert out.error_code == ErrorCode.server
    assert "internal error" in (out.error_message or "")


@pytest.mark.asyncio
@respx.mock
async def test_gemini_probe_chat_stream_429_captures_error_body() -> None:
    p = make_provider(ProviderKind.gemini, "https://generativelanguage.googleapis.com")
    respx.post(url__regex=r".*googleapis\.com.*").mock(
        return_value=httpx.Response(429, text="quota")
    )
    async with httpx.AsyncClient() as c:
        out = await GeminiProber(c).probe_chat(p, "gemini-1.5-pro", stream=True)
    assert not out.success
    assert out.http_status == 429
    assert out.error_code == ErrorCode.rate_limit


@pytest.mark.asyncio
@respx.mock
async def test_openai_probe_chat_stream_2xx_succeeds() -> None:
    """Sanity check: success path on stream still works after the early-exit fix."""
    p = make_provider()

    async def gen():
        yield b'data: {"choices":[{"delta":{"content":"h"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, stream=gen())
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).probe_chat(p, "gpt-4o", stream=True)
    assert out.success
    assert out.http_status == 200


@pytest.mark.asyncio
@respx.mock
async def test_openai_probe_chat_timeout_maps_to_timeout() -> None:
    p = make_provider()
    respx.post("https://api.example.com/v1/chat/completions").mock(
        side_effect=httpx.ConnectTimeout("slow")
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAIProber(c).probe_chat(p, "gpt-4o", stream=False)
    assert not out.success
    assert out.error_code == ErrorCode.timeout


# ---------- OpenAI-compatible (uses same code path) -------------------------


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_list_models() -> None:
    p = make_provider(ProviderKind.openai_compat, "https://api.deepseek.com")
    respx.get("https://api.deepseek.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
    )
    async with httpx.AsyncClient() as c:
        out = await OpenAICompatProber(c).list_models(p)
    assert out.success
    assert out.models[0].model_id == "deepseek-chat"


# ---------- Anthropic --------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_list_models_empty() -> None:
    p = make_provider(ProviderKind.anthropic, "https://api.anthropic.com")
    async with httpx.AsyncClient() as c:
        out = await AnthropicProber(c).list_models(p)
    assert out.success
    assert out.models == []


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_probe_chat_sends_headers() -> None:
    p = make_provider(ProviderKind.anthropic, "https://api.anthropic.com", "sk-ant-test")
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"text": "ok"}]})
    )
    async with httpx.AsyncClient() as c:
        out = await AnthropicProber(c).probe_chat(p, "claude-3-haiku", stream=False)
    assert out.success
    sent = route.calls.last.request
    assert sent.headers["x-api-key"] == "sk-ant-test"
    assert sent.headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_probe_chat_401_maps_to_auth() -> None:
    p = make_provider(ProviderKind.anthropic, "https://api.anthropic.com")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    async with httpx.AsyncClient() as c:
        out = await AnthropicProber(c).probe_chat(p, "claude-3-haiku", stream=False)
    assert not out.success
    assert out.error_code == ErrorCode.auth


# ---------- Gemini -----------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_gemini_list_models_parses_name() -> None:
    p = make_provider(ProviderKind.gemini, "https://generativelanguage.googleapis.com", "key1")
    respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-1.5-pro", "displayName": "Gemini 1.5 Pro"},
                    {"name": "models/gemini-1.5-flash"},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as c:
        out = await GeminiProber(c).list_models(p)
    assert out.success
    assert [m.model_id for m in out.models] == ["gemini-1.5-pro", "gemini-1.5-flash"]


@pytest.mark.asyncio
@respx.mock
async def test_gemini_probe_chat_500_maps_to_server() -> None:
    p = make_provider(ProviderKind.gemini, "https://generativelanguage.googleapis.com")
    respx.post(url__regex=r"https://generativelanguage\.googleapis\.com/v1beta/models/.*").mock(
        return_value=httpx.Response(500, text="boom")
    )
    async with httpx.AsyncClient() as c:
        out = await GeminiProber(c).probe_chat(p, "gemini-1.5-pro", stream=False)
    assert not out.success
    assert out.error_code == ErrorCode.server
