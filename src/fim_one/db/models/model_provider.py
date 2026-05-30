"""Model Provider / Model / Group ORM models.

Three-tier model management: Provider -> Model -> Group.
Providers hold shared credentials, Models define individual LLMs,
Groups assign models to roles (general/fast/reasoning) for one-click switching.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fim_one.core.security.encryption import EncryptedString
from fim_one.db.base import Base, TimestampMixin, UUIDPKMixin


class ModelProvider(UUIDPKMixin, TimestampMixin, Base):
    """A model provider (e.g. DeepSeek, Anthropic, OpenAI).

    Holds shared base_url and api_key for all models under this provider.
    """

    __tablename__ = "model_providers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE", default=True
    )

    # Relationships
    models: Mapped[list[ModelProviderModel]] = relationship(
        "ModelProviderModel",
        back_populates="provider",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ModelProviderModel(UUIDPKMixin, TimestampMixin, Base):
    """An individual model under a provider (e.g. DeepSeek V3, Claude Sonnet).

    References a provider for shared credentials and base_url.
    """

    __tablename__ = "model_provider_models"

    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    json_mode_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE", default=True
    )
    tool_choice_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE", default=True
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, server_default="FALSE", default=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="TRUE", default=True
    )

    # Relationships
    provider: Mapped[ModelProvider] = relationship(
        "ModelProvider", back_populates="models", lazy="selectin"
    )


class ModelGroup(UUIDPKMixin, TimestampMixin, Base):
    """A named group that assigns models to roles (general/fast/reasoning).

    Only one group can be active at a time. When active, its model assignments
    override the ENV-based defaults. When no group is active, ENV defaults apply.
    """

    __tablename__ = "model_groups"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    general_model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model_provider_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    fast_model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model_provider_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    reasoning_model_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("model_provider_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="FALSE", default=False
    )

    # Relationships — use foreign_keys to disambiguate multiple FKs to same table
    general_model: Mapped[ModelProviderModel | None] = relationship(
        "ModelProviderModel",
        foreign_keys=[general_model_id],
        lazy="selectin",
    )
    fast_model: Mapped[ModelProviderModel | None] = relationship(
        "ModelProviderModel",
        foreign_keys=[fast_model_id],
        lazy="selectin",
    )
    reasoning_model: Mapped[ModelProviderModel | None] = relationship(
        "ModelProviderModel",
        foreign_keys=[reasoning_model_id],
        lazy="selectin",
    )
