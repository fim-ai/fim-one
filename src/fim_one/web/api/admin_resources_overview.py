"""Admin endpoint for unified resource lifecycle overview."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.web.auth import get_current_admin
from fim_one.web.models import (
    Agent,
    Connector,
    KnowledgeBase,
    MCPServer as MCPServerModel,
    Skill,
    User,
    Workflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ResourceTypeSummary(BaseModel):
    resource_type: str
    total: int = 0
    active: int = 0
    inactive: int = 0
    stale_count: int = 0  # updated_at older than 30 days


class ResourceOverviewResponse(BaseModel):
    resources: list[ResourceTypeSummary] = Field(default_factory=list)
    total_resources: int = 0
    total_active: int = 0
    total_inactive: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Models that have is_active field
_ACTIVE_MODELS = {
    "agent": Agent,
    "connector": Connector,
    "knowledge_base": KnowledgeBase,
    "mcp_server": MCPServerModel,
    "skill": Skill,
    "workflow": Workflow,
}


async def _count_resource_type(
    db: AsyncSession,
    model: type[Base],
    resource_type: str,
    stale_cutoff: datetime,
) -> ResourceTypeSummary:
    """Count total, active, inactive, and stale resources for a given model."""
    total = (
        await db.execute(select(func.count()).select_from(model))
    ).scalar_one()

    active = 0
    inactive = 0
    if hasattr(model, "is_active"):
        active = (
            await db.execute(
                select(func.count())
                .select_from(model)
                .where(model.is_active == True)  # noqa: E712
            )
        ).scalar_one()
        inactive = total - active
    else:
        active = total

    # Stale: updated_at older than cutoff
    stale = 0
    if hasattr(model, "updated_at"):
        stale = (
            await db.execute(
                select(func.count())
                .select_from(model)
                .where(model.updated_at < stale_cutoff)
            )
        ).scalar_one()

    return ResourceTypeSummary(
        resource_type=resource_type,
        total=total,
        active=active,
        inactive=inactive,
        stale_count=stale,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/resources/overview", response_model=ResourceOverviewResponse)
async def resource_overview(
    current_user: User = Depends(get_current_admin),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ResourceOverviewResponse:
    """Unified cross-type resource overview. Requires admin privileges."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    resources: list[ResourceTypeSummary] = []
    total_resources = 0
    total_active = 0
    total_inactive = 0

    for rtype, model in _ACTIVE_MODELS.items():
        summary = await _count_resource_type(db, model, rtype, stale_cutoff)
        resources.append(summary)
        total_resources += summary.total
        total_active += summary.active
        total_inactive += summary.inactive

    return ResourceOverviewResponse(
        resources=resources,
        total_resources=total_resources,
        total_active=total_active,
        total_inactive=total_inactive,
    )
