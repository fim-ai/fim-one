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
"""

from __future__ import annotations

import os
import logging

from fim_agent.core.model import OpenAICompatibleLLM
from fim_agent.core.model.registry import ModelRegistry
from fim_agent.core.tool import ToolRegistry
from fim_agent.core.tool.builtin.python_exec import PythonExecTool
from fim_agent.core.tool.builtin.web_fetch import WebFetchTool
from fim_agent.core.tool.builtin.web_search import WebSearchTool

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


def get_llm() -> OpenAICompatibleLLM:
    """Create the main (smart) LLM for planning, analysis, and ReAct."""
    return OpenAICompatibleLLM(
        api_key=_api_key(),
        base_url=_base_url(),
        model=_main_model(),
        default_temperature=_temperature(),
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


def get_tools() -> ToolRegistry:
    """Create a :class:`ToolRegistry` pre-loaded with the default tool set."""
    registry = ToolRegistry()
    registry.register(PythonExecTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    return registry


def get_user_id() -> str:
    """Return the current user identifier.

    This is a placeholder that always returns ``"default"``.  It is pre-wired
    so that future authentication middleware can override the value without
    touching endpoint signatures.
    """
    return "default"
