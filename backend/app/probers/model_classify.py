"""Heuristic model-id -> ModelType inference."""

from __future__ import annotations

from app.db.models import ModelType

_RULES: tuple[tuple[str, ModelType], ...] = (
    ("-realtime", ModelType.audio),
    ("dall-e", ModelType.image),
    ("-image-", ModelType.image),
    ("-embedding", ModelType.embedding),
    ("-tts", ModelType.audio),
    ("whisper", ModelType.audio),
    ("-vision", ModelType.vision),
    ("claude-3", ModelType.vision),
    ("-code", ModelType.code),
)


def classify_model_id(model_id: str) -> ModelType:
    mid = model_id.lower()
    for needle, t in _RULES:
        if needle in mid:
            return t
    return ModelType.chat
