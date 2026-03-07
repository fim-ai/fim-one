"""AI-powered action generation and refinement for connectors."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.core.model.types import ChatMessage
from fim_agent.core.utils import get_language_directive
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.deps import get_fast_llm
from fim_agent.web.models.connector import Connector, ConnectorAction
from fim_agent.web.models.user import User
from fim_agent.web.schemas.common import ApiResponse
from fim_agent.web.schemas.connector import (
    AIActionResult,
    AICreateConnectorRequest,
    AICreateConnectorResult,
    AIGenerateActionsRequest,
    AIRefineActionRequest,
    ActionResponse,
    ConnectorResponse,
    OpenAPIImportRequest,
)
from fim_agent.web.api.connectors import _get_owned_connector, _action_to_response, _connector_to_response, _resolve_openapi_spec
from fim_agent.web.exceptions import AppError
from fim_agent.core.tool.connector.openapi_parser import parse_openapi_spec

router = APIRouter(prefix="/api/connectors", tags=["connector-ai"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM Helpers
# ---------------------------------------------------------------------------

_GENERATE_SYSTEM_PROMPT = """\
You are an API action designer. Given a connector description and a user instruction, \
generate a JSON array of API action definitions.

Each action object MUST have these fields:
- "name": string — unique action name (snake_case, e.g. "list_users")
- "description": string — what the action does
- "method": string — HTTP method (GET, POST, PUT, PATCH, DELETE)
- "path": string — relative API path (e.g. "/users/{user_id}")
- "parameters_schema": object|null — JSON Schema describing path/query parameters
- "requires_confirmation": boolean — true for destructive actions (DELETE, mutations)

Optional fields:
- "request_body_template": object|null — template for request body
- "response_extract": string|null — JMESPath expression to extract response data

Output ONLY a valid JSON array. No markdown, no commentary."""

_REFINE_SYSTEM_PROMPT = """\
You are an API connector and action editor. Given the current connector configuration, \
its existing actions, and a user instruction, output a JSON array of operations to apply.

Each operation object MUST have:
- "op": "create" | "update" | "delete" | "update_connector"

For "create" (create a new action):
- "data": object with action fields (name, description, method, path, parameters_schema, \
requires_confirmation, and optionally request_body_template, response_extract)

For "update" (update an existing action):
- "action_id": string — the ID of the action to update
- "data": object with fields to change (partial update)

For "delete" (delete an existing action):
- "action_id": string — the ID of the action to delete

For "update_connector" (update connector settings):
- "data": object with connector fields to change. Allowed fields: \
name, icon, description, base_url, auth_type, auth_config
- icon is a single emoji that represents this connector (e.g. "🐙", "📮", "🔍")
- auth_type must be one of: "none", "bearer", "api_key", "basic"
- auth_config is a JSON object whose shape depends on auth_type:
  - bearer: {"token": "..."}
  - api_key: {"key": "...", "header": "X-API-Key"} (header is optional, defaults to X-API-Key)
  - basic: {"username": "...", "password": "..."}
  - none: null or omit

Examples:
- User says "change the icon to 🚀" → [{"op": "update_connector", "data": {"icon": "🚀"}}]
- User says "rename to Weather API" → [{"op": "update_connector", "data": {"name": "Weather API"}}]
- User says "add a search endpoint" → [{"op": "create", "data": {"name": "search", ...}}]

IMPORTANT: To change connector-level properties (name, icon, description, base_url, auth), \
you MUST use "update_connector" — NOT "update" (which is for actions only).

Output ONLY a valid JSON array of operations. No markdown, no commentary."""

_CREATE_SYSTEM_PROMPT = """\
You are an API connector creation assistant. Analyze the user's instruction and determine the best approach:

1. If the user provides an OpenAPI/Swagger spec URL → output:
   {"mode": "openapi_import", "url": "<the URL>"}

2. Otherwise, generate the connector configuration from the description → output:
   {"mode": "generate", "connector": {"name": "...", "icon": "...", "description": "...", "base_url": "...", "auth_type": "none", "auth_config": null}, "actions": [<array of action objects>]}
   The connector "name" should be plain text without emoji (e.g. "GitHub", "Slack API", "Google Search").
   The connector "icon" is a single emoji that represents this connector (e.g. "🐙", "📮", "🔍").

For generated actions, each action object MUST have:
- "name": string (snake_case)
- "description": string
- "method": string (GET/POST/PUT/PATCH/DELETE)
- "path": string (relative path)
- "parameters_schema": object|null
- "requires_confirmation": boolean (true for destructive actions)

Optional action fields:
- "request_body_template": object|null
- "response_extract": string|null

auth_type must be one of: "none", "bearer", "api_key", "basic"
auth_config shape depends on auth_type:
  - bearer: {"token": "..."}
  - api_key: {"key": "...", "header": "X-API-Key"} (header is optional, defaults to X-API-Key)
  - basic: {"username": "...", "password": "..."}
  - none: null or omit

Choose the appropriate auth_type based on what the API typically uses. \
If the user mentions a token or API key, set the auth_type accordingly and leave \
placeholder values (e.g. "your-api-key-here") in auth_config for the user to fill in.

Output ONLY valid JSON. No markdown, no commentary."""


def _build_connector_context(connector: Connector) -> str:
    """Build a context string describing the connector and its existing actions."""
    lines = [
        f"Connector: {connector.name}",
        f"Icon: {connector.icon or 'N/A'}",
        f"Description: {connector.description or 'N/A'}",
        f"Base URL: {connector.base_url}",
        f"Auth: {connector.auth_type}",
    ]
    if connector.auth_config:
        lines.append(f"Auth Config: {json.dumps(connector.auth_config)}")
    if connector.actions:
        lines.append(f"\nExisting actions ({len(connector.actions)}):")
        for a in connector.actions:
            lines.append(
                f"  - [{a.id}] {a.name} ({a.method} {a.path}): "
                f"{a.description or 'no description'}"
            )
    else:
        lines.append("\nNo existing actions.")
    return "\n".join(lines)


def _extract_json(text: str) -> Any:
    """Extract JSON array from LLM response, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (possibly with language tag)
        first_newline = text.index("\n")
        text = text[first_newline + 1 :]
        # Remove closing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    return json.loads(text)


async def _llm_call(
    system: str,
    user_msg: str,
    retry_context: str | None = None,
    *,
    language_directive: str | None = None,
) -> Any:
    """Call the fast LLM and parse JSON from response. Auto-retries once on parse failure."""
    llm = get_fast_llm()
    sys_content = system
    if language_directive:
        sys_content += (
            f"\n\n{language_directive} "
            "This applies to natural-language fields only. "
            "Keep JSON keys and technical fields in English."
        )
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=sys_content),
        ChatMessage(role="user", content=user_msg),
    ]
    if retry_context:
        messages.append(ChatMessage(role="user", content=retry_context))

    result = await llm.chat(messages=messages)
    content = result.message.content or ""
    if not content:
        raise AppError("llm_empty_response", status_code=422)

    try:
        return _extract_json(content)
    except (json.JSONDecodeError, ValueError) as exc:
        if retry_context is not None:
            # Already retried once — give up
            raise AppError(
                "llm_invalid_json",
                status_code=422,
                detail=f"LLM returned invalid JSON after retry: {exc}",
            ) from exc
        # Auto-retry with error feedback
        logger.warning("LLM JSON parse failed, retrying: %s", exc)
        feedback = (
            f"Your previous response was not valid JSON. Error: {exc}\n"
            "Please output ONLY a valid JSON array, no markdown or commentary."
        )
        return await _llm_call(system, user_msg, retry_context=feedback, language_directive=language_directive)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ai/create", response_model=ApiResponse)
async def ai_create_connector(
    body: AICreateConnectorRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new connector using AI from natural language or OpenAPI URL."""
    lang_directive = get_language_directive(current_user.preferred_language)
    plan = await _llm_call(_CREATE_SYSTEM_PROMPT, body.instruction, language_directive=lang_directive)
    if not isinstance(plan, dict):
        raise AppError("llm_unexpected_format", status_code=422)

    mode = plan.get("mode")

    if mode == "openapi_import":
        url = plan.get("url", "")
        if not url:
            raise AppError("openapi_url_missing", status_code=400)
        spec = await _resolve_openapi_spec(OpenAPIImportRequest(spec_url=url))
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
        await db.flush()

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
        msg = f"Imported {len(action_dicts)} action(s) from OpenAPI spec."
        msg_key = "ai_connector_imported"
        msg_args: dict = {"count": len(action_dicts)}

    elif mode == "generate":
        conn_data = plan.get("connector", {})
        conn_icon = conn_data.get("icon")
        if isinstance(conn_icon, str):
            conn_icon = conn_icon[:100]
        connector = Connector(
            user_id=current_user.id,
            name=str(conn_data.get("name", "New Connector"))[:200],
            icon=conn_icon,
            description=conn_data.get("description"),
            type="api",
            base_url=str(conn_data.get("base_url", "https://api.example.com")),
            auth_type=str(conn_data.get("auth_type", "none")),
            auth_config=conn_data.get("auth_config"),
            status="published",
        )
        db.add(connector)
        await db.flush()

        actions_data = plan.get("actions", [])
        created_count = 0
        for item in actions_data:
            try:
                action = ConnectorAction(
                    connector_id=connector.id,
                    name=str(item["name"]),
                    description=item.get("description"),
                    method=str(item.get("method", "GET")).upper(),
                    path=str(item["path"]),
                    parameters_schema=item.get("parameters_schema"),
                    request_body_template=item.get("request_body_template"),
                    response_extract=item.get("response_extract"),
                    requires_confirmation=bool(item.get("requires_confirmation", False)),
                )
                db.add(action)
                created_count += 1
            except Exception as exc:
                logger.warning("Failed to create action from AI output: %s", exc)

        await db.commit()
        msg = f"Created connector with {created_count} action(s)."
        msg_key = "ai_connector_created"
        msg_args = {"count": created_count}

    else:
        raise AppError(
            "llm_unexpected_format",
            status_code=422,
            detail=f"LLM returned unknown mode: {mode}",
        )

    # Reload with actions
    result = await db.execute(
        select(Connector)
        .options(selectinload(Connector.actions))
        .where(Connector.id == connector.id)
    )
    connector = result.scalar_one()

    return ApiResponse(
        data=AICreateConnectorResult(
            connector=_connector_to_response(connector),
            message=msg,
            message_key=msg_key,
            message_args=msg_args,
        ).model_dump()
    )


@router.post("/{connector_id}/ai/generate-actions", response_model=ApiResponse)
async def ai_generate_actions(
    connector_id: str,
    body: AIGenerateActionsRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Generate new actions for a connector using AI."""
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    lang_directive = get_language_directive(current_user.preferred_language)

    connector_ctx = _build_connector_context(connector)
    user_msg = f"{connector_ctx}\n\nUser instruction: {body.instruction}"
    if body.context:
        user_msg += f"\n\nAPI documentation context:\n{body.context}"

    raw_actions = await _llm_call(_GENERATE_SYSTEM_PROMPT, user_msg, language_directive=lang_directive)
    if not isinstance(raw_actions, list):
        raise AppError("llm_unexpected_format", status_code=422)

    created: list[ActionResponse] = []
    failed: list[str] = []

    for i, item in enumerate(raw_actions):
        try:
            # Validate required fields
            name = str(item["name"])
            method = str(item.get("method", "GET")).upper()
            path = str(item["path"])

            action = ConnectorAction(
                connector_id=connector_id,
                name=name,
                description=item.get("description"),
                method=method,
                path=path,
                parameters_schema=item.get("parameters_schema"),
                request_body_template=item.get("request_body_template"),
                response_extract=item.get("response_extract"),
                requires_confirmation=bool(item.get("requires_confirmation", False)),
            )
            db.add(action)
            await db.flush()
            # Re-query to get server-generated defaults
            result = await db.execute(
                select(ConnectorAction).where(ConnectorAction.id == action.id)
            )
            action = result.scalar_one()
            created.append(_action_to_response(action))
        except Exception as exc:
            failed.append(f"Action #{i}: {exc}")
            logger.warning("Failed to create action #%d: %s", i, exc)

    await db.commit()

    parts = [f"Created {len(created)} action(s)."]
    if failed:
        parts.append(f"{len(failed)} action(s) failed validation.")

    result = AIActionResult(
        created=created,
        failed=failed,
        message=" ".join(parts),
        message_key="ai_actions_generated",
        message_args={"created": len(created), "failed": len(failed)},
    )
    return ApiResponse(data=result.model_dump())


@router.post("/{connector_id}/ai/refine-action", response_model=ApiResponse)
async def ai_refine_action(
    connector_id: str,
    body: AIRefineActionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Refine connector actions using AI instructions."""
    connector = await _get_owned_connector(connector_id, current_user.id, db)
    lang_directive = get_language_directive(current_user.preferred_language)

    connector_ctx = _build_connector_context(connector)
    user_msg = f"{connector_ctx}\n\nUser instruction: {body.instruction}"
    if body.action_id:
        # Find the target action for extra context
        target = next(
            (a for a in connector.actions if a.id == body.action_id), None
        )
        if target is None:
            raise AppError("action_not_found", status_code=404)
        user_msg += (
            f"\n\nTarget action: [{target.id}] {target.name} "
            f"({target.method} {target.path})"
        )

    operations = await _llm_call(_REFINE_SYSTEM_PROMPT, user_msg, language_directive=lang_directive)
    if not isinstance(operations, list):
        raise AppError("llm_unexpected_format", status_code=422)

    created: list[ActionResponse] = []
    updated: list[ActionResponse] = []
    deleted: list[str] = []
    failed: list[str] = []
    connector_changed = False

    for i, op_item in enumerate(operations):
        try:
            op = str(op_item.get("op", "")).lower()

            if op == "create":
                data = op_item["data"]
                action = ConnectorAction(
                    connector_id=connector_id,
                    name=str(data["name"]),
                    description=data.get("description"),
                    method=str(data.get("method", "GET")).upper(),
                    path=str(data["path"]),
                    parameters_schema=data.get("parameters_schema"),
                    request_body_template=data.get("request_body_template"),
                    response_extract=data.get("response_extract"),
                    requires_confirmation=bool(data.get("requires_confirmation", False)),
                )
                db.add(action)
                await db.flush()
                # Re-query to get server-generated defaults
                result = await db.execute(
                    select(ConnectorAction).where(ConnectorAction.id == action.id)
                )
                action = result.scalar_one()
                created.append(_action_to_response(action))

            elif op == "update":
                action_id = str(op_item["action_id"])
                result = await db.execute(
                    select(ConnectorAction).where(
                        ConnectorAction.id == action_id,
                        ConnectorAction.connector_id == connector_id,
                    )
                )
                action = result.scalar_one_or_none()
                if action is None:
                    failed.append(f"Op #{i}: action {action_id} not found for update")
                    continue

                data = op_item.get("data", {})
                updatable = {
                    "name", "description", "method", "path",
                    "parameters_schema", "request_body_template",
                    "response_extract", "requires_confirmation",
                }
                for field, value in data.items():
                    if field in updatable:
                        if field == "method":
                            value = str(value).upper()
                        setattr(action, field, value)

                await db.flush()
                # Re-query to get updated values
                result = await db.execute(
                    select(ConnectorAction).where(ConnectorAction.id == action.id)
                )
                action = result.scalar_one()
                updated.append(_action_to_response(action))

            elif op == "delete":
                action_id = str(op_item["action_id"])
                result = await db.execute(
                    select(ConnectorAction).where(
                        ConnectorAction.id == action_id,
                        ConnectorAction.connector_id == connector_id,
                    )
                )
                action = result.scalar_one_or_none()
                if action is None:
                    failed.append(f"Op #{i}: action {action_id} not found for delete")
                    continue
                await db.delete(action)
                deleted.append(action_id)

            elif op == "update_connector":
                data = op_item.get("data", {})
                connector_updatable = {
                    "name", "icon", "description", "base_url",
                    "auth_type", "auth_config",
                }
                for field, value in data.items():
                    if field in connector_updatable:
                        setattr(connector, field, value)
                await db.flush()
                connector_changed = True

            else:
                failed.append(f"Op #{i}: unknown operation '{op}'")
        except Exception as exc:
            failed.append(f"Op #{i}: {exc}")
            logger.warning("Failed to execute operation #%d: %s", i, exc)

    await db.commit()

    # Build connector_updated response if connector settings were changed
    connector_updated_resp = None
    if connector_changed:
        result = await db.execute(
            select(Connector)
            .options(selectinload(Connector.actions))
            .where(Connector.id == connector.id)
        )
        connector = result.scalar_one()
        connector_updated_resp = _connector_to_response(connector)

    parts: list[str] = []
    if connector_changed:
        parts.append("Updated connector settings.")
    if created:
        parts.append(f"Created {len(created)} action(s).")
    if updated:
        parts.append(f"Updated {len(updated)} action(s).")
    if deleted:
        parts.append(f"Deleted {len(deleted)} action(s).")
    if failed:
        parts.append(f"{len(failed)} operation(s) failed.")
    if not parts:
        parts.append("No operations were performed.")

    ai_result = AIActionResult(
        created=created,
        updated=updated,
        deleted=deleted,
        failed=failed,
        connector_updated=connector_updated_resp,
        message=" ".join(parts),
        message_key="ai_connector_refined",
        message_args={
            "created": len(created),
            "updated": len(updated),
            "deleted": len(deleted),
            "failed": len(failed),
            "connector_changed": connector_changed,
        },
    )
    return ApiResponse(data=ai_result.model_dump())
