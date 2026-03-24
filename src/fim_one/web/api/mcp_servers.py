"""MCP Server management API."""

from __future__ import annotations

import json
import logging
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.security import is_stdio_allowed, validate_stdio_command
from fim_one.db import get_session
from fim_one.web.exceptions import AppError
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.web.models.mcp_server import MCPServer
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.mcp_server import (
    MCPMyCredentialStatus,
    MCPMyCredentialUpsert,
    MCPServerCreate,
    MCPServerForkRequest,
    MCPServerResponse,
    MCPServerUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_dict(d: dict[str, str] | None) -> dict[str, str] | None:
    if not d:
        return d
    return {k: "***" for k in d}


def _to_response(srv: MCPServer, *, is_owner: bool = True, my_has_credentials: bool = False) -> MCPServerResponse:
    # Mask sensitive fields for non-owners
    env = srv.env if is_owner else _mask_dict(srv.env)
    headers = srv.headers if is_owner else _mask_dict(srv.headers)
    return MCPServerResponse(
        id=srv.id,
        user_id=srv.user_id or "",
        name=srv.name,
        description=srv.description,
        transport=srv.transport,
        command=srv.command,
        args=srv.args,
        env=env,
        url=srv.url,
        working_dir=srv.working_dir,
        headers=headers,
        is_active=srv.is_active,
        tool_count=srv.tool_count,
        allow_fallback=getattr(srv, "allow_fallback", True),
        forked_from=getattr(srv, "forked_from", None),
        my_has_credentials=my_has_credentials,
        visibility=getattr(srv, "visibility", "personal"),
        org_id=getattr(srv, "org_id", None),
        publish_status=getattr(srv, "publish_status", None),
        reviewed_by=getattr(srv, "reviewed_by", None),
        reviewed_at=(
            srv.reviewed_at.isoformat() if srv.reviewed_at else None
        ),
        review_note=getattr(srv, "review_note", None),
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
async def get_capabilities() -> dict[str, bool]:
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
    from fim_one.web.models.mcp_server_credential import MCPServerCredential
    from fim_one.web.visibility import build_visibility_filter
    user_org_ids = await get_user_org_ids(current_user.id, db)

    # Get subscribed MCP server IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "mcp_server",
        )
    )
    sub_rows = sub_result.all()
    subscribed_mcp_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(MCPServer).where(
        build_visibility_filter(MCPServer, current_user.id, user_org_ids, subscribed_ids=subscribed_mcp_ids)
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

    # Bulk-fetch credential existence for current user
    server_ids = [s.id for s in servers]
    cred_set: set[str] = set()
    if server_ids:
        cred_result = await db.execute(
            select(MCPServerCredential.server_id).where(
                MCPServerCredential.server_id.in_(server_ids),
                MCPServerCredential.user_id == current_user.id,
            )
        )
        cred_set = {row[0] for row in cred_result.all()}

    subscribed_mcp_ids_set = set(subscribed_mcp_ids)
    items = []
    for s in servers:
        _is_owner = s.user_id == current_user.id
        resp = _to_response(s, is_owner=_is_owner, my_has_credentials=s.id in cred_set)
        if _is_owner:
            resp.source = "own"
        elif s.id in subscribed_mcp_ids_set:
            sub_org_id = sub_org_map.get(s.id)
            resp.source = "market" if sub_org_id == MARKET_ORG_ID else "org"
        else:
            resp.source = "org"  # fallback, should not be reached
        items.append(resp.model_dump())

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


async def _get_accessible_server(
    server_id: str, user_id: str, db: AsyncSession,
) -> MCPServer:
    """Fetch an MCP server the user owns, org-shared, or Market-installed."""
    from fim_one.web.visibility import resolve_visibility
    vis_filter, _, _ = await resolve_visibility(MCPServer, user_id, "mcp_server", db)
    result = await db.execute(
        select(MCPServer).where(MCPServer.id == server_id, vis_filter)
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise AppError("mcp_server_not_found", status_code=404)
    return server


@router.get("/{server_id}", response_model=ApiResponse)
async def get_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    from fim_one.web.models.mcp_server_credential import MCPServerCredential
    server = await _get_accessible_server(server_id, current_user.id, db)
    cred_result = await db.execute(
        select(MCPServerCredential).where(
            MCPServerCredential.server_id == server_id,
            MCPServerCredential.user_id == current_user.id,
        )
    )
    my_has_credentials = cred_result.scalar_one_or_none() is not None
    return ApiResponse(data=_to_response(server, my_has_credentials=my_has_credentials).model_dump())


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

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(server, db)
    else:
        reverted = False

    await db.commit()

    result = await db.execute(select(MCPServer).where(MCPServer.id == server.id))
    server = result.scalar_one()
    data = _to_response(server).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


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
        from fim_one.core.mcp import MCPClient
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
        from fim_one.web.auth import require_org_member
        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        server.visibility = "org"
        server.org_id = body.org_id
        # allow_fallback is now managed via server settings (update endpoint), not at publish time
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(server, body.org_id, db, resource_type="mcp_server", publisher_id=current_user.id)

        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="mcp_server",
            resource_id=server.id,
            resource_name=server.name,
            action="submitted",
            actor=current_user,
        )
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


@router.post("/{server_id}/resubmit", response_model=ApiResponse)
async def resubmit_mcp_server(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected MCP server for review."""
    server = await _get_owned_server(server_id, current_user.id, db)
    if server.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    server.publish_status = "pending_review"
    server.reviewed_by = None
    server.reviewed_at = None
    server.review_note = None

    if server.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=server.org_id,
            resource_type="mcp_server",
            resource_id=server.id,
            resource_name=server.name,
            action="resubmitted",
            actor=current_user,
        )

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
            from fim_one.web.auth import require_org_admin
            await require_org_admin(server.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id
    _org_id = getattr(server, "org_id", None)
    if _org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=_org_id,
            resource_type="mcp_server",
            resource_id=server.id,
            resource_name=server.name,
            action="unpublished",
            actor=current_user,
        )

    server.visibility = "personal"
    server.org_id = None
    server.publish_status = None

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


# ---------------------------------------------------------------------------
# Fork (clone)
# ---------------------------------------------------------------------------


@router.post("/{server_id}/fork", response_model=ApiResponse)
async def fork_mcp_server(
    server_id: str,
    body: MCPServerForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clone an existing MCP server for the current user.

    Copies all configuration fields but NOT encrypted env/headers, org_id, or
    publish_status.  The forked server is set to personal/active ownership.
    """
    # Fetch the source server — must be visible to the user (own / org / global)
    source = await _get_accessible_server(server_id, current_user.id, db)

    fork_name = (body.name if body and body.name else f"{source.name} (Fork)")[:200]

    forked = MCPServer(
        user_id=current_user.id,
        name=fork_name,
        description=source.description,
        transport=source.transport,
        command=source.command,
        args=source.args,
        env=None,  # encrypted credentials — do NOT copy
        url=source.url,
        working_dir=source.working_dir,
        headers=None,  # encrypted credentials — do NOT copy
        is_active=True,
        tool_count=source.tool_count,
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
        allow_fallback=source.allow_fallback,
    )
    db.add(forked)
    await db.commit()

    result = await db.execute(
        select(MCPServer).where(MCPServer.id == forked.id)
    )
    forked = result.scalar_one()
    return ApiResponse(data=_to_response(forked).model_dump())


# ---------------------------------------------------------------------------
# Per-user credential endpoints
# ---------------------------------------------------------------------------


@router.get("/{server_id}/my-credentials", response_model=ApiResponse)
async def get_my_mcp_credentials(
    server_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return current user's personal credentials status for this MCP server."""
    from fim_one.web.models.mcp_server_credential import MCPServerCredential

    server = await _get_accessible_server(server_id, current_user.id, db)
    result = await db.execute(
        select(MCPServerCredential).where(
            MCPServerCredential.server_id == server_id,
            MCPServerCredential.user_id == current_user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        # Return server env key names as template for pre-population
        server_env_keys = list((server.env or {}).keys())
        return ApiResponse(
            data=MCPMyCredentialStatus(has_credentials=False, env_keys=server_env_keys).model_dump()
        )
    env: dict[str, str] = dict(row.env_blob) if row.env_blob else {}
    return ApiResponse(
        data=MCPMyCredentialStatus(
            has_credentials=True,
            env_keys=list(env.keys()),
            env=env,
        ).model_dump()
    )


@router.put("/{server_id}/my-credentials", response_model=ApiResponse)
async def upsert_my_mcp_credentials(
    server_id: str,
    body: MCPMyCredentialUpsert,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create or replace the current user's personal env credentials for this MCP server."""
    from fim_one.web.models.mcp_server_credential import MCPServerCredential

    await _get_accessible_server(server_id, current_user.id, db)

    env_data: dict[str, str] = body.env or {}
    headers_data: dict[str, str] = body.headers or {}

    existing = await db.execute(
        select(MCPServerCredential).where(
            MCPServerCredential.server_id == server_id,
            MCPServerCredential.user_id == current_user.id,
        )
    )
    row = existing.scalar_one_or_none()

    # Empty env + empty headers = user cleared everything → delete the credential row
    if not env_data and not headers_data:
        if row:
            await db.delete(row)
            await db.commit()
        return ApiResponse(data={"saved": True, "cleared": True})

    env_blob = env_data or None
    headers_blob = headers_data or None

    if row:
        setattr(row, "env_blob", env_blob)
        setattr(row, "headers_blob", headers_blob)
    else:
        row = MCPServerCredential(
            server_id=server_id,
            user_id=current_user.id,
            env_blob=env_blob,
            headers_blob=headers_blob,
        )
        db.add(row)

    await db.commit()
    return ApiResponse(data={"saved": True})
