"""MCP Server management API."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.core.security import is_stdio_allowed, validate_stdio_command
from fim_agent.db import get_session
from fim_agent.web.exceptions import AppError
from fim_agent.web.auth import get_current_user, get_user_org_ids
from fim_agent.web.models.mcp_server import MCPServer
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_agent.web.schemas.mcp_server import (
    MCPServerCreate,
    MCPServerResponse,
    MCPServerUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(srv: MCPServer) -> MCPServerResponse:
    return MCPServerResponse(
        id=srv.id,
        name=srv.name,
        description=srv.description,
        transport=srv.transport,
        command=srv.command,
        args=srv.args,
        env=srv.env,
        url=srv.url,
        working_dir=srv.working_dir,
        headers=srv.headers,
        is_active=srv.is_active,
        tool_count=srv.tool_count,
        visibility=getattr(srv, "visibility", "personal"),
        org_id=getattr(srv, "org_id", None),
        created_at=srv.created_at.isoformat() if srv.created_at else "",
        updated_at=srv.updated_at.isoformat() if srv.updated_at else None,
    )


async def _get_owned_server(
    server_id: str, user_id: str, db: AsyncSession,
) -> MCPServer:
    result = await db.execute(
        select(MCPServer).where(MCPServer.id == server_id, MCPServer.user_id == user_id)
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("mcp_server_not_found", status_code=404)
    return server


def _enforce_stdio_policy(transport: str) -> None:
    """Raise AppError if stdio transport is requested but disabled."""
    if transport == "stdio" and not is_stdio_allowed():
        raise AppError(
            "stdio_mcp_disabled",
            status_code=403,
            detail="Stdio MCP servers are disabled by administrator",
        )


# ---------------------------------------------------------------------------
# Capabilities (must be before /{server_id} to avoid path conflict)
# ---------------------------------------------------------------------------


@router.get("/capabilities")
async def get_capabilities():
    return {"allow_stdio": is_stdio_allowed()}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_mcp_server(
    body: MCPServerCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    _enforce_stdio_policy(body.transport)

    if body.transport == "stdio" and body.command:
        try:
            validate_stdio_command(body.command)
        except ValueError as exc:
            raise AppError(
                "stdio_command_not_allowed",
                status_code=400,
                detail=str(exc),
            ) from exc

    server = MCPServer(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        working_dir=body.working_dir,
        headers=body.headers,
        is_active=body.is_active,
    )
    db.add(server)
    await db.commit()

    result = await db.execute(select(MCPServer).where(MCPServer.id == server.id))
    server = result.scalar_one()
    return ApiResponse(data=_to_response(server).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_mcp_servers(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    from fim_agent.web.visibility import build_visibility_filter
    user_org_ids = await get_user_org_ids(current_user.id, db)
    base = select(MCPServer).where(
        build_visibility_filter(MCPServer, current_user.id, user_org_ids)
    )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(MCPServer.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    servers = result.scalars().all()

    return PaginatedResponse(
        items=[_to_response(s).model_dump() for s in servers],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{server_id}", response_model=ApiResponse)
async def get_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    server = await _get_owned_server(server_id, current_user.id, db)
    return ApiResponse(data=_to_response(server).model_dump())


@router.put("/{server_id}", response_model=ApiResponse)
async def update_mcp_server(
    server_id: str,
    body: MCPServerUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    server = await _get_owned_server(server_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)

    # If transport is being changed to stdio, enforce the policy
    new_transport = update_data.get("transport", server.transport)
    _enforce_stdio_policy(new_transport)

    new_command = update_data.get("command", server.command)
    if new_transport == "stdio" and new_command:
        try:
            validate_stdio_command(new_command)
        except ValueError as exc:
            raise AppError(
                "stdio_command_not_allowed",
                status_code=400,
                detail=str(exc),
            ) from exc

    for field, value in update_data.items():
        setattr(server, field, value)

    await db.commit()

    result = await db.execute(select(MCPServer).where(MCPServer.id == server.id))
    server = result.scalar_one()
    return ApiResponse(data=_to_response(server).model_dump())


@router.post("/{server_id}/test", response_model=ApiResponse)
async def test_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Test connectivity to an MCP server and update its tool_count."""
    server = await _get_owned_server(server_id, current_user.id, db)
    _enforce_stdio_policy(server.transport)

    try:
        from fim_agent.core.mcp import MCPClient
    except ImportError:
        return ApiResponse(data={"ok": False, "error": "mcp package not installed"})

    client = MCPClient()
    try:
        if server.transport == "stdio":
            tools = await client.connect_stdio(
                name=server.name,
                command=server.command or "",
                args=server.args or [],
                env=server.env,
                working_dir=server.working_dir,
            )
        elif server.transport == "sse":
            tools = await client.connect_sse(
                name=server.name,
                url=server.url or "",
                headers=server.headers,
            )
        else:
            tools = await client.connect_streamable_http(
                name=server.name,
                url=server.url or "",
                headers=server.headers,
            )

        count = len(tools)
        server.tool_count = count
        await db.commit()
        tool_names = [t.name for t in tools]
        return ApiResponse(data={"ok": True, "tool_count": count, "tools": tool_names})
    except Exception as exc:
        logger.warning("MCP test failed for server %r: %s", server.name, exc)
        return ApiResponse(data={"ok": False, "error": str(exc)})
    finally:
        await client.disconnect_all()


# ---------------------------------------------------------------------------
# Publish / Unpublish
# ---------------------------------------------------------------------------


@router.post("/{server_id}/publish", response_model=ApiResponse)
async def publish_mcp_server(
    server_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish MCP server to org or global scope."""
    server = await _get_owned_server(server_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_agent.web.auth import require_org_member
        await require_org_member(body.org_id, current_user, db)
        server.visibility = "org"
        server.org_id = body.org_id
    elif body.scope == "global":
        if not current_user.is_admin:
            raise AppError("admin_required_for_global", status_code=403)
        server.visibility = "global"
        server.org_id = None
        if hasattr(server, "is_global"):
            server.is_global = True
    else:
        raise AppError("invalid_scope", status_code=400)

    await db.commit()
    await db.refresh(server)
    return ApiResponse(data=_to_response(server).model_dump())


@router.post("/{server_id}/unpublish", response_model=ApiResponse)
async def unpublish_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert MCP server to personal visibility."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("mcp_server_not_found", status_code=404)

    is_owner = server.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if getattr(server, "visibility", "personal") == "org" and server.org_id and not is_owner:
        try:
            from fim_agent.web.auth import require_org_admin
            await require_org_admin(server.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    server.visibility = "personal"
    server.org_id = None
    if hasattr(server, "is_global"):
        server.is_global = False

    await db.commit()
    await db.refresh(server)
    return ApiResponse(data=_to_response(server).model_dump())


@router.delete("/{server_id}", response_model=ApiResponse)
async def delete_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    server = await _get_owned_server(server_id, current_user.id, db)
    await db.delete(server)
    await db.commit()
    return ApiResponse(data={"deleted": server_id})
