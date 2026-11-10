"""Adapter that wraps an MCP tool definition into the FIM One Tool protocol."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fim_one.core.tool.base import BaseTool
from fim_one.core.tool.truncation import truncate_tool_output

logger = logging.getLogger(__name__)

# How much of a failed call's output is kept in its record.
_ERROR_MESSAGE_MAX_CHARS = 500


def _sanitize_tool_name(name: str) -> str:
    """Replace characters invalid for Bedrock/OpenAI tool names with underscores.

    Allowed pattern: ``^[a-zA-Z0-9_-]{1,128}``
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:128]


def server_tool_prefix(server_name: str) -> str:
    """The part of a tool name that stands for an MCP server called *server_name*.

    A tool is named ``_sanitize_tool_name(f"{server_name}__{tool}")``, which
    maps character by character, so this is the server's share of it. Two
    servers with the same prefix offer their tools under the same names.
    """
    return _sanitize_tool_name(server_name)


class MCPToolAdapter(BaseTool):
    """Wraps a single MCP tool into the FIM One ``Tool`` protocol.

    Tool names are prefixed with the server name to avoid collisions when
    multiple MCP servers expose tools with the same name:
    ``"{server_name}__{tool_name}"``.

    Parameters
    ----------
    server_name:
        Unique identifier of the MCP server this tool belongs to.
    tool_def:
        Dictionary with ``name``, ``description``, and ``inputSchema`` keys
        as returned by the MCP ``tools/list`` endpoint.
    call_fn:
        An async callable that sends ``tools/call`` to the MCP server.
        Signature: ``(tool_name: str, arguments: dict) -> CallToolResult``.
    """

    def __init__(
        self,
        server_name: str,
        tool_def: dict[str, Any],
        call_fn: Callable[..., Coroutine[Any, Any, Any]],
    ) -> None:
        self._server_name = server_name  # original, for display_name
        self._name = _sanitize_tool_name(f"{server_name}__{tool_def['name']}")
        self._description = tool_def.get("description", "")
        self._schema: dict[str, Any] = tool_def.get(
            "inputSchema", {"type": "object", "properties": {}}
        )
        self._original_name: str = tool_def["name"]
        self._call_fn = call_fn
        self._on_call_complete: Callable[..., Awaitable[None]] | None = None
        self._server_id: str | None = None

    def record_calls(
        self,
        on_call_complete: Callable[..., Awaitable[None]],
        *,
        server_id: str | None,
    ) -> None:
        """Report every call this tool makes to *on_call_complete*.

        Set by whoever connected the server for a user's run. The adapter is
        the one place a call passes through whichever way the tool is
        offered (on its own, behind the ``mcp`` meta-tool, from a workflow
        step), so the record is taken here.
        """
        self._on_call_complete = on_call_complete
        self._server_id = server_id

    # ------------------------------------------------------------------
    # Tool protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        action = self._original_name.replace("_", " ").title()
        return f"{self._server_name}: {action}"

    @property
    def original_name(self) -> str:
        """The tool's name on its server, as ``tools/list`` gave it."""
        return self._original_name

    @property
    def category(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return self._description or ""

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def run(self, **kwargs: Any) -> str:
        """Call the MCP server's ``tools/call`` endpoint and extract text."""
        started = time.monotonic_ns()
        success = False
        error_message: str | None = None
        try:
            result = await self._call_fn(self._original_name, kwargs)
            output = self._extract_text(result)
            success = not _result_is_error(result)
            if not success:
                error_message = output[:_ERROR_MESSAGE_MAX_CHARS]
            return truncate_tool_output(output)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"[:_ERROR_MESSAGE_MAX_CHARS]
            raise
        finally:
            if self._on_call_complete is not None:
                try:
                    # Named like a database call (``db://name/action``). The
                    # server's real URL is not recorded: it can carry a secret.
                    await self._on_call_complete(
                        mcp_server_id=self._server_id,
                        connector_name=self._server_name,
                        action_name=self._original_name,
                        request_method="MCP",
                        request_url=f"mcp://{self._server_name}/{self._original_name}",
                        response_time_ms=(time.monotonic_ns() - started) // 1_000_000,
                        success=success,
                        error_message=error_message,
                    )
                except Exception:
                    logger.debug("on_call_complete callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Extract a plain-text string from an MCP ``CallToolResult``.

        The result object exposes:
        - ``content``: a list of content items (each with ``.type`` and
          ``.text`` for text items, or ``.data`` for binary items).
        - ``is_error`` (mcp 2.x) or ``isError`` (mcp 1.x): whether the
          call failed.
        """
        parts: list[str] = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif hasattr(item, "data"):
                parts.append(f"[{item.type}: {len(item.data)} bytes]")
        output = "\n".join(parts)
        if _result_is_error(result):
            output = f"[MCP Error] {output}"
        return output

    def __repr__(self) -> str:
        return f"MCPToolAdapter(name={self._name!r}, original={self._original_name!r})"


def _result_is_error(result: Any) -> bool:
    """Read the MCP tool-error flag across sdk 1.x and 2.x spellings."""
    if hasattr(result, "is_error"):
        return bool(result.is_error)
    return bool(getattr(result, "isError", False))
