"""Builder tools for managing Connector actions via LLM agent.

Tools in this module are injected exclusively for Builder Agents (is_builder=True)
that have "builder" in their tool_categories.  They are excluded from
auto-discovery to prevent regular agents from accessing them.
"""

from __future__ import annotations

import json
import logging
from abc import ABC
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from fim_agent.core.security import get_safe_async_client

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

            # SSRF guard
            from urllib.parse import urlparse as _urlparse
            if _urlparse(url).scheme not in {"http", "https"}:
                return "[Error] Unsafe URL scheme. Only http/https are allowed."

            # Send request
            try:
                async with get_safe_async_client(timeout=30, follow_redirects=True) as client:
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


# ------------------------------------------------------------------
# ConnectorGetSettingsTool
# ------------------------------------------------------------------


class ConnectorGetSettingsTool(_ConnectorBuilderBase):
    """Get the full settings of the connector (without actions)."""

    @property
    def name(self) -> str:
        return "connector_get_settings"

    @property
    def display_name(self) -> str:
        return "Get Connector Settings"

    @property
    def description(self) -> str:
        return (
            "Get the current settings of this connector: name, description, icon, "
            "base_url, auth_type, and auth_config. Use this before updating settings "
            "to see what is already configured."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector

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

            # Mask sensitive values to avoid leaking credentials into LLM context
            safe_auth = {}
            if connector.auth_config:
                safe_auth = {
                    k: ("****" + v[-4:] if isinstance(v, str) and len(v) > 4 else "****")
                    for k, v in connector.auth_config.items()
                }

            return json.dumps(
                {
                    "id": connector.id,
                    "name": connector.name,
                    "description": connector.description,
                    "icon": connector.icon,
                    "base_url": connector.base_url,
                    "auth_type": connector.auth_type,
                    "auth_config": safe_auth,
                    "status": connector.status,
                },
                ensure_ascii=False,
                indent=2,
            )


# ------------------------------------------------------------------
# ConnectorTestConnectionTool
# ------------------------------------------------------------------


class ConnectorTestConnectionTool(_ConnectorBuilderBase):
    """Test base URL reachability with the connector's auth credentials."""

    @property
    def name(self) -> str:
        return "connector_test_connection"

    @property
    def display_name(self) -> str:
        return "Test Connector Connection"

    @property
    def description(self) -> str:
        return (
            "Send a GET request to the connector's base_url to verify it is reachable "
            "and the configured auth credentials are accepted. "
            "Returns HTTP status, latency (ms), and a short response snippet."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional sub-path to probe, e.g. '/health' or '/api/v1/me'. "
                        "Defaults to '/' (root)."
                    ),
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:
        import time
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import Connector

        probe_path = (kwargs.get("path") or "/").lstrip("/")

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

        base = connector.base_url.rstrip("/")
        url = f"{base}/{probe_path}" if probe_path else base

        # SSRF guard — only http/https
        from urllib.parse import urlparse
        _p = urlparse(url)
        if _p.scheme not in {"http", "https"}:
            return f"[Error] Unsafe URL scheme '{_p.scheme}'."

        headers: dict[str, str] = {"User-Agent": "FIM-Agent/1.0 (connection_test)"}
        auth = None
        auth_config = connector.auth_config or {}

        if connector.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_config.get('token', '')}"
        elif connector.auth_type == "api_key":
            headers[auth_config.get("header", "X-API-Key")] = auth_config.get("key", "")
        elif connector.auth_type == "basic":
            auth = httpx.BasicAuth(
                username=auth_config.get("username", ""),
                password=auth_config.get("password", ""),
            )

        t0 = time.monotonic()
        try:
            async with get_safe_async_client(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers, auth=auth)
            latency_ms = round((time.monotonic() - t0) * 1000)
        except httpx.TimeoutException:
            return json.dumps({"ok": False, "error": "Timeout after 10s", "url": url}, ensure_ascii=False)
        except httpx.RequestError as exc:
            return json.dumps({"ok": False, "error": str(exc), "url": url}, ensure_ascii=False)

        snippet = resp.text[:500]
        return json.dumps(
            {
                "ok": resp.status_code < 400,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "url": str(resp.url),
                "snippet": snippet,
            },
            ensure_ascii=False,
            indent=2,
        )


# ------------------------------------------------------------------
# ConnectorImportOpenAPITool
# ------------------------------------------------------------------

_MAX_IMPORT_ACTIONS = 50


def _extract_param_schema(parameters: list[dict]) -> dict[str, Any]:
    """Convert OpenAPI parameter list to a simple flat schema dict."""
    props: dict[str, Any] = {}
    for p in parameters:
        if p.get("in") in ("path", "query"):
            schema = p.get("schema") or {}
            props[p["name"]] = {
                "type": schema.get("type", "string"),
                "description": p.get("description") or schema.get("description") or "",
                "required": p.get("required", p.get("in") == "path"),
            }
    return props


def _parse_openapi_spec(spec: dict) -> list[dict]:
    """Parse an OpenAPI 2.x or 3.x spec into a list of action dicts."""
    actions: list[dict] = []
    paths = spec.get("paths") or {}
    is_v2 = "swagger" in spec  # Swagger 2.x has 'swagger' key; OpenAPI 3.x has 'openapi'

    _http_methods = {"get", "post", "put", "patch", "delete"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        # Path-level parameters (inherited by all operations)
        path_params = path_item.get("parameters") or []

        for method, operation in path_item.items():
            if method not in _http_methods:
                continue
            if not isinstance(operation, dict):
                continue

            # Merge path-level params with operation-level params (operation wins)
            op_params = operation.get("parameters") or []
            all_params = {p["name"]: p for p in path_params if isinstance(p, dict) and "name" in p}
            all_params.update({p["name"]: p for p in op_params if isinstance(p, dict) and "name" in p})
            non_body_params = [p for p in all_params.values() if p.get("in") != "body"]
            param_schema = _extract_param_schema(non_body_params) or None

            # Derive action name: prefer operationId, then slug from method+path
            op_id: str = operation.get("operationId") or ""
            if not op_id:
                slug = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
                op_id = f"{method}_{slug}" if slug else method

            # Name must be a valid identifier
            import re as _re
            op_id = _re.sub(r"[^a-zA-Z0-9_]", "_", op_id)[:80]

            summary = operation.get("summary") or operation.get("description") or ""
            summary = summary[:200] if summary else None

            # Request body (v3: requestBody; v2: parameters[in=body])
            body_template: dict | None = None
            if is_v2:
                body_param = next(
                    (p for p in all_params.values() if p.get("in") == "body"), None
                )
                if body_param:
                    body_schema = (body_param.get("schema") or {}).get("properties") or {}
                    if body_schema:
                        body_template = {k: f"<{k}>" for k in body_schema}
            else:
                req_body = operation.get("requestBody") or {}
                content = req_body.get("content") or {}
                json_content = content.get("application/json") or {}
                body_schema_props = (json_content.get("schema") or {}).get("properties") or {}
                if body_schema_props:
                    body_template = {k: f"<{k}>" for k in body_schema_props}

            actions.append(
                {
                    "name": op_id,
                    "method": method.upper(),
                    "path": path,
                    "description": summary,
                    "parameters_schema": param_schema,
                    "request_body_template": body_template,
                }
            )

    return actions


class ConnectorImportOpenAPITool(_ConnectorBuilderBase):
    """Import actions from an OpenAPI 2.x or 3.x specification."""

    @property
    def name(self) -> str:
        return "connector_import_openapi"

    @property
    def display_name(self) -> str:
        return "Import OpenAPI Spec"

    @property
    def description(self) -> str:
        return (
            "Fetch an OpenAPI (Swagger 2.x or OpenAPI 3.x) specification from a URL or "
            "accept it as a JSON object, then batch-create actions for each endpoint. "
            f"Maximum {_MAX_IMPORT_ACTIONS} actions imported per call. "
            "Use dry_run=true to preview without creating."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch the OpenAPI spec JSON from (e.g. https://api.example.com/openapi.json).",
                },
                "spec": {
                    "type": "object",
                    "description": "Raw OpenAPI spec as a JSON object (alternative to url).",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, return the list of actions that would be created without actually creating them.",
                },
                "method_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only import operations with these HTTP methods, e.g. ['GET', 'POST']. Defaults to all methods.",
                },
            },
            "required": [],
        }

    async def run(self, **kwargs: Any) -> str:  # noqa: C901
        from fim_agent.db import create_session
        from fim_agent.web.models.connector import ConnectorAction

        url: str | None = kwargs.get("url")
        spec: dict | None = kwargs.get("spec")
        dry_run: bool = kwargs.get("dry_run", False)
        method_filter: list[str] | None = kwargs.get("method_filter")

        if not url and not spec:
            return "[Error] Either 'url' or 'spec' must be provided."

        # Fetch spec from URL
        if url and not spec:
            from urllib.parse import urlparse
            _p = urlparse(url)
            if _p.scheme not in {"http", "https"}:
                return f"[Error] Unsafe URL scheme '{_p.scheme}'."
            try:
                async with get_safe_async_client(timeout=20, follow_redirects=True) as client:
                    resp = await client.get(url, headers={"Accept": "application/json"})
                    resp.raise_for_status()
                    spec = resp.json()
            except httpx.TimeoutException:
                return "[Error] Timeout fetching OpenAPI spec."
            except Exception as exc:
                return f"[Error] Failed to fetch spec: {exc}"

        if not isinstance(spec, dict):
            return "[Error] Spec must be a JSON object."

        # Parse spec into action candidates
        try:
            candidates = _parse_openapi_spec(spec)
        except Exception as exc:
            return f"[Error] Failed to parse OpenAPI spec: {exc}"

        # Apply method filter
        if method_filter:
            _methods = {m.upper() for m in method_filter}
            candidates = [c for c in candidates if c["method"] in _methods]

        # Enforce limit
        truncated = len(candidates) > _MAX_IMPORT_ACTIONS
        candidates = candidates[:_MAX_IMPORT_ACTIONS]

        if dry_run:
            return json.dumps(
                {
                    "dry_run": True,
                    "would_create": len(candidates),
                    "truncated": truncated,
                    "actions": candidates,
                },
                ensure_ascii=False,
                indent=2,
            )

        # Verify connector ownership
        async with create_session() as db:
            connector = await self._get_connector(db)
            if connector is None:
                return "[Error] Connector not found or access denied."

            created_names: list[str] = []
            for c in candidates:
                action = ConnectorAction(
                    connector_id=self.connector_id,
                    name=c["name"],
                    method=c["method"],
                    path=c["path"],
                    description=c.get("description"),
                    parameters_schema=c.get("parameters_schema"),
                    request_body_template=c.get("request_body_template"),
                )
                db.add(action)
                created_names.append(c["name"])

            await db.commit()

        return json.dumps(
            {
                "imported": len(created_names),
                "truncated": truncated,
                "actions": created_names,
            },
            ensure_ascii=False,
            indent=2,
        )
