"""Builder tools for managing Connector actions via LLM agent."""

from __future__ import annotations

import json
import logging
from abc import ABC
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..base import BaseTool

logger = logging.getLogger(__name__)


class _ConnectorBuilderBase(BaseTool, ABC):
    """Shared base for all connector-builder tools."""

    def __init__(self, connector_id: str, user_id: str) -> None:
        self.connector_id = connector_id
        self.user_id = user_id

    @property
    def category(self) -> str:
        return "builder"

    async def _get_connector(self, db):
        """Fetch the connector and verify ownership."""
        from fim_agent.web.models.connector import Connector

        result = await db.execute(
            select(Connector)
            .options(selectinload(Connector.actions))
            .where(
                Connector.id == self.connector_id,
                Connector.user_id == self.user_id,
            )
        )
        return result.scalar_one_or_none()


# ------------------------------------------------------------------
# ConnectorListActionsTool
# ------------------------------------------------------------------


class ConnectorListActionsTool(_ConnectorBuilderBase):
    """List all actions for the current connector."""

    @property
    def name(self) -> str:
        return "connector_list_actions"

    @property
    def display_name(self) -> str:
        return "List Connector Actions"

    @property
    def description(self) -> str:
        return (
            "List all existing actions for this connector, "
            "including their IDs, names, methods, paths, and schemas."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            actions = connector.actions or []
            result = {
                "connector": {
                    "id": connector.id,
                    "name": connector.name,
                    "base_url": connector.base_url,
                    "auth_type": connector.auth_type,
                },
                "actions": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "description": a.description,
                        "method": a.method,
                        "path": a.path,
                        "parameters_schema": a.parameters_schema,
                        "request_body_template": a.request_body_template,
                        "response_extract": a.response_extract,
                        "requires_confirmation": a.requires_confirmation,
                    }
                    for a in actions
                ],
                "total": len(actions),
            }
            return json.dumps(result, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------
# ConnectorCreateActionTool
# ------------------------------------------------------------------


class ConnectorCreateActionTool(_ConnectorBuilderBase):
    """Create a new action for the connector."""

    @property
    def name(self) -> str:
        return "connector_create_action"

    @property
    def display_name(self) -> str:
        return "Create Connector Action"

    @property
    def description(self) -> str:
        return (
            "Create a new API action for this connector. "
            "Specify method, path, and optionally parameters_schema and request_body_template."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Action name."},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "HTTP method.",
                },
                "path": {"type": "string", "description": "URL path (e.g. /api/users/{id})."},
                "description": {"type": "string", "description": "Action description."},
                "parameters_schema": {
                    "type": "object",
                    "description": "JSON Schema for path/query parameters.",
                },
                "request_body_template": {
                    "type": "object",
                    "description": "JSON template for the request body.",
                },
                "response_extract": {
                    "type": "string",
                    "description": "JMESPath expression to extract from response.",
                },
                "requires_confirmation": {
                    "type": "boolean",
                    "description": "Whether this action requires user confirmation before execution.",
                },
            },
            "required": ["name", "method", "path"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import ConnectorAction

        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            action = ConnectorAction(
                connector_id=self.connector_id,
                name=kwargs["name"],
                method=kwargs["method"],
                path=kwargs["path"],
                description=kwargs.get("description"),
                parameters_schema=kwargs.get("parameters_schema"),
                request_body_template=kwargs.get("request_body_template"),
                response_extract=kwargs.get("response_extract"),
                requires_confirmation=kwargs.get("requires_confirmation", False),
            )
            db.add(action)
            await db.commit()

            return json.dumps(
                {"created": True, "action_id": action.id, "name": action.name},
                ensure_ascii=False,
            )


# ------------------------------------------------------------------
# ConnectorUpdateActionTool
# ------------------------------------------------------------------


class ConnectorUpdateActionTool(_ConnectorBuilderBase):
    """Update an existing action."""

    @property
    def name(self) -> str:
        return "connector_update_action"

    @property
    def display_name(self) -> str:
        return "Update Connector Action"

    @property
    def description(self) -> str:
        return "Update an existing action by action_id. Only provided fields are changed."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "ID of the action to update."},
                "name": {"type": "string", "description": "New action name."},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "description": "New HTTP method.",
                },
                "path": {"type": "string", "description": "New URL path."},
                "description": {"type": "string", "description": "New description."},
                "parameters_schema": {"type": "object", "description": "New parameters schema."},
                "request_body_template": {"type": "object", "description": "New body template."},
                "response_extract": {"type": "string", "description": "New JMESPath expression."},
                "requires_confirmation": {"type": "boolean", "description": "New confirmation flag."},
            },
            "required": ["action_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import ConnectorAction

        action_id = kwargs.pop("action_id")
        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            result = await db.execute(
                select(ConnectorAction).where(
                    ConnectorAction.id == action_id,
                    ConnectorAction.connector_id == self.connector_id,
                )
            )
            action = result.scalar_one_or_none()
            if action is None:
                return f"[Error] Action {action_id} not found."

            _updatable = {
                "name", "method", "path", "description",
                "parameters_schema", "request_body_template",
                "response_extract", "requires_confirmation",
            }
            for field, value in kwargs.items():
                if field in _updatable:
                    setattr(action, field, value)

            await db.commit()
            return json.dumps(
                {"updated": True, "action_id": action_id},
                ensure_ascii=False,
            )


# ------------------------------------------------------------------
# ConnectorDeleteActionTool
# ------------------------------------------------------------------


class ConnectorDeleteActionTool(_ConnectorBuilderBase):
    """Delete an action by ID."""

    @property
    def name(self) -> str:
        return "connector_delete_action"

    @property
    def display_name(self) -> str:
        return "Delete Connector Action"

    @property
    def description(self) -> str:
        return "Delete a connector action by its action_id."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "ID of the action to delete."},
            },
            "required": ["action_id"],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import ConnectorAction

        action_id = kwargs["action_id"]
        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            result = await db.execute(
                select(ConnectorAction).where(
                    ConnectorAction.id == action_id,
                    ConnectorAction.connector_id == self.connector_id,
                )
            )
            action = result.scalar_one_or_none()
            if action is None:
                return f"[Error] Action {action_id} not found."

            await db.delete(action)
            await db.commit()
            return json.dumps(
                {"deleted": True, "action_id": action_id},
                ensure_ascii=False,
            )


# ------------------------------------------------------------------
# ConnectorUpdateSettingsTool
# ------------------------------------------------------------------


class ConnectorUpdateSettingsTool(_ConnectorBuilderBase):
    """Update top-level connector settings."""

    @property
    def name(self) -> str:
        return "connector_update_settings"

    @property
    def display_name(self) -> str:
        return "Update Connector Settings"

    @property
    def description(self) -> str:
        return (
            "Update top-level connector settings such as name, base_url, "
            "auth_type, or auth_config. At least one field must be provided."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "New connector name."},
                "base_url": {"type": "string", "description": "New base URL."},
                "auth_type": {
                    "type": "string",
                    "enum": ["none", "bearer", "api_key", "basic"],
                    "description": "New auth type.",
                },
                "auth_config": {
                    "type": "object",
                    "description": "New auth configuration object.",
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector

        _updatable = {"name", "base_url", "auth_type", "auth_config"}
        updates = {k: v for k, v in kwargs.items() if k in _updatable}
        if not updates:
            return "[Error] At least one of name, base_url, auth_type, or auth_config must be provided."

        async with create_session() as db:
            result = await db.execute(
                select(Connector).where(
                    Connector.id == self.connector_id,
                    Connector.user_id == self.user_id,
                )
            )
            connector = result.scalar_one_or_none()
            if connector is None:
                return "[Error] Connector not found or access denied."

            for field, value in updates.items():
                setattr(connector, field, value)

            await db.commit()
            return json.dumps(
                {"updated": True, "fields": list(updates.keys())},
                ensure_ascii=False,
            )


# ------------------------------------------------------------------
# ConnectorTestActionTool
# ------------------------------------------------------------------


class ConnectorTestActionTool(_ConnectorBuilderBase):
    """Fire a test HTTP request for an action using the connector's auth."""

    @property
    def name(self) -> str:
        return "connector_test_action"

    @property
    def display_name(self) -> str:
        return "Test Connector Action"

    @property
    def description(self) -> str:
        return (
            "Send a test HTTP request for a specific action using the connector's "
            "base_url and auth. Returns status code, headers, and body."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "ID of the action to test."},
                "params": {
                    "type": "object",
                    "description": "Optional path/query parameters as key-value pairs.",
                },
                "body": {
                    "type": "object",
                    "description": "Optional request body.",
                },
            },
            "required": ["action_id"],
        }

    async def run(self, **kwargs: Any) -> str:  # noqa: C901
        import re
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector, ConnectorAction

        action_id = kwargs["action_id"]
        params = kwargs.get("params") or {}
        body = kwargs.get("body")

        async with create_session() as db:
            # Fetch connector (without selectinload — we query action separately)
            result = await db.execute(
                select(Connector).where(
                    Connector.id == self.connector_id,
                    Connector.user_id == self.user_id,
                )
            )
            connector = result.scalar_one_or_none()
            if connector is None:
                return "[Error] Connector not found or access denied."

            result = await db.execute(
                select(ConnectorAction).where(
                    ConnectorAction.id == action_id,
                    ConnectorAction.connector_id == self.connector_id,
                )
            )
            action = result.scalar_one_or_none()
            if action is None:
                return f"[Error] Action {action_id} not found."

            # Build URL: replace path params like {id} with values from params
            path = action.path
            path_param_names = re.findall(r"\{(\w+)\}", path)
            query_params: dict[str, str] = {}
            for key, value in params.items():
                if key in path_param_names:
                    path = path.replace(f"{{{key}}}", str(value))
                else:
                    query_params[key] = str(value)

            base = connector.base_url.rstrip("/")
            url = f"{base}/{path.lstrip('/')}"

            # Build headers with auth
            headers: dict[str, str] = {"User-Agent": "FIM-Agent/1.0 (connector_test)"}
            auth = None
            auth_config = connector.auth_config or {}

            if connector.auth_type == "bearer":
                token = auth_config.get("token", "")
                headers["Authorization"] = f"Bearer {token}"
            elif connector.auth_type == "api_key":
                header_name = auth_config.get("header", "X-API-Key")
                key = auth_config.get("key", "")
                headers[header_name] = key
            elif connector.auth_type == "basic":
                auth = httpx.BasicAuth(
                    username=auth_config.get("username", ""),
                    password=auth_config.get("password", ""),
                )

            # Send request
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.request(
                        method=action.method,
                        url=url,
                        headers=headers,
                        params=query_params or None,
                        json=body if body else None,
                        auth=auth,
                    )
            except httpx.TimeoutException:
                return "[Timeout] Request timed out after 30s."
            except httpx.RequestError as exc:
                return f"[Error] {exc}"

            # Format response
            resp_body = resp.text[:10_000]  # cap at 10 KB for LLM context
            try:
                parsed = json.loads(resp_body)
                resp_body = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass

            return json.dumps(
                {
                    "status_code": resp.status_code,
                    "url": str(resp.url),
                    "method": action.method,
                    "body": resp_body,
                },
                ensure_ascii=False,
                indent=2,
            )
