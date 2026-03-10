"""Connector management API."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.core.security import get_safe_async_client, validate_url
from fim_agent.core.tool.connector.openapi_parser import parse_openapi_spec
from fim_agent.web.exceptions import AppError
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user, get_user_org_ids
from fim_agent.web.models.connector import Connector, ConnectorAction
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_agent.web.schemas.connector import (
    ActionCreate,
    ActionResponse,
    ActionUpdate,
    ConnectorCreate,
    ConnectorResponse,
    ConnectorUpdate,
    OpenAPIImportRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action_to_response(action: ConnectorAction) -> ActionResponse:
    return ActionResponse(
        id=action.id,
        connector_id=action.connector_id,
        name=action.name,
        description=action.description,
        method=action.method,
        path=action.path,
        parameters_schema=action.parameters_schema,
        request_body_template=action.request_body_template,
        response_extract=action.response_extract,
        requires_confirmation=action.requires_confirmation,
        created_at=action.created_at.isoformat() if action.created_at else "",
        updated_at=action.updated_at.isoformat() if action.updated_at else None,
    )


def _connector_to_response(connector: Connector) -> ConnectorResponse:
    return ConnectorResponse(
        id=connector.id,
        name=connector.name,
        description=connector.description,
        icon=connector.icon,
        type=connector.type,
        base_url=connector.base_url,
        auth_type=connector.auth_type,
        auth_config=connector.auth_config,
        is_official=connector.is_official,
        forked_from=connector.forked_from,
        version=connector.version,
        visibility=getattr(connector, "visibility", "personal"),
        org_id=getattr(connector, "org_id", None),
        actions=[_action_to_response(a) for a in (connector.actions or [])],
        created_at=connector.created_at.isoformat() if connector.created_at else "",
        updated_at=connector.updated_at.isoformat() if connector.updated_at else None,
    )


async def _get_owned_connector(
    connector_id: str, user_id: str, db: AsyncSession,
) -> Connector:
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector_id, Connector.user_id == user_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise AppError("connector_not_found", status_code=404)
    return connector


# ---------------------------------------------------------------------------
# Connector CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse)
async def create_connector(
    body: ConnectorCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    connector = Connector(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        type=body.type,
        base_url=body.base_url,
        auth_type=body.auth_type,
        auth_config=body.auth_config,
        status="published",
    )
    db.add(connector)
    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_connectors(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    from fim_agent.web.visibility import build_visibility_filter
    user_org_ids = await get_user_org_ids(current_user.id, db)
    base = select(Connector).where(
        build_visibility_filter(Connector, current_user.id, user_org_ids)
    )

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.options(selectinload(Connector.actions))
        .order_by(Connector.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    connectors = result.scalars().all()

    return PaginatedResponse(
        items=[_connector_to_response(c).model_dump() for c in connectors],
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{connector_id}", response_model=ApiResponse)
async def get_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.put("/{connector_id}", response_model=ApiResponse)
async def update_connector(
    connector_id: str,
    body: ConnectorUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    connector = await _get_owned_connector(connector_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(connector, field, value)

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.delete("/{connector_id}", response_model=ApiResponse)
async def delete_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    await db.delete(connector)
    await db.commit()
    return ApiResponse(data={"deleted": connector_id})


# ---------------------------------------------------------------------------
# Publish / Unpublish
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/publish", response_model=ApiResponse)
async def publish_connector(
    connector_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish connector to org or global scope."""
    connector = await _get_owned_connector(connector_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_agent.web.auth import require_org_member
        await require_org_member(body.org_id, current_user, db)
        connector.visibility = "org"
        connector.org_id = body.org_id
    elif body.scope == "global":
        if not current_user.is_admin:
            raise AppError("admin_required_for_global", status_code=403)
        connector.visibility = "global"
        connector.org_id = None
    else:
        raise AppError("invalid_scope", status_code=400)

    await db.commit()
    await db.refresh(connector)

    # Reload with actions for response
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.post("/{connector_id}/unpublish", response_model=ApiResponse)
async def unpublish_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert connector to personal visibility."""
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector_id)
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise AppError("connector_not_found", status_code=404)

    is_owner = connector.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if getattr(connector, "visibility", "personal") == "org" and connector.org_id and not is_owner:
        try:
            from fim_agent.web.auth import require_org_admin
            await require_org_admin(connector.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    connector.visibility = "personal"
    connector.org_id = None

    await db.commit()
    await db.refresh(connector)

    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


# ---------------------------------------------------------------------------
# OpenAPI Import
# ---------------------------------------------------------------------------


async def _resolve_openapi_spec(body: OpenAPIImportRequest) -> dict[str, Any]:
    """Resolve OpenAPI spec from one of three input modes.

    Priority: ``spec`` (parsed dict) > ``spec_raw`` (string) > ``spec_url``.
    """
    if body.spec is not None:
        return body.spec

    raw: str | None = body.spec_raw

    if raw is None and body.spec_url:
        try:
            validate_url(body.spec_url)
        except ValueError as exc:
            raise AppError(
                "spec_url_blocked",
                status_code=400,
                detail=str(exc),
            ) from exc
        try:
            async with get_safe_async_client(timeout=15) as client:
                resp = await client.get(body.spec_url)
                resp.raise_for_status()
                raw = resp.text
        except httpx.HTTPError as exc:
            raise AppError(
                "spec_fetch_failed",
                status_code=422,
                detail=f"Failed to fetch spec URL: {exc}",
                detail_args={"reason": str(exc)},
            ) from exc

    if raw is None:
        raise AppError("spec_input_required", status_code=400)

    # Try JSON first, then YAML
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        parsed = yaml.safe_load(raw)
        if isinstance(parsed, dict):
            return parsed
    except yaml.YAMLError:
        pass

    raise AppError("spec_parse_failed", status_code=422)


@router.post("/import-openapi", response_model=ApiResponse)
async def import_openapi(
    body: OpenAPIImportRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """One-shot import: create a Connector + Actions from an OpenAPI spec."""
    spec = await _resolve_openapi_spec(body)
    info = spec.get("info", {})
    servers = spec.get("servers", [])
    base_url = servers[0]["url"] if servers else ""

    if not base_url:
        raise AppError("spec_no_server_url", status_code=422)

    connector = Connector(
        user_id=current_user.id,
        name=info.get("title", "Imported API")[:200],
        description=info.get("description"),
        type="api",
        base_url=base_url,
        auth_type="none",
        status="published",
    )
    db.add(connector)
    await db.flush()  # get connector.id

    action_dicts = parse_openapi_spec(spec)
    for ad in action_dicts:
        action = ConnectorAction(
            connector_id=connector.id,
            name=ad["name"],
            description=ad.get("description"),
            method=ad.get("method", "GET"),
            path=ad.get("path", "/"),
            parameters_schema=ad.get("parameters_schema"),
            request_body_template=ad.get("request_body_template"),
            requires_confirmation=ad.get("requires_confirmation", False),
        )
        db.add(action)

    await db.commit()

    # Reload with actions
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.post("/{connector_id}/import-openapi", response_model=ApiResponse)
async def import_openapi_actions(
    connector_id: str,
    body: OpenAPIImportRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Add actions from an OpenAPI spec to an existing connector."""
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    spec = await _resolve_openapi_spec(body)

    if body.replace_existing:
        for existing_action in list(connector.actions or []):
            await db.delete(existing_action)

    action_dicts = parse_openapi_spec(spec)
    for ad in action_dicts:
        action = ConnectorAction(
            connector_id=connector.id,
            name=ad["name"],
            description=ad.get("description"),
            method=ad.get("method", "GET"),
            path=ad.get("path", "/"),
            parameters_schema=ad.get("parameters_schema"),
            request_body_template=ad.get("request_body_template"),
            requires_confirmation=ad.get("requires_confirmation", False),
        )
        db.add(action)

    await db.commit()

    # Reload with updated actions
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


# ---------------------------------------------------------------------------
# Action CRUD (nested under connector)
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/actions", response_model=ApiResponse)
async def create_action(
    connector_id: str,
    body: ActionCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify connector ownership
    await _get_owned_connector(connector_id, current_user.id, db)

    action = ConnectorAction(
        connector_id=connector_id,
        name=body.name,
        description=body.description,
        method=body.method,
        path=body.path,
        parameters_schema=body.parameters_schema,
        request_body_template=body.request_body_template,
        response_extract=body.response_extract,
        requires_confirmation=body.requires_confirmation,
    )
    db.add(action)
    await db.commit()
    result = await db.execute(
        select(ConnectorAction).where(ConnectorAction.id == action.id)
    )
    action = result.scalar_one()
    return ApiResponse(data=_action_to_response(action).model_dump())


@router.put("/{connector_id}/actions/{action_id}", response_model=ApiResponse)
async def update_action(
    connector_id: str,
    action_id: str,
    body: ActionUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify connector ownership
    await _get_owned_connector(connector_id, current_user.id, db)

    result = await db.execute(
        select(ConnectorAction).where(
            ConnectorAction.id == action_id,
            ConnectorAction.connector_id == connector_id,
        )
    )
    action = result.scalar_one_or_none()
    if action is None:
        raise AppError("action_not_found", status_code=404)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(action, field, value)

    await db.commit()
    result = await db.execute(
        select(ConnectorAction).where(ConnectorAction.id == action.id)
    )
    action = result.scalar_one()
    return ApiResponse(data=_action_to_response(action).model_dump())


@router.delete("/{connector_id}/actions/{action_id}", response_model=ApiResponse)
async def delete_action(
    connector_id: str,
    action_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Verify connector ownership
    await _get_owned_connector(connector_id, current_user.id, db)

    result = await db.execute(
        select(ConnectorAction).where(
            ConnectorAction.id == action_id,
            ConnectorAction.connector_id == connector_id,
        )
    )
    action = result.scalar_one_or_none()
    if action is None:
        raise AppError("action_not_found", status_code=404)

    await db.delete(action)
    await db.commit()
    return ApiResponse(data={"deleted": action_id})
