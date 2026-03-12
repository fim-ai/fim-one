"""CallAgent builtin tool — delegate a task to a specialist agent."""
from __future__ import annotations

from typing import Any

from fim_one.core.tool.base import BaseTool


class CallAgentTool(BaseTool):
    """Tool that delegates tasks to specialist agents.

    Dynamically builds its description and parameter enum from the list
    of available agents provided at construction time.
    """

    def __init__(self, available_agents: list[dict], calling_user_id: str):
        """
        available_agents: list of {id, name, description, instructions, model_config_json, ...}
        """
        self._agents = {a["id"]: a for a in available_agents}
        self._calling_user_id = calling_user_id
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
    def parameters_schema(self) -> dict:
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

    async def run(self, **kwargs: Any) -> str:  # type: ignore[override]
        """Run the specified agent on the task and return its response."""
        agent_id: str = kwargs.get("agent_id", "")
        task: str = kwargs.get("task", "")
        from fim_one.core.agent.react import ReActAgent
        from fim_one.core.model.registry import ModelRegistry
        from fim_one.core.tool.registry import ToolRegistry

        agent_cfg = self._agents.get(agent_id)
        if not agent_cfg:
            return f"Error: agent {agent_id} not found"

        # Build a minimal tool registry for the sub-agent (no call_agent to prevent recursion)
        sub_tools = ToolRegistry()
        # Sub-agent does NOT get CallAgentTool — only one level of delegation

        # Resolve model
        model_cfg = agent_cfg.get("model_config_json") or {}
        model_name = model_cfg.get("model") if model_cfg else None

        try:
            registry = ModelRegistry.get_instance()
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
            system_prompt=instructions,
            max_iterations=5,
        )

        try:
            result = await sub_agent.run(task)
            return str(result)
        except Exception as e:
            return f"Sub-agent error: {e}"
