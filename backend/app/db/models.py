"""ORM models matching the design spec §4."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
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
    __table_args__ = (
        Index("ix_providers_name_active", "name", unique=True, sqlite_where=text("deleted_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), index=True)
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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    models: Mapped[list[Model]] = relationship(back_populates="provider", cascade="save-update, merge, refresh-expire")


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (Index("ix_models_provider_model", "provider_id", "model_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True, default=uuid.uuid4)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(300))
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    type: Mapped[ModelType] = mapped_column(SAEnum(ModelType), default=ModelType.chat)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_favorite: Mapped[bool] = mapped_column(default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(40), default="unknown")
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    provider: Mapped[Provider] = relationship(back_populates="models", lazy="joined")

    @property
    def provider_uuid(self) -> uuid.UUID:
        """Return the provider's uuid_id for API responses."""
        assert self.provider is not None
        return self.provider.uuid_id

    results: Mapped[list[ProbeResult]] = relationship(back_populates="model", cascade="save-update, merge, refresh-expire")


class ProbeResult(Base):
    __tablename__ = "probe_results"
    __table_args__ = (
        Index(
            "ix_probe_results_provider_model_time",
            "provider_id",
            "model_id",
            "checked_at",
        ),
        Index("ix_probe_results_model_time", "model_id", "checked_at"),
        Index("ix_probe_results_checked_at", "checked_at"),
        # Index the stable UUID snapshots so historical lookups
        # ("find all probes for this provider UUID") stay fast even
        # when the int FK has been reassigned.
        Index("ix_probe_results_provider_uuid_at_probe", "provider_uuid_at_probe"),
        Index("ix_probe_results_model_uuid_at_probe", "model_uuid_at_probe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True, default=uuid.uuid4)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    target: Mapped[ProbeTarget] = mapped_column(SAEnum(ProbeTarget))
    success: Mapped[bool]
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttfb_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[ErrorCode | None] = mapped_column(SAEnum(ErrorCode), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # String snapshots — capture the upstream-visible identity at probe time.
    # Useful for human display, log lines, and matching the model that
    # the upstream provider was offering on the day of the probe.
    provider_name_at_probe: Mapped[str] = mapped_column(String(120))
    model_id_at_probe: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # UUID snapshots — stable identifiers for the *target* provider/model.
    # These survive provider/model re-creation, schema migrations that
    # reassign int PKs, and cross-database restores. Use these for any
    # historical reporting or cross-referencing that must stay valid even
    # if the int FK becomes NULL (model hard-deleted) or points to a
    # different row (after a re-creation).
    provider_uuid_at_probe: Mapped[uuid.UUID] = mapped_column(Uuid)
    model_uuid_at_probe: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    model: Mapped[Model | None] = relationship(back_populates="results")
    provider_rel: Mapped[Provider] = relationship(lazy="joined")

    # When True, this error is exempt from the regular retention
    # cleanup — the user explicitly pinned it from the error history
    # page. Pinned rows are surfaced at the top of the errors list
    # and survive the retention_days setting (which deletes the rest).
    # A successful row having pinned=True is unusual (we only allow
    # pinning failures) but the column is on the table for simplicity.
    pinned: Mapped[bool] = mapped_column(default=False, server_default=text("0"))

    @property
    def provider_uuid(self) -> uuid.UUID:
        """Return the provider's uuid_id for API responses.
        Prefers the live FK, falls back to the snapshot for orphaned rows
        (e.g. provider hard-deleted, FK CASCADEd)."""
        if self.provider_rel is not None:
            return self.provider_rel.uuid_id
        return self.provider_uuid_at_probe

    @property
    def model_uuid(self) -> uuid.UUID | None:
        """Return the model's uuid_id for API responses.
        Prefers the live FK, falls back to the snapshot for SET-NULL
        rows (model hard-deleted)."""
        if self.model is not None:
            return self.model.uuid_id
        return self.model_uuid_at_probe


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
