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
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any, Literal

from .calls import call_recorder
from .credentials import CredentialSource, resolve_credential
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
    strict: bool = False,
    action_ids: Collection[str] | None = None,
) -> list[BaseTool]:
    """Tools for the API connectors in *connectors* (rows with ``actions`` loaded).

    A connector the acting user has no credential for, or whose tools cannot
    be built (a base URL the SSRF policy blocks), is left out on its own;
    with *strict* the refusal or error is raised instead, for a caller that
    needs this connector or nothing. *action_ids* limits ``legacy`` mode to
    those actions: a caller that runs one action builds one tool.
    """
    from fim_one.core.tool.connector import ConnectorToolAdapter, build_connector_meta_tool

    sources: dict[str, CredentialSource] = {}
    record = call_recorder(ctx, kind="connector", credential_source=sources)
    callable_connectors: list[tuple[Any, dict[str, Any]]] = []
    for conn in connectors:
        try:
            credential = await resolve_credential(db, ctx.principal, conn)
        except AssemblyRefusal as refusal:
            if strict:
                raise
            logger.info("Skipping connector %r: %s", conn.name, refusal.error_code)
            continue
        sources[conn.id] = credential.source
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
    built_for = 0
    for conn, creds in callable_connectors:
        try:
            conn_tools = [
                ConnectorToolAdapter(
                    connector_name=conn.name,
                    connector_base_url=conn.base_url or "",
                    connector_auth_type=conn.auth_type or "none",
                    connector_auth_config=conn.auth_config,
                    auth_credentials=creds or None,
                    action_name=action.name,
                    action_description=action.description or "",
                    action_method=action.method or "GET",
                    action_path=action.path or "",
                    action_parameters_schema=action.parameters_schema,
                    action_request_body_template=action.request_body_template,
                    action_response_extract=action.response_extract,
                    action_requires_confirmation=action.requires_confirmation,
                    connector_id=conn.id,
                    action_id=action.id,
                    on_call_complete=record,
                )
                for action in conn.actions or []
                if action_ids is None or action.id in action_ids
            ]
        except Exception as exc:
            if strict:
                raise
            # One connector that cannot be built does not cost the others.
            logger.warning("Skipping connector %r: %s", conn.name, exc)
            continue
        tools.extend(conn_tools)
        built_for += 1
    logger.info("Loaded %d API tools from %d connectors", len(tools), built_for)
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

    # Only the owner gets this far, on the config stored with the connector.
    record = call_recorder(ctx, kind="database", credential_source=CredentialSource.OWNER)
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
    taken: Collection[str] = (),
) -> tuple[list[BaseTool], MCPClient | None]:
    """Connect to *servers* and return their tools with the client that holds them.

    A server the acting user has no credential for, or that cannot be
    connected, is left out. With *strict* it raises instead (the refusal, a
    :class:`ToolsetError`, or the connection error): for a caller that needs
    this one server or nothing. Whenever it raises, cancellation included,
    it first closes what it had opened. The caller disconnects the returned
    client (``disconnect_all``) when the run ends.

    *taken* are the tool names the caller already has. A tool whose name is
    among them, or equals one from an earlier server, is left out with a
    warning (the first holder of a name keeps it), and so is a server whose
    name repeats an earlier one's: every tool returned can be registered.

    Must be awaited in the coroutine that will also disconnect: the stdio
    client enters an anyio cancel scope that has to be left by the task
    that entered it.
    """
    if not servers:
        return [], None

    from fim_one.core.mcp import MCPClient

    client = MCPClient()
    try:
        tools = await _open_mcp_servers(
            client, ctx, servers, mode=mode, strict=strict, taken=set(taken)
        )
        return tools, client
    except BaseException:
        # Nobody else holds the client yet, so nobody else can close it. That
        # includes cancellation: a workflow step that times out while a
        # server is connecting is cancelled, not failed.
        try:
            await client.disconnect_all()
        except Exception:
            logger.warning("Failed to close MCP connections after an error", exc_info=True)
        raise


async def _open_mcp_servers(
    client: MCPClient,
    ctx: CallContext,
    servers: Sequence[Any],
    *,
    mode: ToolMode,
    strict: bool,
    taken: set[str],
) -> list[BaseTool]:
    from fim_one.core.mcp import build_mcp_meta_tool
    from fim_one.db import create_session

    sources: dict[str, CredentialSource] = {}
    record = call_recorder(ctx, kind="mcp", credential_source=sources)
    connected: dict[str, list[Any]] = {}
    for server in servers:
        if server.name in connected:
            # The client and the meta-tool key a server by name: a second one
            # would replace the first under it.
            logger.warning(
                "Skipping MCP server %r (%s): another server of that name is connected",
                server.name,
                server.id,
            )
            continue
        try:
            async with create_session() as db:
                credential = await resolve_credential(db, ctx.principal, server)
            adapters = await _connect_mcp_server(client, server, credential)
            sources[server.id] = credential.source
            for adapter in adapters:
                adapter.record_calls(record, server_id=server.id)
            connected[server.name] = adapters
        except Exception as exc:
            if strict:
                raise
            if isinstance(exc, AssemblyRefusal):
                logger.info("Skipping MCP server %r: %s", server.name, exc.error_code)
            elif isinstance(exc, ToolsetError):
                logger.warning("Skipping MCP server %r: %s", server.name, exc.detail)
            else:
                logger.warning("Failed to connect user MCP server %r", server.name, exc_info=True)

    if mode == "progressive":
        meta_tool = build_mcp_meta_tool(connected) if connected else None
        if meta_tool is not None and meta_tool.name in taken:
            logger.warning(
                "Skipping the %r tool for %d MCP server(s): a tool of that name is registered",
                meta_tool.name,
                len(connected),
            )
            meta_tool = None
        if meta_tool is None:
            for name in connected:
                await client.disconnect(name)
            return []
        logger.info(
            "Loaded MCPServerMetaTool (progressive): %d servers, %d tools consolidated into 1 tool",
            len(connected),
            sum(len(adapters) for adapters in connected.values()),
        )
        return [meta_tool]

    tools: list[BaseTool] = []
    for name, adapters in connected.items():
        usable = [adapter for adapter in adapters if adapter.name not in taken]
        if len(usable) < len(adapters):
            logger.warning(
                "Skipping %d tool(s) of MCP server %r whose names are already registered: %s",
                len(adapters) - len(usable),
                name,
                sorted(adapter.name for adapter in adapters if adapter.name in taken),
            )
        if not usable:
            await client.disconnect(name)
            continue
        taken.update(adapter.name for adapter in usable)
        tools.extend(usable)
    logger.info("Loaded %d tools from %d user MCP servers", len(tools), len(servers))
    return tools


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
