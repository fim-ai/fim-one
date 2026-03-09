"""Adapter that converts ConnectorActions into FIM Agent tools."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from fim_agent.core.security import get_safe_async_client, validate_url
from fim_agent.core.tool.base import BaseTool
from fim_agent.core.tool.truncation import truncate_tool_output

logger = logging.getLogger(__name__)

# Response truncation limits (ENV-configurable)
CONNECTOR_RESPONSE_MAX_CHARS = int(
    os.environ.get("CONNECTOR_RESPONSE_MAX_CHARS", "50000")
)
CONNECTOR_RESPONSE_MAX_ITEMS = int(
    os.environ.get("CONNECTOR_RESPONSE_MAX_ITEMS", "10")
)


class ConnectorToolAdapter(BaseTool):
    """Wraps a single ConnectorAction as a BaseTool.

    Tool names use format: ``{connector_name}__{action_name}``
    Category: ``"connector"``
    """

    def __init__(
        self,
        connector_name: str,
        connector_base_url: str,
        connector_auth_type: str,
        connector_auth_config: dict[str, Any] | None,
        action_name: str,
        action_description: str,
        action_method: str,
        action_path: str,
        action_parameters_schema: dict[str, Any] | None,
        action_request_body_template: dict[str, Any] | None,
        action_response_extract: str | None,
        action_requires_confirmation: bool,
        auth_credentials: dict[str, str] | None = None,
        connector_id: str | None = None,
        action_id: str | None = None,
        on_call_complete: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        safe_connector = re.sub(r"[^a-zA-Z0-9]", "_", connector_name.lower()).strip("_")
        safe_action = re.sub(r"[^a-zA-Z0-9]", "_", action_name.lower()).strip("_")
        self._name = f"{safe_connector}__{safe_action}"
        self._description = action_description or f"{action_method} {action_path}"
        self._method = action_method.upper()
        self._base_url = connector_base_url.rstrip("/")

        try:
            validate_url(connector_base_url)
        except ValueError as exc:
            raise ValueError(f"Connector base URL blocked by SSRF policy: {exc}") from exc

        self._path = action_path
        self._parameters_schema_val = action_parameters_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._request_body_template = action_request_body_template
        self._response_extract = action_response_extract
        self._requires_confirmation = action_requires_confirmation
        self._auth_type = connector_auth_type
        self._auth_config = connector_auth_config or {}
        self._auth_credentials = auth_credentials or {}
        self._connector_id = connector_id
        self._action_id = action_id
        self._connector_name_raw = connector_name
        self._action_name_raw = action_name
        self._on_call_complete = on_call_complete

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        connector = self._connector_name_raw.replace("_", " ").title()
        action = self._action_name_raw.replace("_", " ").title()
        return f"{connector}: {action}"

    @property
    def description(self) -> str:
        return self._description

    @property
    def category(self) -> str:
        return "connector"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._parameters_schema_val

    async def run(self, **kwargs: Any) -> str:
        """Execute the HTTP request to the target system."""
        # 1. Build URL with path parameters
        path = self._path
        path_params = re.findall(r"\{(\w+)\}", path)
        for param in path_params:
            if param in kwargs:
                value = str(kwargs.pop(param))
                if "/" in value or "\\" in value or ".." in value:
                    raise ValueError(
                        f"Path parameter '{param}' contains invalid characters."
                    )
                path = path.replace(f"{{{param}}}", value)

        url = f"{self._base_url}{path}"

        try:
            validate_url(url)
        except ValueError as exc:
            return f"[Error] SSRF blocked: {exc}"

        # 2. Build headers with auth
        headers: dict[str, str] = {"Accept": "application/json"}
        self._inject_auth(headers)

        # 3. Build request
        query_params: dict[str, Any] = {}
        body: Any = None

        if self._method in ("GET", "DELETE"):
            query_params = {k: v for k, v in kwargs.items() if v is not None}
        else:
            if self._request_body_template:
                body = self._render_template(self._request_body_template, kwargs)
            else:
                body = kwargs if kwargs else None
            headers["Content-Type"] = "application/json"

        # 4. Execute request with call logging
        start_ms = time.monotonic_ns() // 1_000_000
        response_status: int | None = None
        success = False
        error_message: str | None = None
        result = ""

        try:
            async with get_safe_async_client(timeout=30) as client:
                resp = await client.request(
                    method=self._method,
                    url=url,
                    headers=headers,
                    params=query_params if query_params else None,
                    json=body,
                )

                response_status = resp.status_code
                content = resp.text
                if resp.status_code >= 400:
                    error_message = content[:500]
                    result = f"[HTTP {resp.status_code}] {content[:2000]}"
                    return result

                success = True

                # Apply response extract (jmespath) if configured
                if (
                    self._response_extract
                    and resp.headers.get("content-type", "").startswith("application/json")
                ):
                    try:
                        import jmespath  # type: ignore[import-untyped]

                        data = resp.json()
                        extracted = jmespath.search(self._response_extract, data)
                        if extracted is not None:
                            if isinstance(extracted, str):
                                result = extracted
                                return result
                            result = json.dumps(extracted, ensure_ascii=False, indent=2)
                            return result
                    except ImportError:
                        logger.debug(
                            "jmespath not installed — skipping response_extract"
                        )
                    except Exception:
                        pass  # Fall through to raw response

                # Smart truncation for long responses
                content = truncate_tool_output(
                    content,
                    max_chars=CONNECTOR_RESPONSE_MAX_CHARS,
                    max_items=CONNECTOR_RESPONSE_MAX_ITEMS,
                )
                result = content
                return result

        except httpx.TimeoutException:
            error_message = "Request exceeded 30 seconds"
            result = f"[Timeout] {error_message}."
            return result
        except httpx.RequestError as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            result = f"[Error] {error_message}"
            return result
        finally:
            elapsed_ms = time.monotonic_ns() // 1_000_000 - start_ms
            if self._on_call_complete:
                try:
                    await self._on_call_complete(
                        connector_id=self._connector_id,
                        connector_name=self._connector_name_raw,
                        action_id=self._action_id,
                        action_name=self._description,
                        request_method=self._method,
                        request_url=url,
                        response_status=response_status,
                        response_time_ms=elapsed_ms,
                        success=success,
                        error_message=error_message,
                    )
                except Exception:
                    logger.debug("on_call_complete callback failed", exc_info=True)

    def _inject_auth(self, headers: dict[str, str]) -> None:
        """Inject authentication into request headers.

        Priority: per-user credentials > default credentials in auth_config.
        """
        creds = self._auth_credentials
        cfg = self._auth_config

        if self._auth_type == "bearer":
            token = creds.get("token", "") or cfg.get("default_token", "")
            if token:
                prefix = cfg.get("token_prefix", "Bearer")
                headers["Authorization"] = f"{prefix} {token}"
        elif self._auth_type == "api_key":
            header_name = cfg.get("header_name", "X-API-Key")
            key = creds.get("api_key", "") or cfg.get("default_api_key", "")
            if key:
                headers[header_name] = key
        elif self._auth_type == "basic":
            username = creds.get("username", "") or cfg.get("default_username", "")
            password = creds.get("password", "") or cfg.get("default_password", "")
            if username or password:
                encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"

    @staticmethod
    def _render_template(template: dict, params: dict) -> dict:
        """Replace ``{{param}}`` placeholders in body template with actual values."""
        raw = json.dumps(template)
        for key, value in params.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(value, str):
                raw = raw.replace(f'"{placeholder}"', json.dumps(value))
                escaped = json.dumps(value)[1:-1]  # JSON-escaped interior without surrounding quotes
                raw = raw.replace(placeholder, escaped)
            else:
                raw = raw.replace(f'"{placeholder}"', json.dumps(value))
        return json.loads(raw)
