"""Connector management API."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import httpx
import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.core.security import get_safe_async_client, validate_url
from fim_one.core.tool.connector.circuit_breaker import get_circuit_breaker_registry
from fim_one.core.tool.connector.openapi_parser import parse_openapi_spec
from fim_one.core.tool.connector.semantic_tags import get_all_semantic_tags
from fim_one.web.exceptions import AppError
from fim_one.db import get_session
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.web.models.connector import Connector, ConnectorAction
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.schemas.connector import (
    ActionCreate,
    ActionExportData,
    ActionResponse,
    ActionUpdate,
    ConnectorCreate,
    ConnectorExportData,
    ConnectorExportMeta,
    ConnectorForkRequest,
    ConnectorFromConfigRequest,
    ConnectorImportRequest,
    ConnectorImportResult,
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


def _mask_db_config(db_config: dict[str, Any] | None) -> dict[str, Any] | None:
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
    auth_type: str, auth_config: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    auth_type: str, auth_config: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return auth_config with sensitive credential fields removed (for API responses)."""
    if not auth_config:
        return auth_config
    sensitive = _AUTH_SENSITIVE_FIELDS.get(auth_type, [])
    return {k: v for k, v in auth_config.items() if k not in sensitive}


async def _upsert_default_credential(
    connector_id: str, cred_blob: dict[str, Any], db: AsyncSession
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
        is_active=getattr(connector, "is_active", True),
        visibility=getattr(connector, "visibility", "personal"),
        org_id=getattr(connector, "org_id", None),
        allow_fallback=getattr(connector, "allow_fallback", True),
        has_default_credentials=has_default_credentials,
        publish_status=getattr(connector, "publish_status", None),
        reviewed_by=getattr(connector, "reviewed_by", None),
        reviewed_at=(
            rev_at.isoformat()
            if (rev_at := getattr(connector, "reviewed_at", None))
            else None
        ),
        review_note=getattr(connector, "review_note", None),
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

    # Get subscribed connector IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "connector",
        )
    )
    sub_rows = sub_result.all()
    subscribed_connector_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(Connector).where(
        build_visibility_filter(Connector, current_user.id, user_org_ids, subscribed_ids=subscribed_connector_ids)
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

    subscribed_connector_ids_set = set(subscribed_connector_ids)
    items = []
    for c in connectors:
        resp = _connector_to_response(c)
        if c.user_id == current_user.id:
            resp.source = "own"
        elif c.id in subscribed_connector_ids_set:
            sub_org_id = sub_org_map.get(c.id)
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


# ---------------------------------------------------------------------------
# Semantic Tags
# ---------------------------------------------------------------------------


@router.get("/semantic-tags", response_model=ApiResponse)
async def list_semantic_tags(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Return the list of available semantic tags with descriptions."""
    return ApiResponse(data=get_all_semantic_tags())


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

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(connector, db)
    else:
        reverted = False

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    has_creds = await _has_default_credential(connector.id, db)
    data = _connector_to_response(connector, has_default_credentials=has_creds).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


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
# Fork (clone)
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/fork", response_model=ApiResponse)
async def fork_connector(
    connector_id: str,
    body: ConnectorForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clone an existing connector for the current user.

    Copies all configuration fields but NOT credentials, org_id, or
    publish_status.  The forked connector is set to personal/draft ownership.
    """
    # Fetch the source connector — must be visible to the user (own / org / global)
    source = await _get_visible_connector(connector_id, current_user.id, db)

    fork_name = (body.name if body and body.name else f"{source.name} (Copy)")[:200]

    forked = Connector(
        user_id=current_user.id,
        name=fork_name,
        description=source.description,
        icon=source.icon,
        type=source.type,
        base_url=source.base_url,
        auth_type=source.auth_type,
        auth_config=source.auth_config,
        db_config=None,  # credentials — do NOT copy
        status="draft",
        is_official=False,
        forked_from=source.id,
        version=1,
        visibility="personal",
        org_id=None,
        publish_status=None,
        allow_fallback=source.allow_fallback,
        is_active=True,
    )
    db.add(forked)
    await db.flush()  # get forked.id

    # Clone actions
    for action in source.actions or []:
        cloned_action = ConnectorAction(
            connector_id=forked.id,
            name=action.name,
            description=action.description,
            method=action.method,
            path=action.path,
            parameters_schema=action.parameters_schema,
            request_body_template=action.request_body_template,
            response_extract=action.response_extract,
            requires_confirmation=action.requires_confirmation,
        )
        db.add(cloned_action)

    await db.commit()

    # Reload with actions for response
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == forked.id)
    )
    forked = result.scalar_one()
    return ApiResponse(data=_connector_to_response(forked).model_dump())


# ---------------------------------------------------------------------------
# Resubmit
# ---------------------------------------------------------------------------


@router.post("/{connector_id}/resubmit", response_model=ApiResponse)
async def resubmit_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected connector for review."""
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    if connector.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    connector.publish_status = "pending_review"
    connector.reviewed_by = None
    connector.reviewed_at = None
    connector.review_note = None

    if connector.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=connector.org_id,
            resource_type="connector",
            resource_id=connector.id,
            resource_name=connector.name,
            action="resubmitted",
            actor=current_user,
        )

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
    cred_blob: dict[str, Any] = {}
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
        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        connector.visibility = "org"
        connector.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(connector, body.org_id, db, resource_type="connector", publisher_id=current_user.id)

        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="connector",
            resource_id=connector.id,
            resource_name=connector.name,
            action="submitted",
            actor=current_user,
        )
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
            from fim_one.web.auth import require_org_admin
            await require_org_admin(connector.org_id, current_user, db)
            is_org_admin = True
        except Exception:
            pass

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id
    if getattr(connector, "org_id", None):
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=connector.org_id or "",
            resource_type="connector",
            resource_id=connector.id,
            resource_name=connector.name,
            action="unpublished",
            actor=current_user,
        )

    connector.visibility = "personal"
    connector.org_id = None
    connector.publish_status = None

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
# Export / Import
# ---------------------------------------------------------------------------


@router.get("/{connector_id}/export", response_model=ApiResponse)
async def export_connector(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Export a connector configuration as a portable JSON structure.

    Sensitive data (credentials, user_id, org_id) is stripped. The exported
    JSON can be shared and re-imported into any FIM One instance.
    """
    from datetime import UTC, datetime

    connector = await _get_visible_connector(connector_id, current_user.id, db)

    actions_data = [
        ActionExportData(
            name=a.name,
            description=a.description,
            method=a.method,
            path=a.path,
            parameters_schema=a.parameters_schema,
            request_body_template=a.request_body_template,
            response_extract=a.response_extract,
            requires_confirmation=a.requires_confirmation,
        )
        for a in (connector.actions or [])
    ]

    # Strip sensitive fields from auth_config for export
    clean_auth_config = _strip_sensitive_auth_config(connector.auth_type, connector.auth_config)

    export_data = ConnectorExportData.model_construct(
        name=connector.name,
        description=connector.description,
        icon=connector.icon,
        connector_type=connector.type,
        base_url=connector.base_url,
        auth_type=connector.auth_type,
        auth_config=clean_auth_config,
        actions=actions_data,
        _meta=ConnectorExportMeta(
            exported_at=datetime.now(UTC).isoformat(),
        ),
    )
    return ApiResponse(data=export_data.model_dump())


@router.post("/import", response_model=ApiResponse)
async def import_connector(
    body: ConnectorImportRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Import a connector from exported JSON.

    Creates a new connector assigned to the current user with ``status='draft'``
    and empty credentials. The ``warnings`` list in the response tells the user
    which fields need manual configuration (e.g. credentials, base_url).
    """
    connector = Connector(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        type=body.connector_type,
        base_url=body.base_url,
        auth_type=body.auth_type,
        auth_config=body.auth_config,
        status="draft",
    )
    db.add(connector)
    await db.flush()  # get connector.id

    # Create actions from the import payload
    for action_data in body.actions:
        action = ConnectorAction(
            connector_id=connector.id,
            name=action_data.name,
            description=action_data.description,
            method=action_data.method,
            path=action_data.path,
            parameters_schema=action_data.parameters_schema,
            request_body_template=action_data.request_body_template,
            response_extract=action_data.response_extract,
            requires_confirmation=action_data.requires_confirmation,
        )
        db.add(action)

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()

    # Build warnings for fields that need user attention
    warnings: list[str] = []
    if body.auth_type and body.auth_type != "none":
        warnings.append("credentials")
    if body.connector_type == "api" and not body.base_url:
        warnings.append("base_url")
    if body.connector_type == "database":
        warnings.append("db_config")

    import_result = ConnectorImportResult(
        connector=_connector_to_response(connector),
        warnings=warnings,
    )
    return ApiResponse(data=import_result.model_dump())


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

    connector.is_active = not connector.is_active
    await db.commit()
    return ApiResponse(data={"id": connector_id, "is_active": connector.is_active})


# ---------------------------------------------------------------------------
# Config Import (YAML / JSON declarative config)
# ---------------------------------------------------------------------------


@router.post("/from-config", response_model=ApiResponse)
async def create_connector_from_config(
    body: ConnectorFromConfigRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a connector + actions from a YAML or JSON config file."""
    from fim_one.core.tool.connector.config_loader import (
        ConfigValidationError,
        config_to_connector,
        parse_connector_config,
    )

    try:
        config = parse_connector_config(body.config, format=body.format)
    except ConfigValidationError as exc:
        raise AppError(
            "config_validation_failed",
            status_code=422,
            detail="; ".join(exc.errors),
            detail_args={"errors": exc.errors},
        ) from exc

    connector, actions = config_to_connector(config, user_id=current_user.id)
    db.add(connector)
    for action in actions:
        db.add(action)

    await db.commit()

    # Reload with actions relationship for response serialization
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()
    return ApiResponse(data=_connector_to_response(connector).model_dump())


@router.get("/config-template", response_model=ApiResponse)
async def get_connector_config_template(
    format: str = Query("yaml", pattern=r"^(yaml|json)$"),
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Return a starter connector config template in YAML or JSON format."""
    from fim_one.core.tool.connector.config_loader import (
        get_config_template as _get_template,
    )

    template = _get_template(format=format)
    return ApiResponse(data={"template": template, "format": format})


# ---------------------------------------------------------------------------
# Circuit breaker status
# ---------------------------------------------------------------------------


@router.get("/circuit-breaker-status", response_model=ApiResponse)
async def get_circuit_breaker_status(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Return the state of all per-connector circuit breakers.

    Useful for monitoring which external services are experiencing
    failures and when recovery probes are scheduled.
    """
    registry = await get_circuit_breaker_registry()
    return ApiResponse(data={"breakers": registry.get_status()})


@router.post("/{connector_id}/circuit-breaker-reset", response_model=ApiResponse)
async def reset_circuit_breaker(
    connector_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Manually reset a circuit breaker for a connector to CLOSED state."""
    registry = await get_circuit_breaker_registry()
    existed = await registry.reset(connector_id)
    return ApiResponse(
        data={"connector_id": connector_id, "reset": existed},
    )
