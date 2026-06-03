"""ORM models matching the design spec §4."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ProviderKind(str, enum.Enum):
    openai = "openai"
    openai_compat = "openai_compat"
    anthropic = "anthropic"
    gemini = "gemini"


class ModelType(str, enum.Enum):
    chat = "chat"
    vision = "vision"
    audio = "audio"
    image = "image"
    embedding = "embedding"
    code = "code"
    unknown = "unknown"


class ErrorCode(str, enum.Enum):
    auth = "auth"
    rate_limit = "rate_limit"
    timeout = "timeout"
    server = "server"
    network = "network"
    other = "other"


class ProbeTarget(str, enum.Enum):
    list_models = "list_models"
    chat_completion = "chat_completion"


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    kind: Mapped[ProviderKind] = mapped_column(SAEnum(ProviderKind))
    base_url: Mapped[str] = mapped_column(String(500))
    api_key: Mapped[str] = mapped_column(String(500))
    proxy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    headers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    models: Mapped[list[Model]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (Index("ix_models_provider_model", "provider_id", "model_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    type: Mapped[ModelType] = mapped_column(SAEnum(ModelType), default=ModelType.chat)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_favorite: Mapped[bool] = mapped_column(default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    provider: Mapped[Provider] = relationship(back_populates="models")
    results: Mapped[list[ProbeResult]] = relationship(back_populates="model", cascade="all, delete-orphan")


class ProbeResult(Base):
    __tablename__ = "probe_results"
    __table_args__ = (
        Index(
            "ix_probe_results_provider_model_time",
            "provider_id",
            "model_id",
            "checked_at",
        ),
        Index("ix_probe_results_checked_at", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    target: Mapped[ProbeTarget] = mapped_column(SAEnum(ProbeTarget))
    success: Mapped[bool]
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttfb_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[ErrorCode | None] = mapped_column(SAEnum(ErrorCode), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    model: Mapped[Model | None] = relationship(back_populates="results")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobState(Base):
    __tablename__ = "job_states"

    job_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
