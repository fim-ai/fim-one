"""CallAgent builtin tool — delegate a task to a specialist agent."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fim_one.core.tool.base import BaseTool


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
    ):
        """
        available_agents: list of {id, name, description, instructions, model_config_json, ...}
        tool_resolver: optional async callback (agent_cfg, conv_id) -> ToolRegistry
        """
        self._agents = {a["id"]: a for a in available_agents}
        self._calling_user_id = calling_user_id
        self._tool_resolver = tool_resolver
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

    async def run(self, **kwargs: Any) -> str:
        """Run the specified agent on the task and return its response."""
        agent_id: str = kwargs.get("agent_id", "")
        task: str = kwargs.get("task", "")
        from fim_one.core.agent.react import ReActAgent
        from fim_one.core.model.registry import ModelRegistry

        agent_cfg = self._agents.get(agent_id)
        if not agent_cfg:
            return f"Error: agent {agent_id} not found"

        # Resolve full tools for the sub-agent via callback
        if self._tool_resolver:
            try:
                sub_tools = await self._tool_resolver(agent_cfg, None)
                # Exclude call_agent from sub-tools to prevent infinite recursion
                sub_tools = sub_tools.exclude_by_name("call_agent")
            except Exception:
                from fim_one.core.tool.registry import ToolRegistry
                sub_tools = ToolRegistry()
        else:
            from fim_one.core.tool.registry import ToolRegistry
            sub_tools = ToolRegistry()

        # Resolve model
        model_cfg = agent_cfg.get("model_config_json") or {}
        model_name = model_cfg.get("model") if model_cfg else None

        try:
            registry = ModelRegistry()
            if model_name:
                llm = registry.get(model_name)
            else:
                llm = registry.get_default()
        except Exception:
            return f"Error: could not load model for agent {agent_id}"

        instructions = agent_cfg.get("instructions") or ""

        sub_agent = ReActAgent(
            llm=llm,
            tools=sub_tools,
            extra_instructions=instructions,
            max_iterations=5,
        )

        try:
            result = await sub_agent.run(task)
            return str(result)
        except Exception as e:
            return f"Sub-agent error: {e}"
