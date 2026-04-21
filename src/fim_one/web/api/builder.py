"""Builder Session API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session
from fim_one.web.auth import get_current_user
from fim_one.web.exceptions import AppError
from fim_one.web.models import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.user import User
from fim_one.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/builder", tags=["builder"])


class BuilderSessionRequest(BaseModel):
    target_type: str  # "connector" | "agent"
    target_id: str


@router.post("/session", response_model=ApiResponse)
async def create_builder_session(
    body: BuilderSessionRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    target: Any = None
    if body.target_type == "connector":
        result = await db.execute(
            select(Connector).where(
                Connector.id == body.target_id,
                Connector.user_id == current_user.id,
            )
        )
        target = result.scalar_one_or_none()
        if target is None:
            raise AppError("connector_not_found", status_code=404)

        agent_name = f"__builder_connector_{body.target_id}"
        instructions = (
            f"You are a Connector Builder Agent.\n"
            f"connector_id={body.target_id}\n"
            f"Connector name: {target.name}\n"
            f"Base URL: {target.base_url}\n\n"
            f"You have specialized tools to manage this connector:\n"
            f"- connector_get_settings: Read current connector settings (base_url, auth, etc.)\n"
            f"- connector_update_settings: Update connector settings (name, base_url, auth_type, auth_config)\n"
            f"- connector_test_connection: Verify the base_url is reachable with current auth\n"
            f"- connector_list_actions: List all existing actions\n"
            f"- connector_create_action: Create a new API action\n"
            f"- connector_update_action: Update an existing action\n"
            f"- connector_delete_action: Delete an action\n"
            f"- connector_test_action: Test an action with sample parameters\n"
            f"- connector_import_openapi: Batch-import actions from an OpenAPI/Swagger spec URL or JSON\n\n"
            f"Recommended workflow:\n"
            f"1. Call connector_get_settings to see the current state\n"
            f"2. Call connector_test_connection to verify connectivity before building actions\n"
            f"3. If the user provides an OpenAPI spec URL, use connector_import_openapi (with dry_run=true first)\n"
            f"4. For manual action building, call connector_list_actions first, then create/update as needed\n"
            f"5. After building, test key actions with connector_test_action\n\n"
            f"IMPORTANT — Credential masking:\n"
            f"When connector_get_settings returns auth_config, secret values are ALWAYS masked for display — "
            f"you will see strings like '****abcd' (masked with last 4 chars visible) or '****' (fully masked). "
            f"This is encrypted/desensitized output, NOT a missing value. The credential IS configured on the server.\n"
            f"- If the user asks whether the configuration is complete, DO NOT say the API key or token is missing "
            f"just because the returned value starts with '****'. Treat any masked value as present and valid.\n"
            f"- Only report a credential as missing when the field itself is absent from auth_config, is null, or is an empty string.\n"
            f"- If the user wants to verify the credential actually works, use connector_test_connection — never ask them to paste the real key.\n"
        )
    elif body.target_type == "agent":
        result = await db.execute(
            select(Agent).where(
                Agent.id == body.target_id,
                Agent.user_id == current_user.id,
            )
        )
        target = result.scalar_one_or_none()
        if target is None:
            raise AppError("agent_not_found", status_code=404)
        if target.is_builder:
            raise AppError("unsupported_target_type", status_code=400)

        agent_name = f"__builder_agent_{body.target_id}"
        instructions = (
            f"You are an Agent Builder Assistant.\n"
            f"target_agent_id={body.target_id}\n"
            f"Agent name: {target.name}\n\n"
            f"You have specialized tools to read and modify this agent's configuration:\n"
            f"- agent_get_settings: Read the current agent settings\n"
            f"- agent_update_settings: Update name, description, instructions, execution_mode, tool_categories, or suggested_prompts\n"
            f"- agent_list_connectors: List all connectors owned by the user (with attached status)\n"
            f"- agent_add_connector: Attach a connector to this agent so its actions become tools\n"
            f"- agent_remove_connector: Detach a connector from this agent\n"
            f"- agent_set_model: Change the LLM model and/or temperature/max_tokens\n\n"
            f"Workflow:\n"
            f"1. ALWAYS call agent_get_settings first to see the current state before making changes\n"
            f"2. Make targeted updates using the appropriate tool\n"
            f"3. To add connector tools: call agent_list_connectors to find the connector_id, then agent_add_connector\n"
            f"4. After updating, confirm what was changed\n\n"
            f"Current agent state:\n"
            f"- Description: {target.description or '(none)'}\n"
            f"- Instructions: {(target.instructions or '(none)')[:500]}\n"
            f"- Execution mode: {target.execution_mode}\n"
            f"- Tool categories: {target.tool_categories or []}\n"
            f"- Connected connectors: {target.connector_ids or []}\n"
            f"- Model config: {target.model_config_json or '(default)'}\n"
            f"- Status: {target.status}\n"
        )
    elif body.target_type == "connector_db":
        result = await db.execute(
            select(Connector)
            .where(Connector.id == body.target_id, Connector.user_id == current_user.id)
        )
        target = result.scalar_one_or_none()
        if not target or target.type != "database":
            raise AppError("connector_not_found", status_code=404)
        agent_name = f"__builder_db_{body.target_id}"
        db_type = (target.db_config or {}).get("type", "unknown")
        instructions = (
            f"You are a DB Schema Builder Agent.\n"
            f"connector_id={body.target_id}\n"
            f"Connector name: {target.name}\n"
            f"Database type: {db_type}\n\n"
            f"You have specialized tools to manage this database connector's schema:\n"
            f"- db_get_connector_settings: View current db config (read_only, max_rows, timeout, ssl)\n"
            f"- db_update_connector_settings: Update safe config fields\n"
            f"- db_test_connection: Verify the database connection works\n"
            f"- db_list_tables: List all tables with visibility and annotation status\n"
            f"- db_get_table_detail: Get columns for a specific table\n"
            f"- db_annotate_table: Set display_name and description for a table\n"
            f"- db_annotate_column: Set display_name and description for a specific column\n"
            f"- db_set_table_visibility: Show or hide a specific table\n"
            f"- db_batch_set_visibility: Bulk show/hide by prefix list or name list\n"
            f"- db_run_sample_query: Run a SELECT to understand table content\n\n"
            f"Recommended workflow:\n"
            f"1. Call db_test_connection to verify connectivity\n"
            f"2. Call db_list_tables to see current state\n"
            f"3. Use db_batch_set_visibility to handle system/framework tables by prefix\n"
            f"4. Use db_get_table_detail + db_run_sample_query for ambiguous tables\n"
            f"5. Use db_annotate_table / db_annotate_column to add human-readable names\n\n"
            f"IMPORTANT — Credential masking:\n"
            f"When db_get_connector_settings returns the db_config, the 'password' field is ALWAYS masked as '****'. "
            f"This is desensitized display only — the real password is stored encrypted on the server and IS configured.\n"
            f"- If the user asks whether the database credentials are complete, DO NOT say the password is missing "
            f"just because you see '****'. Treat a masked password as present and valid.\n"
            f"- Only report the password as missing when the field itself is absent, null, or an empty string.\n"
            f"- To verify the credential actually works, call db_test_connection — never ask the user to paste the real password.\n"
        )
    else:
        raise AppError("unsupported_target_type", status_code=400)

    # Find or create builder agent
    result = await db.execute(
        select(Agent).where(
            Agent.name == agent_name,
            Agent.user_id == current_user.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        # Refresh instructions so new tools and current state are reflected
        setattr(existing, "instructions", instructions)
        await db.commit()
        return ApiResponse(data={"builder_agent_id": existing.id})

    if body.target_type == "connector":
        description = f"Builder agent for connector {body.target_id}"
        tool_categories = ["builder", "web"]
    elif body.target_type == "connector_db":
        description = f"DB Schema Builder for connector {body.target_id}"
        tool_categories = ["db_builder"]
    else:
        description = f"Builder assistant for agent {body.target_id}"
        tool_categories = ["agent_builder"]

    agent = Agent(
        user_id=current_user.id,
        name=agent_name,
        icon="\U0001f528",
        description=description,
        instructions=instructions,
        execution_mode="auto",
        tool_categories=tool_categories,
        status="draft",
        is_builder=True,
    )
    db.add(agent)
    await db.commit()
    return ApiResponse(data={"builder_agent_id": agent.id})
