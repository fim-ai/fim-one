"""Dependency providers for the FIM Agent web layer.

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

from fim_agent.core.embedding.openai_compatible import OpenAICompatibleEmbedding
from fim_agent.core.model import OpenAICompatibleLLM
from fim_agent.core.model.registry import ModelRegistry
from fim_agent.core.reranker.base import BaseReranker
from fim_agent.core.tool import ToolRegistry
from fim_agent.core.tool.builtin import discover_builtin_tools
from fim_agent.db import get_session
from fim_agent.rag.manager import KnowledgeBaseManager
from fim_agent.rag.store.lancedb import LanceDBVectorStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_agent.core.mcp import MCPClient

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def _base_url() -> str:
    return os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")


def _main_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o")


def _fast_model() -> str:
    return os.environ.get("FAST_LLM_MODEL", "") or _main_model()


def _temperature() -> float:
    return float(os.environ.get("LLM_TEMPERATURE", "0.7"))


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


def get_react_max_iterations() -> int:
    """Max iterations for ReAct agent mode (env: REACT_MAX_ITERATIONS)."""
    return int(os.environ.get("REACT_MAX_ITERATIONS", "20"))


# Reserve for system prompt + tool descriptions in the context window.
_SYSTEM_PROMPT_RESERVE = 4_000


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
    )


def get_fast_llm() -> OpenAICompatibleLLM:
    """Create the fast LLM for DAG step execution.

    Falls back to the main model if ``FAST_LLM_MODEL`` is not set.
    """
    return OpenAICompatibleLLM(
        api_key=_api_key(),
        base_url=_base_url(),
        model=_fast_model(),
        default_temperature=_temperature(),
        default_max_tokens=_fast_max_output(),
    )


def get_model_registry() -> ModelRegistry:
    """Create a :class:`ModelRegistry` with main and fast models."""
    registry = ModelRegistry()

    registry.register(
        "main",
        get_llm(),
        roles=["general", "planning", "analysis"],
    )

    fast = get_fast_llm()
    # Only register as a separate entry if it's actually a different model.
    if _fast_model() != _main_model():
        registry.register("fast", fast, roles=["fast", "execution"])
    else:
        # Same model — register the "fast" role on the main entry.
        registry._roles.setdefault("fast", []).append("main")
        registry._roles.setdefault("execution", []).append("main")

    return registry


def get_tools(
    *,
    sandbox_root: Path | None = None,
    sandbox_config: dict | None = None,
    uploads_root: Path | None = None,
) -> ToolRegistry:
    """Create a :class:`ToolRegistry` pre-loaded with all discovered built-in tools.

    Tools are auto-discovered from the ``fim_agent.core.tool.builtin`` package.
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
    return OpenAICompatibleLLM(
        api_key=str(config.get("api_key", "")) or _api_key(),
        base_url=str(config.get("base_url", "")) or _base_url(),
        model=str(model_name),
        default_temperature=float(config.get("temperature", 0) or _temperature()),
        default_max_tokens=int(config.get("max_output_tokens", 0) or _main_max_output()),
    )


async def get_llm_by_config_id(
    db: "AsyncSession",
    config_id: str,
) -> OpenAICompatibleLLM | None:
    """Look up a ModelConfig by id and build an LLM instance."""
    from sqlalchemy import select
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM

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
    )


async def get_system_default_llm(
    db: "AsyncSession",
) -> OpenAICompatibleLLM | None:
    """Query the system-level default model config (user_id=NULL, is_default=True).

    Deprecated: prefer ``get_system_llm_by_role`` for role-based lookup.
    Kept for backward compatibility.
    """
    from sqlalchemy import select
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM

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
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM

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
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM

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
    from fim_agent.web.models.model_config import ModelConfig as ModelConfigORM

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
        from fim_agent.core.reranker.cohere import CohereReranker
        return CohereReranker(api_key=cohere_key, model=model)

    if provider == "openai":
        model = os.environ.get("RERANKER_MODEL", "text-embedding-3-small")
        from fim_agent.core.reranker.openai import OpenAIReranker
        return OpenAIReranker(model=model)

    # Default: Jina — skip silently if no key available
    jina_key = os.environ.get("JINA_API_KEY", "")
    if not jina_key:
        return None
    model = os.environ.get("RERANKER_MODEL", "jina-reranker-v2-base-multilingual")
    from fim_agent.core.reranker.jina import JinaReranker
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
        from fim_agent.core.mcp import MCPClient
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
