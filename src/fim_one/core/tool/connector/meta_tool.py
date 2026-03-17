"""ConnectorMetaTool — single tool proxy for progressive connector disclosure.

Instead of registering every connector action as a separate tool (N connectors
x M actions = N*M tool definitions in the system prompt), the ConnectorMetaTool
presents a compact stub listing (~30 tokens per connector) and exposes two
subcommands:

    discover <connector>          — returns full action schemas on demand
    execute <connector> <action>  — runs an action through the existing adapter

This reduces prompt size dramatically while keeping full functionality.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fim_one.core.tool.base import BaseTool
from fim_one.core.tool.connector.adapter import ConnectorToolAdapter

logger = logging.getLogger(__name__)

_DISCOVER_INDENT = int(os.getenv("CONNECTOR_DISCOVER_INDENT", "2"))


@dataclass(frozen=True)
class ActionStub:
    """Lightweight action metadata stored for discover/execute routing."""

    name: str
    description: str
    method: str
    path: str
    parameters_schema: dict[str, Any] | None
    request_body_template: dict[str, Any] | None
    response_extract: str | None
    requires_confirmation: bool
    action_id: str | None = None


@dataclass(frozen=True)
class ConnectorStub:
    """Lightweight connector summary for the system prompt."""

    name: str
    description: str
    action_count: int
    actions: list[ActionStub] = field(default_factory=list)


class ConnectorMetaTool(BaseTool):
    """A single tool that proxies all connector operations.

    System prompt sees only lightweight stubs::

        connector("discover", "salesforce")
        connector("execute", "salesforce", "get_contacts", {"limit": 10})

    Subcommands:
        discover <connector_name> — returns full action schemas
        execute <connector_name> <action_name> <params_json> — executes
    """

    def __init__(
        self,
        stubs: list[ConnectorStub],
        *,
        # Shared auth/connection info keyed by connector name
        connector_configs: dict[str, dict[str, Any]] | None = None,
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._stubs: dict[str, ConnectorStub] = {s.name: s for s in stubs}
        self._connector_configs = connector_configs or {}
        self._on_call_complete = on_call_complete

    # ------------------------------------------------------------------
    # BaseTool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "connector"

    @property
    def display_name(self) -> str:
        return "Connector"

    @property
    def description(self) -> str:
        lines = ["Interact with external services. Available connectors:"]
        for stub in self._stubs.values():
            desc = stub.description or stub.name
            lines.append(f"  - {stub.name}: {desc} ({stub.action_count} actions)")
        lines.append("")
        lines.append("Subcommands:")
        lines.append(
            "  discover <name> — list actions with full parameter schemas"
        )
        lines.append(
            '  execute <name> <action> {"param": "value"} — run an action'
        )
        return "\n".join(lines)

    @property
    def category(self) -> str:
        return "connector"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        # Filter out empty names (e.g. pure non-ASCII connector names that
        # sanitize to "").  Gemini rejects empty enum arrays and empty strings
        # inside enum arrays.
        connector_names = sorted(n for n in self._stubs.keys() if n)
        connector_prop: dict[str, Any] = {
            "type": "string",
            "description": "Connector name",
        }
        if connector_names:
            connector_prop["enum"] = connector_names
        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": ["discover", "execute"],
                    "description": (
                        "discover: list actions for a connector. "
                        "execute: run an action."
                    ),
                },
                "connector": connector_prop,
                "action": {
                    "type": "string",
                    "description": "Action name (required for execute)",
                },
                "parameters": {
                    "type": "object",
                    "description": "Action parameters as JSON (required for execute)",
                },
            },
            "required": ["subcommand", "connector"],
        }

    async def run(self, **kwargs: Any) -> str:
        """Route to discover or execute subcommand."""
        subcommand = kwargs.get("subcommand", "")
        connector = kwargs.get("connector", "")
        action = kwargs.get("action", "")
        parameters = kwargs.get("parameters") or {}

        if not subcommand:
            return "Error: 'subcommand' is required. Use 'discover' or 'execute'."
        if not connector:
            return "Error: 'connector' is required."

        if subcommand == "discover":
            return self._discover(connector)
        elif subcommand == "execute":
            return await self._execute(connector, action, parameters)
        else:
            return (
                f"Unknown subcommand: '{subcommand}'. "
                "Use 'discover' or 'execute'."
            )

    # ------------------------------------------------------------------
    # Subcommand implementations
    # ------------------------------------------------------------------

    def _discover(self, connector_name: str) -> str:
        """Return formatted action list with full parameter schemas."""
        stub = self._stubs.get(connector_name)
        if stub is None:
            available = ", ".join(sorted(self._stubs.keys()))
            return (
                f"Unknown connector: '{connector_name}'. "
                f"Available connectors: {available}"
            )

        if not stub.actions:
            return f"Connector '{connector_name}' has no actions configured."

        lines = [
            f"Connector: {stub.name}",
            f"Description: {stub.description}",
            f"Actions ({len(stub.actions)}):",
            "",
        ]

        for action in stub.actions:
            lines.append(f"  {action.name}:")
            lines.append(f"    method: {action.method}")
            lines.append(f"    path: {action.path}")
            if action.description:
                lines.append(f"    description: {action.description}")
            if action.requires_confirmation:
                lines.append("    requires_confirmation: true")
            if action.parameters_schema:
                schema_str = json.dumps(
                    action.parameters_schema, ensure_ascii=False, indent=_DISCOVER_INDENT
                )
                lines.append(f"    parameters: {schema_str}")
            else:
                lines.append("    parameters: (none)")
            lines.append("")

        return "\n".join(lines)

    async def _execute(
        self,
        connector_name: str,
        action_name: str,
        parameters: dict[str, Any],
    ) -> str:
        """Execute an action through a temporary ConnectorToolAdapter."""
        stub = self._stubs.get(connector_name)
        if stub is None:
            available = ", ".join(sorted(self._stubs.keys()))
            return (
                f"Unknown connector: '{connector_name}'. "
                f"Available connectors: {available}"
            )

        if not action_name:
            action_names = [a.name for a in stub.actions]
            return (
                f"Error: 'action' is required for execute. "
                f"Available actions for '{connector_name}': {', '.join(action_names)}"
            )

        # Find the action
        action_stub = None
        for a in stub.actions:
            if a.name == action_name:
                action_stub = a
                break

        if action_stub is None:
            action_names = [a.name for a in stub.actions]
            return (
                f"Unknown action: '{action_name}' for connector '{connector_name}'. "
                f"Available actions: {', '.join(action_names)}"
            )

        # Get connector config (auth, base_url, etc.)
        config = self._connector_configs.get(connector_name, {})
        if not config:
            return (
                f"Error: No configuration found for connector '{connector_name}'. "
                "The connector may not be properly set up."
            )

        # Build a temporary ConnectorToolAdapter and delegate execution
        try:
            adapter = ConnectorToolAdapter(
                connector_name=connector_name,
                connector_base_url=config.get("base_url", ""),
                connector_auth_type=config.get("auth_type", "none"),
                connector_auth_config=config.get("auth_config"),
                auth_credentials=config.get("auth_credentials"),
                action_name=action_stub.name,
                action_description=action_stub.description,
                action_method=action_stub.method,
                action_path=action_stub.path,
                action_parameters_schema=action_stub.parameters_schema,
                action_request_body_template=action_stub.request_body_template,
                action_response_extract=action_stub.response_extract,
                action_requires_confirmation=action_stub.requires_confirmation,
                connector_id=config.get("connector_id"),
                action_id=action_stub.action_id,
                on_call_complete=self._on_call_complete,
            )
            return await adapter.run(**parameters)
        except ValueError as exc:
            return f"[Error] Configuration error: {exc}"
        except Exception as exc:
            logger.warning(
                "ConnectorMetaTool execute failed: connector=%s action=%s",
                connector_name,
                action_name,
                exc_info=True,
            )
            return f"[Error] Execution failed: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def connector_names(self) -> list[str]:
        """Return sorted list of available connector names."""
        return sorted(self._stubs.keys())

    @property
    def stub_count(self) -> int:
        """Return number of registered connector stubs."""
        return len(self._stubs)


# ---------------------------------------------------------------------------
# Factory helper — builds a ConnectorMetaTool from ORM connector objects
# ---------------------------------------------------------------------------


def build_connector_meta_tool(
    connectors: list[Any],
    *,
    resolved_credentials: dict[str, dict[str, Any]] | None = None,
    on_call_complete: Callable[..., Awaitable[None]] | None = None,
) -> ConnectorMetaTool:
    """Build a ConnectorMetaTool from a list of ORM Connector objects.

    This is the primary integration point called from ``chat.py`` when
    ``CONNECTOR_TOOL_MODE=progressive``.

    Args:
        connectors: List of ORM Connector objects (with ``.actions`` loaded).
        resolved_credentials: Mapping of connector_id → decrypted credentials dict.
        on_call_complete: Optional async callback for call logging.

    Returns:
        A fully configured ConnectorMetaTool instance.
    """
    resolved_credentials = resolved_credentials or {}
    stubs: list[ConnectorStub] = []
    configs: dict[str, dict[str, Any]] = {}

    for conn in connectors:
        # Sanitize connector name to match ConnectorToolAdapter convention.
        # Fall back to connector ID prefix if the name is pure non-ASCII.
        safe_name = re.sub(r"[^a-zA-Z0-9]", "_", conn.name.lower()).strip("_")
        if not safe_name:
            safe_name = f"connector_{getattr(conn, 'id', '')[:8] or len(stubs)}"

        actions: list[ActionStub] = []
        for action in (conn.actions or []):
            safe_action_name = re.sub(
                r"[^a-zA-Z0-9]", "_", action.name.lower()
            ).strip("_")
            actions.append(
                ActionStub(
                    name=safe_action_name,
                    description=action.description or "",
                    method=action.method,
                    path=action.path,
                    parameters_schema=action.parameters_schema,
                    request_body_template=action.request_body_template,
                    response_extract=getattr(action, "response_extract", None),
                    requires_confirmation=action.requires_confirmation,
                    action_id=getattr(action, "id", None),
                )
            )

        stub = ConnectorStub(
            name=safe_name,
            description=conn.description or conn.name,
            action_count=len(actions),
            actions=actions,
        )
        stubs.append(stub)

        # Store connector config for execute-time adapter creation
        creds = resolved_credentials.get(conn.id, {})
        configs[safe_name] = {
            "base_url": conn.base_url or "",
            "auth_type": conn.auth_type,
            "auth_config": conn.auth_config,
            "auth_credentials": creds or None,
            "connector_id": conn.id,
        }

    return ConnectorMetaTool(
        stubs=stubs,
        connector_configs=configs,
        on_call_complete=on_call_complete,
    )


def get_connector_tool_mode(agent_cfg: dict[str, Any] | None = None) -> str:
    """Determine the connector tool mode from environment or agent config.

    Priority:
        1. Agent-level ``model_config_json.connector_tool_mode``
        2. Environment variable ``CONNECTOR_TOOL_MODE``
        3. Default: ``"progressive"``

    Returns:
        ``"progressive"`` or ``"legacy"``
    """
    import os

    # Check agent-level config first
    if agent_cfg:
        model_cfg = agent_cfg.get("model_config_json") or {}
        if isinstance(model_cfg, dict):
            agent_mode = model_cfg.get("connector_tool_mode")
            if agent_mode in ("progressive", "legacy"):
                return agent_mode

    # Fall back to environment variable
    env_mode = os.environ.get("CONNECTOR_TOOL_MODE", "progressive").lower()
    if env_mode in ("progressive", "legacy"):
        return env_mode

    return "progressive"
