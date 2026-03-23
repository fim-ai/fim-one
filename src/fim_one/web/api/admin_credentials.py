"""Admin endpoints for credential dashboard — cross-user credential visibility."""

from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_admin
from fim_one.web.exceptions import AppError
from fim_one.web.models import Connector, MCPServer as MCPServerModel, User
from fim_one.web.models.connector_credential import ConnectorCredential
from fim_one.web.models.mcp_server_credential import MCPServerCredential
from fim_one.web.schemas.common import PaginatedResponse

from fim_one.web.api.admin_utils import write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminCredentialInfo(BaseModel):
    id: str
    resource_type: str  # "connector" or "mcp"
    resource_id: str
    resource_name: str | None = None
    user_id: str | None = None
    username: str | None = None
    email: str | None = None
    updated_at: str | None = None
    created_at: str | None = None


class CredentialStatsResponse(BaseModel):
    total_credentials: int
    connector_credentials: int
    mcp_credentials: int
    users_with_credentials: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/credentials", response_model=PaginatedResponse)
async def list_all_credentials(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    resource_type: str | None = Query(None, pattern="^(connector|mcp)$"),
    user_id: str | None = Query(None),
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    """List all user credentials across the system. Requires admin privileges."""
    items: list[dict[str, object]] = []
    total = 0

    # Fetch connector credentials
    if resource_type is None or resource_type == "connector":
        cc_stmt = (
            select(
                ConnectorCredential,
                Connector.name.label("resource_name"),
                User.username,
                User.email,
            )
            .join(Connector, ConnectorCredential.connector_id == Connector.id)
            .outerjoin(User, ConnectorCredential.user_id == User.id)
        )
        cc_count_base = select(ConnectorCredential)

        if user_id:
            cc_stmt = cc_stmt.where(ConnectorCredential.user_id == user_id)
            cc_count_base = cc_count_base.where(ConnectorCredential.user_id == user_id)

        cc_count = (
            await db.execute(
                select(func.count()).select_from(cc_count_base.subquery())
            )
        ).scalar_one()

        cc_rows = (
            await db.execute(
                cc_stmt.order_by(ConnectorCredential.updated_at.desc())
            )
        ).all()

        for row in cc_rows:
            cred = row[0]
            items.append(
                AdminCredentialInfo(
                    id=cred.id,
                    resource_type="connector",
                    resource_id=cred.connector_id,
                    resource_name=row.resource_name,
                    user_id=cred.user_id,
                    username=row.username,
                    email=row.email,
                    updated_at=cred.updated_at.isoformat() if cred.updated_at else None,
                    created_at=cred.created_at.isoformat() if cred.created_at else None,
                ).model_dump()
            )
        total += cc_count

    # Fetch MCP credentials
    if resource_type is None or resource_type == "mcp":
        mc_stmt = (
            select(
                MCPServerCredential,
                MCPServerModel.name.label("resource_name"),
                User.username,
                User.email,
            )
            .join(MCPServerModel, MCPServerCredential.server_id == MCPServerModel.id)
            .outerjoin(User, MCPServerCredential.user_id == User.id)
        )
        mc_count_base = select(MCPServerCredential)

        if user_id:
            mc_stmt = mc_stmt.where(MCPServerCredential.user_id == user_id)
            mc_count_base = mc_count_base.where(MCPServerCredential.user_id == user_id)

        mc_count = (
            await db.execute(
                select(func.count()).select_from(mc_count_base.subquery())
            )
        ).scalar_one()

        mc_rows = (
            await db.execute(
                mc_stmt.order_by(MCPServerCredential.updated_at.desc())
            )
        ).all()

        for mc_row in mc_rows:
            cred = mc_row[0]
            items.append(
                AdminCredentialInfo(
                    id=cred.id,
                    resource_type="mcp",
                    resource_id=cred.server_id,
                    resource_name=mc_row.resource_name,
                    user_id=cred.user_id,
                    username=mc_row.username,
                    email=mc_row.email,
                    updated_at=cred.updated_at.isoformat() if cred.updated_at else None,
                    created_at=cred.created_at.isoformat() if cred.created_at else None,
                ).model_dump()
            )
        total += mc_count

    # Sort combined by created_at descending, then paginate in-memory
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    offset = (page - 1) * size
    paginated_items = items[offset : offset + size]

    return PaginatedResponse(
        items=paginated_items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 1,
    )


@router.delete("/credentials/connector/{credential_id}", status_code=204)
async def revoke_connector_credential(
    credential_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Revoke a connector credential (admin emergency revocation)."""
    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.id == credential_id)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise AppError("credential_not_found", status_code=404)

    cred_id = cred.id
    connector_id = cred.connector_id
    await db.delete(cred)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "credential.admin_revoke",
        target_type="connector_credential",
        target_id=cred_id,
        detail=f"connector_id={connector_id}",
    )


@router.delete("/credentials/mcp/{credential_id}", status_code=204)
async def revoke_mcp_credential(
    credential_id: str,
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Revoke an MCP server credential (admin emergency revocation)."""
    result = await db.execute(
        select(MCPServerCredential).where(MCPServerCredential.id == credential_id)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise AppError("credential_not_found", status_code=404)

    cred_id = cred.id
    server_id = cred.server_id
    await db.delete(cred)
    await db.commit()

    await write_audit(
        db,
        current_user,
        "credential.admin_revoke",
        target_type="mcp_credential",
        target_id=cred_id,
        detail=f"server_id={server_id}",
    )


@router.get("/credentials/stats", response_model=CredentialStatsResponse)
async def credential_stats(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> CredentialStatsResponse:
    """Summary credential stats. Requires admin privileges."""
    connector_count = (
        await db.execute(select(func.count()).select_from(ConnectorCredential))
    ).scalar_one()

    mcp_count = (
        await db.execute(select(func.count()).select_from(MCPServerCredential))
    ).scalar_one()

    # Count distinct users with any credential
    cc_users = select(ConnectorCredential.user_id).where(
        ConnectorCredential.user_id.isnot(None)
    )
    mc_users = select(MCPServerCredential.user_id).where(
        MCPServerCredential.user_id.isnot(None)
    )
    all_user_ids = union_all(cc_users, mc_users).subquery()
    users_with_creds = (
        await db.execute(
            select(func.count(func.distinct(all_user_ids.c.user_id)))
        )
    ).scalar_one()

    return CredentialStatsResponse(
        total_credentials=connector_count + mcp_count,
        connector_credentials=connector_count,
        mcp_credentials=mcp_count,
        users_with_credentials=users_with_creds,
    )
