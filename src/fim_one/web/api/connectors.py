"""Connector management API."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.core.security import get_safe_async_client, validate_url
from fim_one.core.tool.connector.openapi_parser import parse_openapi_spec
from fim_one.web.exceptions import AppError
from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.models.connector import Connector, ConnectorAction
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.connector import (
    ActionCreate,
    ActionResponse,
    ActionUpdate,
    ConnectorCreate,
    ConnectorResponse,
    ConnectorUpdate,
    CredentialUpsertRequest,
    MyCredentialStatus,
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


def _mask_db_config(db_config: dict | None) -> dict | None:
    """Return a copy of db_config with sensitive fields masked."""
    if not db_config:
        return db_config
    masked = dict(db_config)
    if "encrypted_password" in masked:
        masked.pop("encrypted_password")
        masked["password"] = "***"
    if "password" in masked and masked["password"] and masked["password"] != "***":
        masked["password"] = "***"
    return masked


_AUTH_SENSITIVE_FIELDS: dict[str, list[str]] = {
    "bearer": ["default_token"],
    "api_key": ["default_api_key"],
    "basic": ["default_username", "default_password"],
}


def _split_auth_config(
    auth_type: str, auth_config: dict | None
) -> tuple[dict, dict]:
    """Split auth_config into (clean_config, cred_blob).

    clean_config: non-sensitive fields only (token_prefix, header_name, etc.)
    cred_blob: sensitive fields (default_token, default_api_key, etc.)
    """
    if not auth_config:
        return {}, {}
    sensitive = _AUTH_SENSITIVE_FIELDS.get(auth_type, [])
    clean = {k: v for k, v in auth_config.items() if k not in sensitive}
    cred_blob = {k: v for k, v in auth_config.items() if k in sensitive and v}
    return clean, cred_blob


def _strip_sensitive_auth_config(
    auth_type: str, auth_config: dict | None
) -> dict | None:
    """Return auth_config with sensitive credential fields removed (for API responses)."""
    if not auth_config:
        return auth_config
    sensitive = _AUTH_SENSITIVE_FIELDS.get(auth_type, [])
    return {k: v for k, v in auth_config.items() if k not in sensitive}


async def _upsert_default_credential(
    connector_id: str, cred_blob: dict, db: AsyncSession
) -> None:
    """Create or update the connector-owner's default credential row (user_id=NULL)."""
    from fim_one.core.security.encryption import encrypt_credential

    encrypted = encrypt_credential(cred_blob)
    existing = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.connector_id == connector_id,
            ConnectorCredential.user_id.is_(None),
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.credentials_blob = encrypted
    else:
        row = ConnectorCredential(
            connector_id=connector_id,
            user_id=None,
            credentials_blob=encrypted,
        )
        db.add(row)


async def _has_default_credential(connector_id: str, db: AsyncSession) -> bool:
    """Check whether a default (owner) credential row exists for this connector."""
    result = await db.execute(
        select(ConnectorCredential.id).where(
            ConnectorCredential.connector_id == connector_id,
            ConnectorCredential.user_id.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


def _connector_to_response(
    connector: Connector,
    has_default_credentials: bool = False,
) -> ConnectorResponse:
    return ConnectorResponse(
        id=connector.id,
        user_id=connector.user_id,
        name=connector.name,
        description=connector.description,
        icon=connector.icon,
        type=connector.type,
        base_url=connector.base_url,
        auth_type=connector.auth_type,
        auth_config=_strip_sensitive_auth_config(connector.auth_type, connector.auth_config),
        db_config=_mask_db_config(connector.db_config),
        is_official=connector.is_official,
        forked_from=connector.forked_from,
        version=connector.version,
        visibility=getattr(connector, "visibility", "personal"),
        org_id=getattr(connector, "org_id", None),
        allow_fallback=getattr(connector, "allow_fallback", True),
        has_default_credentials=has_default_credentials,
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


async def _get_visible_connector(
    connector_id: str, user_id: str, db: AsyncSession
) -> Connector:
    """Fetch a connector visible to the given user (own + org + global)."""
    from fim_one.web.visibility import build_visibility_filter

    user_org_ids = await get_user_org_ids(user_id, db)
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(
            Connector.id == connector_id,
            build_visibility_filter(Connector, user_id, user_org_ids),
        )
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
    # Handle database connector — encrypt password in db_config
    db_config = None
    if body.type == "database":
        if not body.db_config:
            raise AppError(
                "db_config_required",
                status_code=400,
                detail="db_config is required for database connectors",
            )
        from fim_one.core.security.encryption import encrypt_db_config

        db_config = encrypt_db_config(body.db_config)
    elif not body.base_url:
        raise AppError(
            "base_url_required",
            status_code=400,
            detail="base_url is required for API connectors",
        )

    # Split sensitive fields out of auth_config before storing
    clean_auth_config, cred_blob = _split_auth_config(body.auth_type, body.auth_config)

    connector = Connector(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        type=body.type,
        base_url=body.base_url,
        auth_type=body.auth_type,
        auth_config=clean_auth_config or None,
        db_config=db_config,
        status="published",
    )
    db.add(connector)
    await db.flush()  # get connector.id

    if cred_blob:
        await _upsert_default_credential(connector.id, cred_blob, db)

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    has_creds = await _has_default_credential(connector.id, db)
    return ApiResponse(data=_connector_to_response(connector, has_default_credentials=has_creds).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_connectors(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    from fim_one.web.visibility import build_visibility_filter
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
    from fim_one.web.visibility import build_visibility_filter

    user_org_ids = await get_user_org_ids(current_user.id, db)
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(
            Connector.id == connector_id,
            build_visibility_filter(Connector, current_user.id, user_org_ids),
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise AppError("connector_not_found", status_code=404)
    has_creds = await _has_default_credential(connector_id, db)
    return ApiResponse(data=_connector_to_response(connector, has_default_credentials=has_creds).model_dump())


@router.put("/{connector_id}", response_model=ApiResponse)
async def update_connector(
    connector_id: str,
    body: ConnectorUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    connector = await _get_owned_connector(connector_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)

    # Re-encrypt password if db_config is being updated
    if "db_config" in update_data and update_data["db_config"]:
        from fim_one.core.security.encryption import encrypt_db_config

        new_config = update_data["db_config"]
        # If password is the masked sentinel "***", preserve existing
        # encrypted password instead of encrypting the literal "***".
        if new_config.get("password") == "***" and connector.db_config:
            new_config.pop("password")
            existing_encrypted = connector.db_config.get("encrypted_password")
            if existing_encrypted:
                new_config["encrypted_password"] = existing_encrypted
            update_data["db_config"] = new_config
        else:
            update_data["db_config"] = encrypt_db_config(new_config)
        # Close any existing driver pool for this connector
        from fim_one.core.tool.connector.database.pool import ConnectionPoolManager

        pool = ConnectionPoolManager.get_instance()
        await pool.close_driver(connector_id)

    # Handle auth_config credential split
    if "auth_config" in update_data:
        auth_type = update_data.get("auth_type") or connector.auth_type
        clean_config, cred_blob = _split_auth_config(auth_type, update_data["auth_config"])
        update_data["auth_config"] = clean_config or None
        # Only update credential if blob is non-empty; otherwise keep existing
        if cred_blob:
            await _upsert_default_credential(connector_id, cred_blob, db)

    # Handle allow_fallback separately (it's a direct column, not JSON)
    allow_fallback = update_data.pop("allow_fallback", None)
    if allow_fallback is not None:
        connector.allow_fallback = allow_fallback

    for field, value in update_data.items():
        setattr(connector, field, value)

    # Explicitly mark JSON columns as modified so SQLAlchemy flushes them
    # even when the dict content changes without an object identity change.
    if "db_config" in update_data:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(connector, "db_config")
    if "auth_config" in update_data:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(connector, "auth_config")

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    has_creds = await _has_default_credential(connector.id, db)
    return ApiResponse(data=_connector_to_response(connector, has_default_credentials=has_creds).model_dump())


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
# Per-user credential endpoints
# ---------------------------------------------------------------------------


@router.get("/{connector_id}/my-credentials", response_model=ApiResponse)
async def get_my_credentials(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Return whether the current user has personal credentials for this connector."""
    connector = await _get_visible_connector(connector_id, current_user.id, db)
    result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.connector_id == connector_id,
            ConnectorCredential.user_id == current_user.id,
        )
    )
    row = result.scalar_one_or_none()
    return ApiResponse(
        data=MyCredentialStatus(
            has_credentials=row is not None,
            auth_type=connector.auth_type,
            allow_fallback=getattr(connector, "allow_fallback", True),
        ).model_dump()
    )


@router.put("/{connector_id}/my-credentials", response_model=ApiResponse)
async def upsert_my_credentials(
    connector_id: str,
    body: CredentialUpsertRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create or replace the current user's personal credentials for this connector."""
    connector = await _get_visible_connector(connector_id, current_user.id, db)

    # Build credential blob from request based on connector auth_type
    cred_blob: dict = {}
    if connector.auth_type == "bearer" and body.token:
        cred_blob["default_token"] = body.token
    elif connector.auth_type == "api_key" and body.api_key:
        cred_blob["default_api_key"] = body.api_key
    elif connector.auth_type == "basic":
        if body.username:
            cred_blob["default_username"] = body.username
        if body.password:
            cred_blob["default_password"] = body.password

    if not cred_blob:
        raise AppError("no_credentials_provided", status_code=400)

    from fim_one.core.security.encryption import encrypt_credential

    encrypted = encrypt_credential(cred_blob)

    existing = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.connector_id == connector_id,
            ConnectorCredential.user_id == current_user.id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.credentials_blob = encrypted
    else:
        row = ConnectorCredential(
            connector_id=connector_id,
            user_id=current_user.id,
            credentials_blob=encrypted,
        )
        db.add(row)

    await db.commit()
    return ApiResponse(data={"saved": True})


@router.delete("/{connector_id}/my-credentials", response_model=ApiResponse)
async def delete_my_credentials(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Delete the current user's personal credentials for this connector."""
    await _get_visible_connector(connector_id, current_user.id, db)
    result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.connector_id == connector_id,
            ConnectorCredential.user_id == current_user.id,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return ApiResponse(data={"deleted": True})


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
        from fim_one.web.auth import require_org_member
        await require_org_member(body.org_id, current_user, db)
        connector.visibility = "org"
        connector.org_id = body.org_id
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
            from fim_one.web.auth import require_org_admin
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


@router.post("/{connector_id}/toggle", response_model=ApiResponse)
async def toggle_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Toggle connector status between published and suspended."""
    result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = result.scalar_one_or_none()
    if connector is None:
        raise AppError("connector_not_found", status_code=404)
    if connector.user_id != current_user.id:
        raise AppError("permission_denied", status_code=403)

    connector.status = "suspended" if connector.status == "published" else "published"
    await db.commit()
    return ApiResponse(data={"id": connector_id, "status": connector.status})
