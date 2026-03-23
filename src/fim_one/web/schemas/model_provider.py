"""Pydantic schemas for model provider / model / group endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provider schemas
# ---------------------------------------------------------------------------


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = None
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class ProviderModelResponse(BaseModel):
    """A model entry as nested inside a provider response."""

    id: str
    name: str
    model_name: str
    temperature: float | None
    max_output_tokens: int | None
    context_size: int | None
    json_mode_enabled: bool
    tool_choice_enabled: bool
    is_active: bool
    created_at: str
    updated_at: str | None


class ProviderResponse(BaseModel):
    id: str
    name: str
    base_url: str | None
    has_api_key: bool
    is_active: bool
    models: list[ProviderModelResponse]
    created_at: str
    updated_at: str | None
    # NEVER expose api_key in responses


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    total: int


# ---------------------------------------------------------------------------
# Provider Model schemas
# ---------------------------------------------------------------------------


class ProviderModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=100)
    temperature: float | None = None
    max_output_tokens: int | None = None
    context_size: int | None = None
    json_mode_enabled: bool = True
    tool_choice_enabled: bool = True


class ProviderModelUpdate(BaseModel):
    name: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    context_size: int | None = None
    json_mode_enabled: bool | None = None
    tool_choice_enabled: bool | None = None
    is_active: bool | None = None


class ProviderModelFullResponse(BaseModel):
    """A model entry with its provider info included."""

    id: str
    provider_id: str
    provider_name: str
    name: str
    model_name: str
    temperature: float | None
    max_output_tokens: int | None
    context_size: int | None
    json_mode_enabled: bool
    tool_choice_enabled: bool
    is_active: bool
    created_at: str
    updated_at: str | None


# ---------------------------------------------------------------------------
# Model Group schemas
# ---------------------------------------------------------------------------


class ModelSlotInfo(BaseModel):
    """Resolved info for a model slot (general/fast/reasoning).

    Field names match the frontend ``ModelSlotInfo`` TypeScript interface.
    """

    id: str
    name: str  # Display name of the model
    model_name: str  # API model identifier
    provider_name: str  # Provider display name
    is_available: bool  # True if both model and its provider are active


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    general_model_id: str | None = None
    fast_model_id: str | None = None
    reasoning_model_id: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    general_model_id: str | None = None
    fast_model_id: str | None = None
    reasoning_model_id: str | None = None


class GroupResponse(BaseModel):
    id: str
    name: str
    description: str | None
    general_model_id: str | None
    fast_model_id: str | None
    reasoning_model_id: str | None
    general_model: ModelSlotInfo | None
    fast_model: ModelSlotInfo | None
    reasoning_model: ModelSlotInfo | None
    is_active: bool
    created_at: str
    updated_at: str | None


class EnvFallbackInfoV2(BaseModel):
    """Extended ENV fallback info including reasoning tier."""

    llm_model: str
    llm_base_url: str
    fast_llm_model: str
    fast_llm_base_url: str
    reasoning_llm_model: str
    reasoning_llm_base_url: str
    has_api_key: bool
    has_fast_api_key: bool
    has_reasoning_api_key: bool


class GroupListResponse(BaseModel):
    groups: list[GroupResponse]
    env_fallback: EnvFallbackInfoV2
    active_group_id: str | None


# ---------------------------------------------------------------------------
# Active Configuration schema
# ---------------------------------------------------------------------------


class EffectiveModelInfo(BaseModel):
    model_name: str | None
    provider_name: str | None
    source: str  # "group" or "env"


class ActiveConfigResponse(BaseModel):
    mode: str  # "env" or "group"
    active_group: dict[str, Any] | None  # { id, name } or null
    effective: dict[str, EffectiveModelInfo]  # keys: general, fast, reasoning
    env_fallback: EnvFallbackInfoV2


# ---------------------------------------------------------------------------
# Model Config Import / Export schemas
# ---------------------------------------------------------------------------


class ModelExportData(BaseModel):
    """A single model entry in the export payload."""

    name: str
    model_name: str
    temperature: float | None = None
    max_output_tokens: int | None = None
    context_size: int | None = None
    json_mode_enabled: bool = True
    tool_choice_enabled: bool = True
    is_active: bool = True


class ProviderExportData(BaseModel):
    """A provider with its models in the export payload."""

    name: str
    base_url: str | None = None
    api_key: None = None  # NEVER export secrets
    is_active: bool = True
    models: list[ModelExportData] = []


class GroupModelRef(BaseModel):
    """Portable reference to a model by provider name + model_name."""

    provider: str
    model_name: str


class GroupExportData(BaseModel):
    """A model group in the export payload."""

    name: str
    description: str | None = None
    general_model: GroupModelRef | None = None
    fast_model: GroupModelRef | None = None
    reasoning_model: GroupModelRef | None = None
    is_active: bool = False


class ModelConfigExportEnvelope(BaseModel):
    """The inner envelope for model config export."""

    exported_at: str
    providers: list[ProviderExportData]
    groups: list[GroupExportData]


class ModelConfigExportResponse(BaseModel):
    """Top-level export wrapper with versioned key."""

    fim_model_config_v1: ModelConfigExportEnvelope


class ModelConfigImportRequest(BaseModel):
    """Request body for model config import."""

    fim_model_config_v1: ModelConfigExportEnvelope
    api_keys: dict[str, str] = Field(
        default_factory=dict,
        description="Optional mapping of provider name -> API key",
    )


class ModelConfigImportSummary(BaseModel):
    """Summary of import results."""

    created: dict[str, int]  # { providers, models, groups }
    skipped: dict[str, int]  # { providers, models, groups }
    warnings: list[str] = []
