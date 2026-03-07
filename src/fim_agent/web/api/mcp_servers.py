"""MCP Server management API."""

from __future__ import annotations

import logging
import math
import os

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.db import get_session
from fim_agent.web.exceptions import AppError
from fim_agent.web.auth import get_current_user
from fim_agent.web.models.mcp_server import MCPServer
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse
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


# ---------------------------------------------------------------------------
# Capabilities (must be before /{server_id} to avoid path conflict)
# ---------------------------------------------------------------------------


@router.get("/capabilities")
async def get_capabilities():
    allow_stdio = os.environ.get("ALLOW_STDIO_MCP", "true").lower() != "false"
    return {"allow_stdio": allow_stdio}



# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_mcp_server(
    body: MCPServerCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
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
    base = select(MCPServer).where(MCPServer.user_id == current_user.id)

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
