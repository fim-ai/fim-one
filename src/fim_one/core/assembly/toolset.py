"""The tools a call gets for the resources it may use.

These functions are where connector and MCP tools are built for a user's
run: each resolves the acting user's credential (:mod:`.credentials`),
leaves out a resource the user may not call, and wires the call record
(:mod:`.calls`). The caller has already decided which resources are usable
(:func:`fim_one.core.assembly.visibility.authorize_use`).
:func:`kb_binding` answers which knowledge bases a call may read.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from .calls import call_recorder
from .credentials import resolve_credential
from .errors import AssemblyRefusal
from .visibility import ResourceKind, authorize_use

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_one.core.mcp import MCPClient
    from fim_one.core.tool.base import BaseTool

    from .credentials import ResolvedCredential
    from .principal import CallContext

__all__ = [
    "ToolMode",
    "ToolsetError",
    "connector_tools",
    "database_tools",
    "kb_binding",
    "mcp_tools",
]

logger = logging.getLogger(__name__)

# ``progressive``: one meta-tool for all resources of a kind, with
# discover / execute subcommands. ``legacy``: one tool per action.
ToolMode = Literal["progressive", "legacy"]


async def connector_tools(
    db: AsyncSession,
    ctx: CallContext,
    connectors: Sequence[Any],
    *,
    mode: ToolMode,
) -> list[BaseTool]:
    """Tools for the API connectors in *connectors* (rows with ``actions`` loaded).

    A connector the acting user has no credential for is left out.
    """
    from fim_one.core.tool.connector import ConnectorToolAdapter, build_connector_meta_tool

    record = call_recorder(ctx, kind="connector")
    callable_connectors: list[tuple[Any, dict[str, Any]]] = []
    for conn in connectors:
        try:
            credential = await resolve_credential(db, ctx.principal, conn)
        except AssemblyRefusal as refusal:
            logger.info("Skipping connector %r: %s", conn.name, refusal.error_code)
            continue
        callable_connectors.append((conn, credential.connector or {}))

    if mode == "progressive":
        if not callable_connectors:
            return []
        logger.info(
            "Loaded ConnectorMetaTool (progressive): %d connectors, "
            "%d actions consolidated into 1 tool",
            len(callable_connectors),
            sum(len(conn.actions or []) for conn, _ in callable_connectors),
        )
        return [
            build_connector_meta_tool(
                [conn for conn, _ in callable_connectors],
                resolved_credentials={conn.id: creds for conn, creds in callable_connectors},
                on_call_complete=record,
            )
        ]

    tools: list[BaseTool] = []
    for conn, creds in callable_connectors:
        for action in conn.actions or []:
            tools.append(
                ConnectorToolAdapter(
                    connector_name=conn.name,
                    connector_base_url=conn.base_url or "",
                    connector_auth_type=conn.auth_type,
                    connector_auth_config=conn.auth_config,
                    auth_credentials=creds or None,
                    action_name=action.name,
                    action_description=action.description or "",
                    action_method=action.method,
                    action_path=action.path,
                    action_parameters_schema=action.parameters_schema,
                    action_request_body_template=action.request_body_template,
                    action_response_extract=action.response_extract,
                    action_requires_confirmation=action.requires_confirmation,
                    connector_id=conn.id,
                    action_id=action.id,
                    on_call_complete=record,
                )
            )
    logger.info("Loaded %d API tools from %d connectors", len(tools), len(callable_connectors))
    return tools


def _visible_schema(conn: Any) -> list[dict[str, Any]]:
    """The tables and columns of a database connector its owner left visible."""
    tables: list[dict[str, Any]] = []
    for table in conn.database_schemas or []:
        if not table.is_visible:
            continue
        columns = [
            {
                "column_name": col.column_name,
                "data_type": col.data_type,
                "is_nullable": col.is_nullable,
                "is_primary_key": col.is_primary_key,
                "display_name": col.display_name,
                "description": col.description,
                "is_pii": col.is_pii,
            }
            for col in table.columns or []
            if col.is_visible
        ]
        tables.append(
            {
                "table_name": table.table_name,
                "display_name": table.display_name,
                "description": table.description,
                "column_count": len(columns),
                "columns": columns,
            }
        )
    return tables


async def database_tools(
    db: AsyncSession,
    ctx: CallContext,
    connectors: Sequence[Any],
    *,
    mode: ToolMode,
) -> list[BaseTool]:
    """Tools for the database connectors in *connectors*.

    Rows need ``database_schemas`` and their ``columns`` loaded. Raw-SQL
    tools run on the owner's database account with no per-caller scoping,
    so a connector is left out for anyone but its owner, whatever its
    ``allow_fallback`` says (that switch shares an API credential). One
    with no connection config saved is left out too.
    """
    from fim_one.core.tool.connector.database.adapter import DatabaseToolAdapter
    from fim_one.core.tool.connector.database.meta_tool import build_database_meta_tool

    record = call_recorder(ctx, kind="database")
    databases: list[tuple[Any, dict[str, Any], list[Any]]] = []
    for conn in connectors:
        if not conn.db_config:
            continue
        try:
            credential = await resolve_credential(db, ctx.principal, conn)
            databases.append((conn, credential.db_config or {}, _visible_schema(conn)))
        except AssemblyRefusal as refusal:
            logger.info("Skipping database connector %r: %s", conn.name, refusal.error_code)
        except Exception:
            logger.warning("Failed to load DB connector tools: %s", conn.name, exc_info=True)

    if mode == "progressive":
        if not databases:
            return []
        logger.info(
            "Loaded DatabaseMetaTool (progressive): %d databases, "
            "%d tables consolidated into 1 tool",
            len(databases),
            sum(len(tables) for _, _, tables in databases),
        )
        return [build_database_meta_tool(databases, on_call_complete=record)]

    tools: list[BaseTool] = []
    for conn, config, tables in databases:
        tools.extend(
            DatabaseToolAdapter.create_tools(
                connector_name=conn.name,
                connector_id=conn.id,
                db_config=config,
                schema_tables=tables,
                on_call_complete=record,
            )
        )
    logger.info("Loaded %d DB tools from %d connectors", len(tools), len(databases))
    return tools


class ToolsetError(Exception):
    """A resource could not be turned into tools, for a reason that is not a rule.

    Raised only to a caller that asked for failures (``strict``); ``detail``
    is the text to show whoever started the run.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def _connect_mcp_server(
    client: MCPClient, server: Any, credential: ResolvedCredential
) -> list[Any]:
    """Open *server* with *credential* and return its tools."""
    if server.transport == "stdio" and server.command:
        from fim_one.core.security import is_stdio_allowed

        if not is_stdio_allowed():
            raise ToolsetError(
                "STDIO MCP transport is disabled. Set ALLOW_STDIO_MCP=true to enable."
            )
        return list(
            await client.connect_stdio(
                name=server.name,
                command=server.command,
                args=server.args or [],
                env=credential.env,
                working_dir=server.working_dir,
            )
        )
    if server.transport == "sse" and server.url:
        return list(
            await client.connect_sse(name=server.name, url=server.url, headers=credential.headers)
        )
    if server.transport == "streamable_http" and server.url:
        return list(
            await client.connect_streamable_http(
                name=server.name, url=server.url, headers=credential.headers
            )
        )
    raise ToolsetError(
        f"MCP server '{server.name}' has unsupported or misconfigured transport: {server.transport}"
    )


async def mcp_tools(
    ctx: CallContext,
    servers: Sequence[Any],
    *,
    mode: ToolMode,
    strict: bool = False,
) -> tuple[list[BaseTool], MCPClient | None]:
    """Connect to *servers* and return their tools with the client that holds them.

    A server the acting user has no credential for, or that cannot be
    connected, is left out. With *strict* it raises instead (the refusal, a
    :class:`ToolsetError`, or the connection error), after closing what it
    had opened: for a caller that needs this one server or nothing. The
    caller disconnects the returned client (``disconnect_all``) when the
    run ends.

    Must be awaited in the coroutine that will also disconnect: the stdio
    client enters an anyio cancel scope that has to be left by the task
    that entered it.
    """
    if not servers:
        return [], None

    from fim_one.core.mcp import MCPClient, build_mcp_meta_tool
    from fim_one.db import create_session

    client = MCPClient()
    connected: dict[str, list[Any]] = {}
    for server in servers:
        try:
            async with create_session() as db:
                credential = await resolve_credential(db, ctx.principal, server)
            connected[server.name] = await _connect_mcp_server(client, server, credential)
        except Exception as exc:
            if strict:
                await client.disconnect_all()
                raise
            if isinstance(exc, AssemblyRefusal):
                logger.info("Skipping MCP server %r: %s", server.name, exc.error_code)
            elif isinstance(exc, ToolsetError):
                logger.warning("Skipping MCP server %r: %s", server.name, exc.detail)
            else:
                logger.warning("Failed to connect user MCP server %r", server.name, exc_info=True)

    loaded = sum(len(adapters) for adapters in connected.values())
    if mode == "progressive":
        if not connected:
            return [], client
        logger.info(
            "Loaded MCPServerMetaTool (progressive): %d servers, %d tools consolidated into 1 tool",
            len(connected),
            loaded,
        )
        return [build_mcp_meta_tool(connected)], client

    logger.info("Loaded %d tools from %d user MCP servers", loaded, len(servers))
    return [tool for adapters in connected.values() for tool in adapters], client


async def kb_binding(db: AsyncSession, ctx: CallContext, kb_ids: Sequence[str]) -> dict[str, str]:
    """The knowledge bases in *kb_ids* this call may read, as ``kb_id → owner_id``.

    A knowledge base is not shareable: it is readable while the subject of
    the call (the binding's owner, else the acting user) owns it and it is
    switched on. Its content lives under the owner's namespace, which is
    what retrieval needs the owner id for. Anything else is absent from the
    result.
    """
    decision = await authorize_use(db, ctx, ResourceKind.KNOWLEDGE_BASE, kb_ids)
    return {kb.id: kb.user_id for kb in decision.usable}
