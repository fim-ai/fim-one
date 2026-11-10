"""CallAgent builtin tool -- delegate a task to another agent."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fim_one.core.tool.base import BaseTool

if TYPE_CHECKING:
    from fim_one.core.assembly.principal import CallContext
    from fim_one.core.assembly.runtime import AgentRuntime, AgentRuntimeFactory

logger = logging.getLogger(__name__)


class CallAgentTool(BaseTool):
    """Tool that delegates tasks to specialist agents.

    Dynamically builds its description and parameter enum from the list
    of available agents provided at construction time.

    The delegate's runtime (its model, its bound connectors, knowledge bases
    and MCP servers, its enforcement hooks) comes from the assembly layer's
    agent runtime port, the same assembly a chat with that agent gets. The
    tool holds a catalogue of names only: the delegate's configuration is
    read, and its availability checked, when it is called.
    """

    def __init__(
        self,
        available_agents: list[dict[str, Any]],
        call_context: CallContext,
        *,
        runtime_factory: AgentRuntimeFactory | None = None,
        sandbox_id: str | None = None,
    ):
        """
        Parameters
        ----------
        available_agents:
            The catalogue offered to the model: ``{id, name, description}``.
        call_context:
            Who delegates, and from where. The delegate is judged by this
            context's subject; credentials resolve for its principal; call
            records carry its entry.
        runtime_factory:
            Builds the delegate's runtime. Defaults to the factory the web
            layer registered (``core.assembly.runtime``).
        sandbox_id:
            The conversation the delegation is made from: the delegate's
            shell, code-execution and file tools work in that conversation's
            directory, as in a chat with the delegate there. Without one
            each delegation gets a directory of its own, removed when the
            delegation ends.
        """
        self._agents = {a["id"]: a for a in available_agents}
        self._call_context = call_context
        self._runtime_factory = runtime_factory
        self._sandbox_id = sandbox_id
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

    async def _build_runtime(self, agent_id: str, sandbox_id: str) -> AgentRuntime:
        from fim_one.core.assembly.runtime import get_agent_runtime_factory

        factory = self._runtime_factory or get_agent_runtime_factory()
        return await factory.build(
            self._call_context, agent_id, conversation_id=None, sandbox_id=sandbox_id
        )

    async def run(self, **kwargs: Any) -> str:
        """Run the specified agent on the task and return its response."""
        agent_id: str = kwargs.get("agent_id", "")
        task: str = kwargs.get("task", "")
        from fim_one.core.assembly.sandbox import delegation_sandbox_id, release_sandbox

        if agent_id not in self._agents:
            return f"Error: agent {agent_id} not found"

        # The directory the delegate's shell, code and file tools work in:
        # the calling conversation's, or one of this delegation's own
        # (``release_sandbox`` removes only the latter).
        sandbox_id = self._sandbox_id or delegation_sandbox_id()
        try:
            return await self._delegate(agent_id, task, sandbox_id)
        finally:
            await release_sandbox(sandbox_id)

    async def _delegate(self, agent_id: str, task: str, sandbox_id: str) -> str:
        from fim_one.core.agent.react import ReActAgent
        from fim_one.core.assembly.errors import AssemblyRefusal

        # The delegate's whole runtime, or no delegation. Fail CLOSED: a
        # delegate that cannot be assembled (no model, no enforcement hooks)
        # would otherwise run stripped of the gates the same call hits when
        # made in a chat with that agent.
        try:
            runtime = await self._build_runtime(agent_id, sandbox_id)
        except AssemblyRefusal as refusal:
            return f"Error: agent {agent_id} is not available: {refusal.detail}"
        except Exception:
            logger.error(
                "Failed to assemble agent %s — refusing to delegate", agent_id, exc_info=True
            )
            return f"Error: could not set up agent {agent_id}; delegation aborted"

        try:
            # ``agent_id``/``user_id`` are what make the hooks effective:
            # the approval gate keys off ``HookContext.agent_id`` to load the
            # delegate's row (and its ``require_confirmation_for_all``).
            delegate = ReActAgent(
                llm=runtime.llm,
                # Exclude call_agent to prevent infinite recursion
                tools=runtime.tools.exclude_by_name("call_agent"),
                extra_instructions=runtime.instructions or "",
                max_iterations=5,
                hook_registry=runtime.hooks,
                agent_id=agent_id,
                user_id=self._call_context.principal.user_id or None,
            )
            result = await delegate.run(task)
            # Return the delegate's answer, never the ``AgentResult`` itself.
            # It is a plain dataclass, so ``str()`` renders its whole repr —
            # every StepResult, observation and message — which buries the
            # answer in kilobytes of trace and blows the caller's context.
            answer = (result.answer or "").strip()
            if not answer:
                return (
                    f"Agent {agent_id} finished after {result.iterations} "
                    "iteration(s) without producing a textual answer."
                )
            return answer
        except Exception as e:
            logger.error(
                "Delegated agent %s failed: %s", agent_id, e,
                exc_info=True,
            )
            return f"Agent delegation error: {e}"
        finally:
            # The MCP connections the runtime opened, in the task that
            # opened them.
            try:
                await runtime.aclose()
            except Exception:
                logger.warning(
                    "Failed to close the runtime of agent %s", agent_id, exc_info=True
                )
