"""Dependency providers for the FIM One web layer.

All configuration is read from environment variables so that callers only need
to populate the environment (or a ``.env`` file) before importing this module.

Environment variables
---------------------
LLM_API_KEY      : API key for the LLM provider (default: empty string).
LLM_BASE_URL     : Base URL of the OpenAI-compatible endpoint
                    (default: ``https://api.openai.com/v1``).
LLM_MODEL        : Model identifier for the main (smart) model used for
                    planning, analysis, and ReAct (default: ``gpt-4o``).
FAST_LLM_MODEL   : Model identifier for the fast model used for DAG step
                    execution (default: falls back to ``LLM_MODEL``).
LLM_TEMPERATURE  : Default sampling temperature (default: ``0.7``).
LLM_REASONING_EFFORT : Extended thinking level — ``low``, ``medium``, or
                    ``high`` (default: empty = disabled).  For OpenAI/Gemini
                    sends ``reasoning_effort``; for Anthropic (auto-detected)
                    sends ``thinking`` via ``extra_body``.
LLM_REASONING_BUDGET_TOKENS : Explicit token budget for Anthropic thinking
                    (minimum 1024).  Only effective when
                    ``LLM_REASONING_EFFORT`` is set.
FAST_LLM_API_KEY : API key for the fast model provider (default: falls
                    back to ``LLM_API_KEY``).
FAST_LLM_BASE_URL : Base URL for the fast model (default: falls back to
                    ``LLM_BASE_URL``).
FAST_LLM_TEMPERATURE : Temperature for the fast model (default: falls
                    back to ``LLM_TEMPERATURE``).
REASONING_LLM_MODEL : Model identifier for the reasoning model (default:
                    falls back to ``LLM_MODEL``).
REASONING_LLM_API_KEY : API key for the reasoning model provider (default:
                    falls back to ``LLM_API_KEY``).
REASONING_LLM_BASE_URL : Base URL for the reasoning model (default: falls
                    back to ``LLM_BASE_URL``).
REASONING_LLM_TEMPERATURE : Temperature for the reasoning model (default:
                    falls back to ``LLM_TEMPERATURE``).
REASONING_LLM_CONTEXT_SIZE : Context window for the reasoning model
                    (default: falls back to ``LLM_CONTEXT_SIZE``).
REASONING_LLM_MAX_OUTPUT_TOKENS : Max output tokens for the reasoning model
                    (default: falls back to ``LLM_MAX_OUTPUT_TOKENS``).
REASONING_LLM_EFFORT : Reasoning effort for the reasoning model (default:
                    falls back to ``LLM_REASONING_EFFORT``).
REASONING_LLM_BUDGET : Reasoning budget for the reasoning model (default:
                    falls back to ``LLM_REASONING_BUDGET_TOKENS``).
MAX_CONCURRENCY  : Max parallel steps in DAG executor (default: ``5``).
LLM_CONTEXT_SIZE : Effective context cap for the main LLM in tokens
                    (default: ``128000`` — sweet spot for attention quality).
LLM_MAX_OUTPUT_TOKENS : Max output tokens per call for the main LLM
                    (default: ``64000``).
FAST_LLM_CONTEXT_SIZE : Total context window of the fast LLM
                    (default: falls back to ``LLM_CONTEXT_SIZE``).
FAST_LLM_MAX_OUTPUT_TOKENS : Max output tokens for the fast LLM
                    (default: falls back to ``LLM_MAX_OUTPUT_TOKENS``).
MCP_SERVERS      : Optional JSON array of MCP server configs.  Each entry
                    is ``{"name": str, "command": str, "args": [str], "env": {}}``.
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fim_one.core.embedding.openai_compatible import OpenAICompatibleEmbedding
from fim_one.core.model import OpenAICompatibleLLM
from fim_one.core.model.registry import ModelRegistry
from fim_one.core.reranker.base import BaseReranker
from fim_one.core.tool import ToolRegistry
from fim_one.core.tool.builtin import discover_builtin_tools
from fim_one.db import get_session
from fim_one.rag.manager import KnowledgeBaseManager
from fim_one.rag.store.lancedb import LanceDBVectorStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_one.core.mcp import MCPClient

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def _base_url() -> str:
    return os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")


def _main_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o")


def _fast_model() -> str:
    return os.environ.get("FAST_LLM_MODEL", "") or _main_model()


def _fast_api_key() -> str:
    return os.getenv("FAST_LLM_API_KEY", "") or _api_key()


def _fast_base_url() -> str:
    return os.getenv("FAST_LLM_BASE_URL", "") or _base_url()


def _fast_temperature() -> float:
    raw = os.getenv("FAST_LLM_TEMPERATURE", "")
    return float(raw) if raw else _temperature()


def _temperature() -> float:
    return float(os.environ.get("LLM_TEMPERATURE", "0.7"))


def _reasoning_effort() -> str | None:
    """Return the reasoning effort level for extended thinking, or None."""
    val = os.environ.get("LLM_REASONING_EFFORT", "").strip().lower()
    return val if val in ("low", "medium", "high") else None


def _reasoning_budget_tokens() -> int | None:
    """Return the reasoning budget token cap, or None."""
    raw = os.environ.get("LLM_REASONING_BUDGET_TOKENS", "").strip()
    return int(raw) if raw else None


# -- Reasoning-tier helpers (fall back to general config) --


def _reasoning_model() -> str:
    return os.getenv("REASONING_LLM_MODEL", "") or _main_model()


def _reasoning_api_key() -> str:
    return os.getenv("REASONING_LLM_API_KEY", "") or _api_key()


def _reasoning_base_url() -> str:
    return os.getenv("REASONING_LLM_BASE_URL", "") or _base_url()


def _reasoning_temperature() -> float:
    raw = os.getenv("REASONING_LLM_TEMPERATURE", "")
    return float(raw) if raw else _temperature()


def _reasoning_context_size() -> int:
    raw = os.getenv("REASONING_LLM_CONTEXT_SIZE", "")
    return int(raw) if raw else int(os.environ.get("LLM_CONTEXT_SIZE", "128000"))


def _reasoning_max_output() -> int:
    raw = os.getenv("REASONING_LLM_MAX_OUTPUT_TOKENS", "")
    return int(raw) if raw else _main_max_output()


def _reasoning_tier_effort() -> str | None:
    """Return the reasoning-tier effort level, falling back to general."""
    val = os.getenv("REASONING_LLM_EFFORT", "").strip().lower()
    if val in ("low", "medium", "high"):
        return val
    return _reasoning_effort()


def _reasoning_tier_budget() -> int | None:
    """Return the reasoning-tier budget, falling back to general."""
    raw = os.getenv("REASONING_LLM_BUDGET", "").strip()
    if raw:
        return int(raw)
    return _reasoning_budget_tokens()


def _json_mode_enabled() -> bool:
    """Return whether json_mode is enabled (env: LLM_JSON_MODE_ENABLED, default true)."""
    val = os.environ.get("LLM_JSON_MODE_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no")


def _tool_choice_enabled() -> bool:
    """Return whether forced tool_choice is enabled (env: LLM_TOOL_CHOICE_ENABLED, default true)."""
    val = os.environ.get("LLM_TOOL_CHOICE_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no")


def get_max_concurrency() -> int:
    """Return the max parallel steps for the DAG executor."""
    return int(os.environ.get("MAX_CONCURRENCY", "5"))


def get_dag_max_replan_rounds() -> int:
    """Max autonomous replan rounds for DAG mode (env: DAG_MAX_REPLAN_ROUNDS)."""
    return int(os.environ.get("DAG_MAX_REPLAN_ROUNDS", "3"))


def get_dag_replan_stop_confidence() -> float:
    """Confidence threshold to stop DAG re-planning (env: DAG_REPLAN_STOP_CONFIDENCE)."""
    return float(os.environ.get("DAG_REPLAN_STOP_CONFIDENCE", "0.8"))


def get_dag_step_max_iterations() -> int:
    """Max ReAct iterations per DAG step (env: DAG_STEP_MAX_ITERATIONS)."""
    return int(os.environ.get("DAG_STEP_MAX_ITERATIONS", "15"))


def get_dag_tool_cache_enabled() -> bool:
    """Whether to cache identical tool calls within a DAG execution (env: DAG_TOOL_CACHE)."""
    return os.getenv("DAG_TOOL_CACHE", "true").lower() in ("true", "1", "yes")


def get_dag_step_verification() -> bool:
    """Whether to run LLM-based verification of DAG step results.

    Opt-in via ``DAG_STEP_VERIFICATION=true``.  Disabled by default because
    it adds latency and cost (one extra LLM call per step, plus a retry on
    failure).
    """
    return os.environ.get("DAG_STEP_VERIFICATION", "false").lower() in (
        "true", "1", "yes",
    )



def get_react_max_iterations() -> int:
    """Max iterations for ReAct agent mode (env: REACT_MAX_ITERATIONS)."""
    return int(os.environ.get("REACT_MAX_ITERATIONS", "20"))


# Reserve for system prompt + tool descriptions in the context window.
_SYSTEM_PROMPT_RESERVE = int(os.getenv("SYSTEM_PROMPT_RESERVE", "4000"))


def _compute_input_budget(context_size: int, max_output: int) -> int:
    """Compute usable input token budget from model specs.

    Formula: ``context_size - max_output_tokens - system_prompt_reserve``.
    Ensures the budget is at least 4 000 tokens.
    """
    budget = context_size - max_output - _SYSTEM_PROMPT_RESERVE
    return max(budget, 4_000)


def get_context_budget() -> int:
    """Return the input token budget for the main LLM.

    Computed from ``LLM_CONTEXT_SIZE`` and ``LLM_MAX_OUTPUT_TOKENS``.
    """
    context_size = int(os.environ.get("LLM_CONTEXT_SIZE", "128000"))
    return _compute_input_budget(context_size, _main_max_output())


def get_fast_context_budget() -> int:
    """Return the input token budget for the fast LLM.

    Computed from ``FAST_LLM_CONTEXT_SIZE`` and ``FAST_LLM_MAX_OUTPUT_TOKENS``.
    Falls back to the main LLM values when not set.
    """
    context_size = os.environ.get("FAST_LLM_CONTEXT_SIZE", "")
    if context_size:
        return _compute_input_budget(int(context_size), _fast_max_output())
    return get_context_budget()


def _main_max_output() -> int:
    return int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "64000"))


def _fast_max_output() -> int:
    raw = os.environ.get("FAST_LLM_MAX_OUTPUT_TOKENS", "")
    return int(raw) if raw else _main_max_output()


def get_llm() -> OpenAICompatibleLLM:
    """Create the main (smart) LLM for planning, analysis, and ReAct."""
    return OpenAICompatibleLLM(
        api_key=_api_key(),
        base_url=_base_url(),
        model=_main_model(),
        default_temperature=_temperature(),
        default_max_tokens=_main_max_output(),
        reasoning_effort=_reasoning_effort(),
        reasoning_budget_tokens=_reasoning_budget_tokens(),
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=int(os.environ.get("LLM_CONTEXT_SIZE", "128000")),
    )


def get_fast_llm() -> OpenAICompatibleLLM:
    """Create the fast LLM for DAG step execution.

    Falls back to the main model config if ``FAST_LLM_*`` vars are not set.
    Fast models never have reasoning enabled.
    """
    fast_ctx = os.environ.get("FAST_LLM_CONTEXT_SIZE", "")
    ctx_size = int(fast_ctx) if fast_ctx else int(os.environ.get("LLM_CONTEXT_SIZE", "128000"))
    return OpenAICompatibleLLM(
        api_key=_fast_api_key(),
        base_url=_fast_base_url(),
        model=_fast_model(),
        default_temperature=_fast_temperature(),
        default_max_tokens=_fast_max_output(),
        reasoning_effort=None,
        reasoning_budget_tokens=None,
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=ctx_size,
    )


def get_reasoning_llm() -> OpenAICompatibleLLM:
    """Create the reasoning LLM for steps requiring deep analysis.

    Falls back to the main model config if ``REASONING_LLM_*`` vars are not set.
    Reasoning effort and budget ARE enabled for this tier.
    """
    return OpenAICompatibleLLM(
        api_key=_reasoning_api_key(),
        base_url=_reasoning_base_url(),
        model=_reasoning_model(),
        default_temperature=_reasoning_temperature(),
        default_max_tokens=_reasoning_max_output(),
        reasoning_effort=_reasoning_tier_effort(),
        reasoning_budget_tokens=_reasoning_tier_budget(),
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=_reasoning_context_size(),
    )


def get_reasoning_context_budget() -> int:
    """Return the input token budget for the reasoning LLM.

    Computed from ``REASONING_LLM_CONTEXT_SIZE`` and
    ``REASONING_LLM_MAX_OUTPUT_TOKENS``.  Falls back to the main LLM
    values when not set.
    """
    return _compute_input_budget(_reasoning_context_size(), _reasoning_max_output())


def get_model_registry() -> ModelRegistry:
    """Create a :class:`ModelRegistry` with main, fast, and reasoning models.

    This is the ENV-only fallback. For group-aware registry building, use
    :func:`get_model_registry_with_group`.
    """
    registry = ModelRegistry()

    registry.register(
        "main",
        get_llm(),
        roles=["general", "planning", "analysis"],
    )

    # -- Fast model --
    fast = get_fast_llm()
    # Only register as a separate entry if it's actually a different model.
    if _fast_model() != _main_model():
        registry.register("fast", fast, roles=["fast", "execution"])
    else:
        # Same model — register the "fast" role on the main entry.
        registry._roles.setdefault("fast", []).append("main")
        registry._roles.setdefault("execution", []).append("main")

    # -- Reasoning model --
    reasoning = get_reasoning_llm()
    if _reasoning_model() != _main_model():
        registry.register("reasoning", reasoning, roles=["reasoning"])
    else:
        # Same model — register the "reasoning" role on the main entry.
        registry._roles.setdefault("reasoning", []).append("main")

    return registry


# ---------------------------------------------------------------------------
# Model Group-aware registry (hot-switchable)
# ---------------------------------------------------------------------------

# Cached registry + version for hot-switching.
_cached_registry: ModelRegistry | None = None
_cached_registry_version: int = -1


def _build_llm_from_group_model(
    model: object,
) -> OpenAICompatibleLLM | None:
    """Build an OpenAICompatibleLLM from a ModelProviderModel ORM instance.

    Returns None if the model or its provider is inactive or missing required fields.
    """
    if model is None:
        return None
    if not model.is_active:
        return None
    provider = model.provider
    if provider is None or not provider.is_active:
        return None
    if not provider.base_url and not provider.api_key:
        # No connection info at all — cannot build LLM
        return None

    return OpenAICompatibleLLM(
        api_key=provider.api_key or _api_key(),
        base_url=provider.base_url or _base_url(),
        model=model.model_name,
        default_temperature=(
            model.temperature if model.temperature is not None else _temperature()
        ),
        default_max_tokens=model.max_output_tokens or _main_max_output(),
        reasoning_effort=None,  # Set per-role below if needed
        reasoning_budget_tokens=None,
        json_mode_enabled=model.json_mode_enabled,
        tool_choice_enabled=model.tool_choice_enabled,
        context_size=model.context_size,
    )


async def _get_active_group_config(
    db: "AsyncSession",
) -> dict | None:
    """Query the active model group and return resolved model configs.

    Returns a dict with keys 'general', 'fast', 'reasoning', each mapping to
    an OpenAICompatibleLLM instance (or None for that slot if not assigned).
    Returns None if no group is active.
    """
    from sqlalchemy import select
    from fim_one.web.models.model_provider import ModelGroup

    stmt = select(ModelGroup).where(
        ModelGroup.is_active == True  # noqa: E712
    ).limit(1)
    result = await db.execute(stmt)
    group = result.scalar_one_or_none()
    if group is None:
        return None

    return {
        "general": _build_llm_from_group_model(group.general_model),
        "fast": _build_llm_from_group_model(group.fast_model),
        "reasoning": _build_llm_from_group_model(group.reasoning_model),
    }


def _build_registry_from_group(group_config: dict) -> ModelRegistry:
    """Build a ModelRegistry using group models with ENV fallback per slot."""
    registry = ModelRegistry()

    # General / main
    general_llm = group_config.get("general") or get_llm()
    registry.register("main", general_llm, roles=["general", "planning", "analysis"])

    # Fast
    fast_llm = group_config.get("fast") or get_fast_llm()
    # Check if it's actually the same LLM instance as main
    if fast_llm is not general_llm:
        registry.register("fast", fast_llm, roles=["fast", "execution"])
    else:
        registry._roles.setdefault("fast", []).append("main")
        registry._roles.setdefault("execution", []).append("main")

    # Reasoning — inject reasoning effort from ENV if group model doesn't have it
    reasoning_llm = group_config.get("reasoning")
    if reasoning_llm is not None:
        # Apply reasoning effort/budget from ENV to the group's reasoning model
        reasoning_llm._reasoning_effort = _reasoning_tier_effort()
        reasoning_llm._reasoning_budget_tokens = _reasoning_tier_budget()
    else:
        reasoning_llm = get_reasoning_llm()

    if reasoning_llm is not general_llm:
        registry.register("reasoning", reasoning_llm, roles=["reasoning"])
    else:
        registry._roles.setdefault("reasoning", []).append("main")

    return registry


async def get_model_registry_with_group(
    db: "AsyncSession",
) -> ModelRegistry:
    """Return a ModelRegistry that respects the active model group.

    If a model group is active, its model assignments override ENV defaults.
    Uses a version counter for hot-switching: when the active group changes
    (via admin API), the cached registry is invalidated and rebuilt on the
    next call.

    Falls back to the ENV-only registry when no group is active.
    """
    global _cached_registry, _cached_registry_version

    from fim_one.web.api.admin import get_model_group_version

    current_version = get_model_group_version()

    if _cached_registry is not None and _cached_registry_version == current_version:
        return _cached_registry

    # Rebuild
    group_config = await _get_active_group_config(db)
    if group_config is not None:
        registry = _build_registry_from_group(group_config)
    else:
        registry = get_model_registry()

    _cached_registry = registry
    _cached_registry_version = current_version
    return registry


def get_tools(
    *,
    sandbox_root: Path | None = None,
    sandbox_config: dict | None = None,
    uploads_root: Path | None = None,
) -> ToolRegistry:
    """Create a :class:`ToolRegistry` pre-loaded with all discovered built-in tools.

    Tools are auto-discovered from the ``fim_one.core.tool.builtin`` package.
    To add a new tool, simply drop a module containing a ``BaseTool`` subclass
    into that package — no manual registration needed.

    When *sandbox_root* is provided, sandboxed tools (file_ops, shell_exec,
    python_exec) are configured with per-conversation subdirectories under
    that root.

    When *sandbox_config* is provided (from an agent's ``sandbox_config``
    column), per-agent resource limits are applied to exec tools.

    When *uploads_root* is provided, tools that produce files (generate_image,
    python_exec, etc.) copy artifacts to a per-conversation subdirectory so
    files are isolated and cleaned up when the conversation is deleted.
    """
    registry = ToolRegistry()
    for tool in discover_builtin_tools(
        sandbox_root=sandbox_root,
        sandbox_config=sandbox_config,
        uploads_root=uploads_root,
    ):
        registry.register(tool)
    logger.info("Registered %d built-in tools: %s", len(registry), registry)
    return registry


def get_llm_from_config(config: dict[str, object]) -> OpenAICompatibleLLM | None:
    """Build an LLM from an agent's ``model_config_json`` dict.

    Accepts either inline config (``model_name``, ``base_url``, ``api_key``)
    or falls back to env-based defaults for any missing field.

    Returns ``None`` when ``config`` is empty or has no usable model info.
    """
    if not config:
        return None
    model_name = config.get("model_name") or config.get("model")
    if not model_name:
        return None
    ctx_size = config.get("context_size")
    return OpenAICompatibleLLM(
        api_key=str(config.get("api_key", "")) or _api_key(),
        base_url=str(config.get("base_url", "")) or _base_url(),
        model=str(model_name),
        default_temperature=float(config.get("temperature", 0) or _temperature()),
        default_max_tokens=int(config.get("max_output_tokens", 0) or _main_max_output()),
        reasoning_effort=_reasoning_effort(),
        reasoning_budget_tokens=_reasoning_budget_tokens(),
        provider=str(config.get("provider", "")) or None,
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=int(ctx_size) if ctx_size else None,
    )


async def get_llm_by_config_id(
    db: "AsyncSession",
    config_id: str,
) -> OpenAICompatibleLLM | None:
    """Look up a ModelConfig by id and build an LLM instance."""
    from sqlalchemy import select
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    stmt = select(ModelConfigORM).where(
        ModelConfigORM.id == config_id,
        ModelConfigORM.is_active == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return None
    return OpenAICompatibleLLM(
        api_key=cfg.api_key or _api_key(),
        base_url=cfg.base_url or _base_url(),
        model=cfg.model_name,
        default_temperature=cfg.temperature if cfg.temperature is not None else _temperature(),
        default_max_tokens=cfg.max_output_tokens or _main_max_output(),
        reasoning_effort=_reasoning_effort(),
        reasoning_budget_tokens=_reasoning_budget_tokens(),
        provider=cfg.provider or None,
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=cfg.context_size,
    )


async def get_system_default_llm(
    db: "AsyncSession",
) -> OpenAICompatibleLLM | None:
    """Query the system-level default model config (user_id=NULL, is_default=True).

    Deprecated: prefer ``get_system_llm_by_role`` for role-based lookup.
    Kept for backward compatibility.
    """
    from sqlalchemy import select
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    stmt = (
        select(ModelConfigORM)
        .where(
            ModelConfigORM.user_id == None,  # noqa: E711
            ModelConfigORM.category == "llm",
            ModelConfigORM.is_default == True,  # noqa: E712
            ModelConfigORM.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return None
    return OpenAICompatibleLLM(
        api_key=cfg.api_key or _api_key(),
        base_url=cfg.base_url or _base_url(),
        model=cfg.model_name,
        default_temperature=cfg.temperature if cfg.temperature is not None else _temperature(),
        default_max_tokens=cfg.max_output_tokens or _main_max_output(),
        reasoning_effort=_reasoning_effort(),
        reasoning_budget_tokens=_reasoning_budget_tokens(),
        provider=cfg.provider or None,
        json_mode_enabled=_json_mode_enabled(),
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=cfg.context_size,
    )


async def get_system_llm_by_role(
    db: "AsyncSession",
    role: str,
) -> "OpenAICompatibleLLM | None":
    """Query a system-level model config by role (user_id=NULL).

    Returns an :class:`OpenAICompatibleLLM` built from the matching config, or
    ``None`` if no active config with that role exists.
    """
    from sqlalchemy import select
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    stmt = (
        select(ModelConfigORM)
        .where(
            ModelConfigORM.user_id == None,  # noqa: E711
            ModelConfigORM.role == role,
            ModelConfigORM.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return None
    return OpenAICompatibleLLM(
        api_key=cfg.api_key or _api_key(),
        base_url=cfg.base_url or _base_url(),
        model=cfg.model_name,
        default_temperature=cfg.temperature if cfg.temperature is not None else _temperature(),
        default_max_tokens=cfg.max_output_tokens or _main_max_output(),
        reasoning_effort=_reasoning_effort(),
        reasoning_budget_tokens=_reasoning_budget_tokens(),
        provider=cfg.provider or None,
        json_mode_enabled=cfg.json_mode_enabled,
        tool_choice_enabled=_tool_choice_enabled(),
        context_size=cfg.context_size,
    )


async def get_effective_llm(db: "AsyncSession") -> OpenAICompatibleLLM:
    """General model: DB role='general' -> DB is_default -> ENV fallback."""
    llm = await get_system_llm_by_role(db, "general")
    if llm is not None:
        return llm
    llm = await get_system_default_llm(db)
    return llm if llm is not None else get_llm()


async def get_effective_fast_llm(db: "AsyncSession") -> OpenAICompatibleLLM:
    """Fast model: DB role='fast' -> DB role='general' -> ENV fast fallback."""
    llm = await get_system_llm_by_role(db, "fast")
    if llm is not None:
        return llm
    llm = await get_system_llm_by_role(db, "general")
    return llm if llm is not None else get_fast_llm()


async def _get_context_budget_for_role(
    db: "AsyncSession",
    role: str,
    env_fallback_fn: "callable[[], int]",
) -> int:
    """Return input token budget for the given role from DB config or ENV."""
    from sqlalchemy import select
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    stmt = (
        select(ModelConfigORM)
        .where(
            ModelConfigORM.user_id == None,  # noqa: E711
            ModelConfigORM.role == role,
            ModelConfigORM.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if cfg is None or cfg.context_size is None:
        return env_fallback_fn()
    max_out = cfg.max_output_tokens or _main_max_output()
    return _compute_input_budget(cfg.context_size, max_out)


async def get_effective_context_budget(db: "AsyncSession") -> int:
    """Input token budget for the general model: DB context_size -> ENV."""
    return await _get_context_budget_for_role(db, "general", get_context_budget)


async def get_effective_fast_context_budget(db: "AsyncSession") -> int:
    """Input token budget for the fast model: DB role=fast -> role=general -> ENV."""
    from sqlalchemy import select
    from fim_one.web.models.model_config import ModelConfig as ModelConfigORM

    for role in ("fast", "general"):
        stmt = (
            select(ModelConfigORM)
            .where(
                ModelConfigORM.user_id == None,  # noqa: E711
                ModelConfigORM.role == role,
                ModelConfigORM.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        result = await db.execute(stmt)
        cfg = result.scalar_one_or_none()
        if cfg is not None and cfg.context_size is not None:
            max_out = cfg.max_output_tokens or _main_max_output()
            return _compute_input_budget(cfg.context_size, max_out)
    return get_fast_context_budget()


# Alias for convenience — endpoints can use either name.
get_db = get_session


def get_embedding() -> OpenAICompatibleEmbedding:
    """Create an embedding model from environment config.

    Priority: ``JINA_API_KEY`` with Jina defaults, then ``EMBEDDING_API_KEY``
    with ``EMBEDDING_BASE_URL``.
    """
    jina_key = os.environ.get("JINA_API_KEY", "")
    emb_key = os.environ.get("EMBEDDING_API_KEY", "") or jina_key
    base_url = os.environ.get("EMBEDDING_BASE_URL", "https://api.jina.ai/v1")
    model = os.environ.get("EMBEDDING_MODEL", "jina-embeddings-v3")
    dim = int(os.environ.get("EMBEDDING_DIMENSION", "1024"))
    return OpenAICompatibleEmbedding(
        api_key=emb_key,
        base_url=base_url,
        model=model,
        dim=dim,
    )


def get_reranker() -> BaseReranker | None:
    """Create a reranker from environment config.

    Respects RERANKER_PROVIDER env var (jina / cohere / openai).
    Falls back to auto-detecting from available API keys.
    """
    provider = os.environ.get("RERANKER_PROVIDER", "").lower()

    if provider == "cohere" or (not provider and os.environ.get("COHERE_API_KEY")):
        cohere_key = os.environ.get("COHERE_API_KEY", "")
        if not cohere_key:
            return None
        model = os.environ.get("COHERE_RERANKER_MODEL", "rerank-multilingual-v3.0")
        from fim_one.core.reranker.cohere import CohereReranker
        return CohereReranker(api_key=cohere_key, model=model)

    if provider == "openai":
        model = os.environ.get("RERANKER_MODEL", "text-embedding-3-small")
        from fim_one.core.reranker.openai import OpenAIReranker
        return OpenAIReranker(model=model)

    # Default: Jina — skip silently if no key available
    jina_key = os.environ.get("JINA_API_KEY", "")
    if not jina_key:
        return None
    model = os.environ.get("RERANKER_MODEL", "jina-reranker-v2-base-multilingual")
    from fim_one.core.reranker.jina import JinaReranker
    return JinaReranker(api_key=jina_key, model=model)


def get_vector_store() -> LanceDBVectorStore:
    """Create a LanceDB vector store from environment config."""
    base_dir = os.environ.get("VECTOR_STORE_DIR", "./data/vector_store")
    dim = int(os.environ.get("EMBEDDING_DIMENSION", "1024"))
    return LanceDBVectorStore(base_dir=base_dir, embedding_dim=dim)


def get_kb_manager() -> KnowledgeBaseManager:
    """Create a KnowledgeBaseManager wired with embedding, store, and reranker."""
    return KnowledgeBaseManager(
        store=get_vector_store(),
        embedding=get_embedding(),
        reranker=get_reranker(),
    )


async def get_mcp_tools(registry: ToolRegistry) -> MCPClient | None:
    """Connect to MCP servers defined in ``MCP_SERVERS`` and register their tools.

    The ``MCP_SERVERS`` environment variable should be a JSON array of server
    config objects::

        [
          {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {}
          }
        ]

    Each server's tools are discovered and registered in *registry* with
    names prefixed by the server name (e.g. ``filesystem__read_file``).

    Returns
    -------
    MCPClient | None
        The connected :class:`MCPClient` instance, or ``None`` if
        ``MCP_SERVERS`` is not set.  The caller is responsible for calling
        ``await client.disconnect_all()`` on shutdown.
    """
    servers_json = os.environ.get("MCP_SERVERS", "")
    if not servers_json:
        return None

    try:
        from fim_one.core.mcp import MCPClient
    except ImportError:
        logger.warning(
            "MCP_SERVERS is set but the 'mcp' package is not installed. "
            "Install it with: uv sync --extra mcp"
        )
        return None

    servers: list[dict[str, object]] = json.loads(servers_json)
    client = MCPClient()

    for server in servers:
        name = str(server["name"])
        command = str(server["command"])
        args = [str(a) for a in server.get("args", [])]  # type: ignore[union-attr]
        env = server.get("env")  # type: ignore[assignment]

        try:
            tools = await client.connect_stdio(
                name=name,
                command=command,
                args=args,
                env=env,  # type: ignore[arg-type]
            )
            for tool in tools:
                registry.register(tool)
        except Exception:
            logger.exception("Failed to connect to MCP server %r", name)

    return client
