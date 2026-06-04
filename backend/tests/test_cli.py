"""Tests for app.cli (test-data pattern matching + cleanup command)."""

from __future__ import annotations

from app.cli import _looks_like_test_data


def test_looks_like_test_data_short_names() -> None:
    for n in ("t", "test", "demo", "smoke", "tmp", "x", "T", "DEMO"):
        assert _looks_like_test_data(n, "sk-real-abcdef"), f"{n!r} should match"


def test_looks_like_test_data_short_keys() -> None:
    for k in ("k", "sk-test", "sk-fake", "demo", "xxx"):
        assert _looks_like_test_data("real-name", k), f"{k!r} should match"


def test_looks_like_test_data_prefixed() -> None:
    assert _looks_like_test_data("test-foo", "real-key")
    assert _looks_like_test_data("tmp-bar", "real-key")
    assert _looks_like_test_data("foo", "sk-test-abc")
    assert _looks_like_test_data("foo", "sk-fake-abc")


def test_looks_like_test_data_rejects_real_looking() -> None:
    assert not _looks_like_test_data("openai-prod", "sk-abcdefghij1234567890")
    assert not _looks_like_test_data("anthropic", "sk-ant-api03-xxxxxxx")
    assert not _looks_like_test_data("my-company-gateway", "real-secret-key")
    assert not _looks_like_test_data("", "")
    assert not _looks_like_test_data("staging", "real-prod-key-12345")
