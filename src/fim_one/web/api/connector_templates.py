"""Connector template endpoints — list built-in templates and create connectors from them.

These endpoints mirror the workflow template pattern: templates are hardcoded
in ``fim_one.core.tool.connector.template_seeds`` (not stored in the DB).
Creating from a template inserts a real ``Connector`` row with its
``ConnectorAction`` children pre-populated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.user import User
from fim_one.web.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connector-templates", tags=["connector-templates"])


@router.get("", response_model=ApiResponse)
async def list_connector_templates(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    """Return all built-in connector templates."""
    from fim_one.core.tool.connector.template_seeds import list_templates

    return ApiResponse(data=list_templates())


@router.post("/{template_id}/create", response_model=ApiResponse)
async def create_connector_from_template(
    template_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new connector (with actions) from a built-in template."""
    from fim_one.core.tool.connector.template_seeds import get_template

    template = get_template(template_id)
    if not template:
        raise AppError("template_not_found", status_code=404)

    bp = template["blueprint"]

    connector = Connector(
        user_id=current_user.id,
        name=template["name"],
        description=template.get("description"),
        icon=None,  # template icon is a Lucide name for gallery, not an emoji
        type=bp.get("type", "api"),
        base_url=bp.get("base_url"),
        auth_type=bp.get("auth_type", "none"),
        auth_config=bp.get("auth_config"),
        is_active=True,
    )
    db.add(connector)
    await db.flush()  # get connector.id before creating actions

    # Create template actions
    for action_data in bp.get("actions", []):
        action = ConnectorAction(
            connector_id=connector.id,
            name=action_data["name"],
            description=action_data.get("description"),
            method=action_data.get("method", "GET"),
            path=action_data.get("path", "/"),
            parameters_schema=action_data.get("parameters_schema"),
            request_body_template=action_data.get("request_body_template"),
            response_extract=action_data.get("response_extract"),
        )
        db.add(action)

    await db.commit()

    # Re-fetch with actions eagerly loaded
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()

    from fim_one.web.api.connectors import _connector_to_response

    return ApiResponse(data=_connector_to_response(connector).model_dump())
