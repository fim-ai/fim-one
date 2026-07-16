"""CallAgent builtin tool -- delegate a task to another agent."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fim_one.core.agent.hooks import HookRegistry
from fim_one.core.model.base import BaseLLM
from fim_one.core.tool.base import BaseTool

logger = logging.getLogger(__name__)

# Type alias for the LLM resolver callback.
# Signature: (agent_cfg) -> BaseLLM
# The callback implements the full 3-tier resolution logic with DB access.
LLMResolver = Callable[[dict[str, Any]], Awaitable[BaseLLM]]

# Type alias for the hook resolver callback.
# Signature: (agent_cfg) -> HookRegistry
# Builds the *delegate's own* registry rather than inheriting the caller's.
# ``build_hook_registry_for_agent`` auto-attaches the confirmation gate to
# every agent, so rebuilding from the delegate's config is a superset of
# inheritance: it yields the gate plus any hooks the delegate declares.
HookResolver = Callable[[dict[str, Any]], Awaitable[HookRegistry]]


class CallAgentTool(BaseTool):
    """Tool that delegates tasks to specialist agents.

    Dynamically builds its description and parameter enum from the list
    of available agents provided at construction time.
    """

    def __init__(
        self,
        available_agents: list[dict[str, Any]],
        calling_user_id: str,
        tool_resolver: Callable[[dict[str, Any], str | None], Awaitable[Any]] | None = None,
        llm_resolver: LLMResolver | None = None,
        hook_resolver: HookResolver | None = None,
    ):
        """
        Parameters
        ----------
        available_agents:
            list of {id, name, description, instructions, model_config_json, ...}
        calling_user_id:
            ID of the user initiating the call.
        tool_resolver:
            optional async callback ``(agent_cfg, conv_id) -> ToolRegistry``
        llm_resolver:
            optional async callback ``(agent_cfg) -> BaseLLM`` that resolves
            the LLM for a delegated agent using the full 3-tier fallback
            (config_id -> inline config -> system default).
        hook_resolver:
            optional async callback ``(agent_cfg) -> HookRegistry`` building
            the delegate's enforcement hooks.  When omitted, the delegate
            runs with no hooks — acceptable only for callers that have no
            hooks to enforce (core-level tests, offline eval).  Web call
            sites MUST wire this: without it a delegated connector write
            skips the confirmation gate the same call would hit inline.
        """
        self._agents = {a["id"]: a for a in available_agents}
        self._calling_user_id = calling_user_id
        self._tool_resolver = tool_resolver
        self._llm_resolver = llm_resolver
        self._hook_resolver = hook_resolver
        agent_list = "\n".join(
            f"  - {a['name']} (id={a['id']}): {a.get('description', '')}"
            for a in available_agents
        )
        self._description = (
            f"Delegate a task to a specialist agent. "
            f"Available agents:\n{agent_list}"
        )

    @property
    def name(self) -> str:
        return "call_agent"

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        agent_ids = list(self._agents.keys())
        return {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "enum": agent_ids,
                    "description": "ID of the agent to delegate to",
                },
                "task": {
                    "type": "string",
                    "description": "The task or question to delegate to the agent",
                },
            },
            "required": ["agent_id", "task"],
        }

    async def _resolve_llm(self, agent_cfg: dict[str, Any]) -> BaseLLM:
        """Resolve an LLM for the delegated agent using the injected callback or ENV fallback.

        Resolution order:
        1. Injected ``llm_resolver`` callback (has full DB access for 3-tier resolution)
        2. Inline ``model_config_json`` via ``get_llm_from_config()`` (no DB needed)
        3. ENV-based ``get_model_registry()`` default (no DB needed)
        """
        # Tier 1: Use the injected resolver if available (supports DB lookups)
        if self._llm_resolver is not None:
            return await self._llm_resolver(agent_cfg)

        # Tier 2: Try inline model_config_json (no DB needed)
        from fim_one.web.deps import get_llm_from_config

        model_cfg = agent_cfg.get("model_config_json") or {}
        if model_cfg and isinstance(model_cfg, dict):
            llm = get_llm_from_config(model_cfg)
            if llm is not None:
                return llm

        # Tier 3: Fall back to ENV-based model registry
        from fim_one.web.deps import get_model_registry

        registry = get_model_registry()
        return registry.get_default()

    async def run(self, **kwargs: Any) -> str:
        """Run the specified agent on the task and return its response."""
        agent_id: str = kwargs.get("agent_id", "")
        task: str = kwargs.get("task", "")
        from fim_one.core.agent.react import ReActAgent

        agent_cfg = self._agents.get(agent_id)
        if not agent_cfg:
            return f"Error: agent {agent_id} not found"

        # Resolve full tools for the delegated agent via callback
        if self._tool_resolver:
            try:
                delegate_tools = await self._tool_resolver(agent_cfg, None)
                # Exclude call_agent to prevent infinite recursion
                delegate_tools = delegate_tools.exclude_by_name("call_agent")
            except Exception:
                logger.warning(
                    "Failed to resolve tools for agent %s", agent_id,
                    exc_info=True,
                )
                from fim_one.core.tool.registry import ToolRegistry
                delegate_tools = ToolRegistry()
        else:
            from fim_one.core.tool.registry import ToolRegistry
            delegate_tools = ToolRegistry()

        # Resolve model using the 3-tier fallback
        try:
            llm = await self._resolve_llm(agent_cfg)
        except Exception:
            logger.error(
                "Failed to resolve LLM for agent %s", agent_id,
                exc_info=True,
            )
            return f"Error: could not load model for agent {agent_id}"

        # Resolve the delegate's own enforcement hooks.  Fail CLOSED: a
        # delegate that cannot build its hooks would run its tools past an
        # approval gate that the same call would hit when made inline, so
        # abort the delegation instead of silently running it unguarded.
        hook_registry: HookRegistry | None = None
        if self._hook_resolver is not None:
            try:
                hook_registry = await self._hook_resolver(agent_cfg)
            except Exception:
                logger.error(
                    "Failed to resolve hooks for agent %s — refusing to "
                    "delegate unguarded",
                    agent_id,
                    exc_info=True,
                )
                return (
                    f"Error: could not load enforcement hooks for agent "
                    f"{agent_id}; delegation aborted"
                )

        instructions = agent_cfg.get("instructions") or ""

        # ``agent_id``/``user_id`` are what make the hooks above effective:
        # FeishuGateHook keys off ``HookContext.agent_id`` to load the
        # delegate's row (and its ``require_confirmation_for_all``), and
        # bows out entirely when it is absent.  Passing the registry without
        # these would leave the gate a no-op.
        delegate = ReActAgent(
            llm=llm,
            tools=delegate_tools,
            extra_instructions=instructions,
            max_iterations=5,
            hook_registry=hook_registry,
            agent_id=agent_id,
            user_id=self._calling_user_id or None,
        )

        try:
            result = await delegate.run(task)
            return str(result)
        except Exception as e:
            logger.error(
                "Delegated agent %s failed: %s", agent_id, e,
                exc_info=True,
            )
            return f"Agent delegation error: {e}"
