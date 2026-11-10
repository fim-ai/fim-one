"""What an agent loop is built from, and the port that builds it.

Model resolution and the built-in tools live in the web layer
(``web/deps.py``), so the assembly of an agent's runtime is a port the web
layer registers at start-up. Chat, a ``call_agent`` delegate and a workflow
Agent step all get their runtime from it: one order of assembly, one set of
rules, whoever asks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fim_one.core.agent.hooks import HookRegistry
    from fim_one.core.model.base import BaseLLM
    from fim_one.core.tool.registry import ToolRegistry

    from .principal import CallContext

__all__ = [
    "AgentRuntime",
    "AgentRuntimeFactory",
    "get_agent_runtime_factory",
    "set_agent_runtime_factory",
]


@dataclass
class AgentRuntime:
    """Everything one run of one agent is built from."""

    # The context the tools were built under and their calls are recorded as.
    ctx: CallContext
    # The agent's configuration; ``None`` when the run has no agent.
    agent: Mapping[str, Any] | None
    llm: BaseLLM
    fast_llm: BaseLLM
    # Built-in, connector, knowledge-base and MCP tools; MCP servers are connected.
    tools: ToolRegistry
    # The agent's enforcement hooks, the approval gate among them.
    hooks: HookRegistry
    instructions: str | None = None
    # Holds the MCP connections; closed by :meth:`aclose`.
    mcp_client: Any | None = None

    async def aclose(self) -> None:
        """Close the MCP connections. Call it from the task that built the runtime."""
        client, self.mcp_client = self.mcp_client, None
        if client is not None:
            await client.disconnect_all()


class AgentRuntimeFactory(Protocol):
    """Builds the runtime of an agent for a caller. Registered by the web layer."""

    async def build(
        self,
        ctx: CallContext,
        agent_id: str | None,
        *,
        conversation_id: str | None = None,
    ) -> AgentRuntime:
        """The runtime of *agent_id* as used through *ctx*.

        The agent is judged by ``ctx``'s subject (the binding's owner, or the
        principal with no binding); the resources bound to it by the agent's
        owner; credentials by the principal. Raises
        :class:`~fim_one.core.assembly.errors.AssemblyRefusal` when the agent
        is not usable. Must be awaited in the task that will run the agent
        and later call :meth:`AgentRuntime.aclose` (MCP connections are
        scoped to the task that opened them).
        """
        ...


_factory: AgentRuntimeFactory | None = None


def set_agent_runtime_factory(factory: AgentRuntimeFactory | None) -> None:
    """Register the runtime factory (``None`` unregisters it)."""
    global _factory
    _factory = factory


def get_agent_runtime_factory() -> AgentRuntimeFactory:
    """The registered factory; raises when the web layer has not registered one."""
    if _factory is None:
        raise RuntimeError(
            "No agent runtime factory is registered. The web layer registers one at "
            "start-up (fim_one.web.services.assembly_ports.register_assembly_ports)."
        )
    return _factory
