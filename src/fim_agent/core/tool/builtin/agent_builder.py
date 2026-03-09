"""Builder tools for managing Agent settings via LLM agent.

Tools in this module are injected exclusively for Builder Agents (is_builder=True)
that have "agent_builder" in their tool_categories.  They are excluded from
auto-discovery to prevent regular agents from accessing them.
"""

from __future__ import annotations

import json
import logging
from abc import ABC
from typing import Any

from sqlalchemy import select

from ..base import BaseTool

logger = logging.getLogger(__name__)


class _AgentBuilderBase(BaseTool, ABC):
    """Shared base for all agent-builder tools."""

    def __init__(self, agent_id: str, user_id: str) -> None:
        self.agent_id = agent_id
        self.user_id = user_id

    @property
    def category(self) -> str:
        return "agent_builder"

    async def _get_agent(self, db):
        from fim_agent.web.models.agent import Agent
        result = await db.execute(
            select(Agent).where(
                Agent.id == self.agent_id,
                Agent.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()


class AgentGetSettingsTool(_AgentBuilderBase):
    """Get current settings of the target agent."""

    @property
    def name(self) -> str:
        return "agent_get_settings"

    @property
    def display_name(self) -> str:
        return "Get Agent Settings"

    @property
    def description(self) -> str:
        return "Get the current settings of the target agent including instructions, tool categories, execution mode, and more."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        async with create_session() as db:
            agent = await self._get_agent(db)
            if agent is None:
                return "[Error] Agent not found or access denied."
            result = {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "instructions": agent.instructions,
                "execution_mode": agent.execution_mode,
                "tool_categories": agent.tool_categories or [],
                "status": agent.status,
                "model_config": agent.model_config_json,
                "suggested_prompts": agent.suggested_prompts or [],
            }
            return json.dumps(result, ensure_ascii=False, indent=2)


class AgentUpdateSettingsTool(_AgentBuilderBase):
    """Update settings of the target agent."""

    @property
    def name(self) -> str:
        return "agent_update_settings"

    @property
    def display_name(self) -> str:
        return "Update Agent Settings"

    @property
    def description(self) -> str:
        return (
            "Update one or more settings of the target agent. "
            "Only provided fields are changed. "
            "Updatable fields: name, description, instructions, execution_mode, tool_categories, suggested_prompts."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "New agent name."},
                "description": {"type": "string", "description": "New agent description."},
                "instructions": {"type": "string", "description": "New system prompt / instructions for the agent."},
                "execution_mode": {
                    "type": "string",
                    "enum": ["react", "dag"],
                    "description": "New execution mode.",
                },
                "tool_categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New list of tool categories. Valid values: computation, web, filesystem, knowledge, connector, general, mcp.",
                },
                "suggested_prompts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New list of suggested prompts shown at conversation start.",
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        async with create_session() as db:
            agent = await self._get_agent(db)
            if agent is None:
                return "[Error] Agent not found or access denied."

            _updatable = {"name", "description", "instructions", "execution_mode", "tool_categories", "suggested_prompts"}
            updates = {k: v for k, v in kwargs.items() if k in _updatable}
            if not updates:
                return "[Error] At least one updatable field must be provided."

            for field, value in updates.items():
                setattr(agent, field, value)

            await db.commit()
            return json.dumps(
                {"updated": True, "fields": list(updates.keys())},
                ensure_ascii=False,
            )


class AgentListConnectorsTool(_AgentBuilderBase):
    """List all connectors available to the current user."""

    @property
    def name(self) -> str:
        return "agent_list_connectors"

    @property
    def display_name(self) -> str:
        return "List Available Connectors"

    @property
    def description(self) -> str:
        return (
            "List all connectors owned by the user, including their IDs, names, "
            "descriptions, and action counts. Use this to find connector IDs before "
            "calling agent_add_connector."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector
        from sqlalchemy.orm import selectinload

        async with create_session() as db:
            result = await db.execute(
                select(Connector)
                .options(selectinload(Connector.actions))
                .where(Connector.user_id == self.user_id)
                .order_by(Connector.name)
            )
            connectors = result.scalars().all()

            # Also get the agent's currently attached connector_ids
            agent = await self._get_agent(db)
            attached_ids: set[str] = set(agent.connector_ids or []) if agent else set()

            items = [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                    "icon": c.icon,
                    "base_url": c.base_url,
                    "action_count": len(c.actions),
                    "attached": c.id in attached_ids,
                }
                for c in connectors
            ]
            return json.dumps(
                {"connectors": items, "total": len(items)},
                ensure_ascii=False,
                indent=2,
            )


class AgentAddConnectorTool(_AgentBuilderBase):
    """Attach a connector to the agent so the agent can use its actions as tools."""

    @property
    def name(self) -> str:
        return "agent_add_connector"

    @property
    def display_name(self) -> str:
        return "Add Connector to Agent"

    @property
    def description(self) -> str:
        return (
            "Attach a connector to this agent, making all of its actions available "
            "as tools during conversations. Use agent_list_connectors first to find "
            "the connector_id."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "The UUID of the connector to attach.",
                },
            },
            "required": ["connector_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector

        connector_id: str = kwargs["connector_id"]

        async with create_session() as db:
            # Verify the connector exists and belongs to the user
            c_result = await db.execute(
                select(Connector).where(
                    Connector.id == connector_id,
                    Connector.user_id == self.user_id,
                )
            )
            connector = c_result.scalar_one_or_none()
            if connector is None:
                return f"[Error] Connector {connector_id!r} not found or access denied."

            agent = await self._get_agent(db)
            if agent is None:
                return "[Error] Agent not found or access denied."

            current: list[str] = list(agent.connector_ids or [])
            if connector_id in current:
                return json.dumps(
                    {"added": False, "reason": "already attached", "connector_id": connector_id},
                    ensure_ascii=False,
                )

            current.append(connector_id)
            agent.connector_ids = current
            await db.commit()

            return json.dumps(
                {"added": True, "connector_id": connector_id, "connector_name": connector.name},
                ensure_ascii=False,
            )


class AgentRemoveConnectorTool(_AgentBuilderBase):
    """Detach a connector from the agent."""

    @property
    def name(self) -> str:
        return "agent_remove_connector"

    @property
    def display_name(self) -> str:
        return "Remove Connector from Agent"

    @property
    def description(self) -> str:
        return "Detach a connector from this agent. The connector itself is not deleted."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "connector_id": {
                    "type": "string",
                    "description": "The UUID of the connector to detach.",
                },
            },
            "required": ["connector_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session

        connector_id: str = kwargs["connector_id"]

        async with create_session() as db:
            agent = await self._get_agent(db)
            if agent is None:
                return "[Error] Agent not found or access denied."

            current: list[str] = list(agent.connector_ids or [])
            if connector_id not in current:
                return json.dumps(
                    {"removed": False, "reason": "not attached", "connector_id": connector_id},
                    ensure_ascii=False,
                )

            current.remove(connector_id)
            agent.connector_ids = current
            await db.commit()

            return json.dumps(
                {"removed": True, "connector_id": connector_id},
                ensure_ascii=False,
            )


class AgentSetModelTool(_AgentBuilderBase):
    """Change the LLM model and generation parameters for the agent."""

    @property
    def name(self) -> str:
        return "agent_set_model"

    @property
    def display_name(self) -> str:
        return "Set Agent Model"

    @property
    def description(self) -> str:
        return (
            "Update the LLM model and/or generation parameters (temperature, max_tokens) "
            "for this agent. Use agent_get_settings first to see the current model_config."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": (
                        "Model name as registered in ModelRegistry, "
                        "e.g. 'gpt-4o', 'claude-sonnet-4-6', 'deepseek-chat'."
                    ),
                },
                "temperature": {
                    "type": "number",
                    "description": "Sampling temperature (0.0–2.0). Lower = more deterministic.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum tokens in the model's response.",
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session

        updates = {k: v for k, v in kwargs.items() if k in {"model", "temperature", "max_tokens"}}
        if not updates:
            return "[Error] At least one of model, temperature, or max_tokens must be provided."

        async with create_session() as db:
            agent = await self._get_agent(db)
            if agent is None:
                return "[Error] Agent not found or access denied."

            current_cfg: dict = dict(agent.model_config_json or {})
            current_cfg.update(updates)
            agent.model_config_json = current_cfg
            await db.commit()

            return json.dumps(
                {"updated": True, "model_config": current_cfg},
                ensure_ascii=False,
            )
