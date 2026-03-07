"""AI-powered agent creation and refinement."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_agent.core.model.types import ChatMessage
from fim_agent.core.utils import get_language_directive
from fim_agent.db import get_session
from fim_agent.web.auth import get_current_user
from fim_agent.web.deps import get_fast_llm
from fim_agent.web.models import Agent
from fim_agent.web.models.connector import Connector
from fim_agent.web.models.knowledge_base import KnowledgeBase
from fim_agent.web.models.user import User
from fim_agent.web.schemas.agent import (
    AICreateAgentRequest,
    AICreateAgentResult,
    AIRefineAgentRequest,
    AIRefineAgentResult,
)
from fim_agent.web.exceptions import AppError
from fim_agent.web.schemas.common import ApiResponse
from fim_agent.web.api.agents import (
    _agent_to_response,
    _get_owned_agent,
    _validate_binding_ownership,
)

router = APIRouter(prefix="/api/agents", tags=["agent-ai"])

logger = logging.getLogger(__name__)

_VALID_TOOL_CATEGORIES = frozenset(
    ["computation", "web", "filesystem", "knowledge", "mcp", "connector", "general"]
)

# ---------------------------------------------------------------------------
# LLM Helpers
# ---------------------------------------------------------------------------

_CREATE_SYSTEM_PROMPT = """\
You are an AI agent configuration assistant. Given a user instruction and the \
available knowledge bases and connectors, generate a complete agent configuration \
as a JSON object.

The JSON object MUST have these fields:
- "name": string — a short, descriptive agent name (max 200 chars), plain text without emoji
- "icon": string — a single emoji that represents this agent (e.g. "🗣️", "🔬", "🤖")
- "description": string|null — what the agent does
- "instructions": string|null — system instructions for the agent
- "tool_categories": list[string]|null — from: computation, web, filesystem, knowledge, mcp, connector, general
- "kb_ids": list[string]|null — IDs of knowledge bases to bind (from the available list)
- "connector_ids": list[string]|null — IDs of connectors to bind (from the available list)
- "suggested_prompts": list[string]|null — example prompts users can try
- "grounding_config": object|null — optional, may include "confidence_threshold" (float 0-1)

IMPORTANT binding rules — be conservative:
- Only bind kb_ids and include "knowledge" in tool_categories when the user \
EXPLICITLY asks for document retrieval, knowledge base search, or RAG capabilities. \
Do NOT bind KBs just because they exist — most agents do not need them.
- Only bind connector_ids and include "connector" when the user EXPLICITLY \
mentions using external APIs, connectors, or specific integrations.
- If the user mentions searching the web, include "web".
- When in doubt, leave kb_ids and connector_ids as null.

Output ONLY valid JSON. No markdown, no commentary."""

_REFINE_SYSTEM_PROMPT = """\
You are an AI agent configuration editor. Given the current agent configuration \
and a user instruction, output a JSON object with ONLY the fields that should change.

Updatable fields:
- "name": string
- "icon": string — a single emoji
- "description": string|null
- "instructions": string|null
- "tool_categories": list[string]|null — from: computation, web, filesystem, knowledge, mcp, connector, general
- "kb_ids": list[string]|null
- "connector_ids": list[string]|null
- "suggested_prompts": list[string]|null
- "grounding_config": object|null

Only include fields the user wants to change. Do not include unchanged fields.
Do NOT add kb_ids or connector_ids unless the user explicitly requests them.

Output ONLY valid JSON. No markdown, no commentary."""


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
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
            raise AppError(
                "llm_invalid_json",
                status_code=422,
                detail=f"LLM returned invalid JSON after retry: {exc}",
            ) from exc
        logger.warning("LLM JSON parse failed, retrying: %s", exc)
        feedback = (
            f"Your previous response was not valid JSON. Error: {exc}\n"
            "Please output ONLY a valid JSON object, no markdown or commentary."
        )
        return await _llm_call(system, user_msg, retry_context=feedback, language_directive=language_directive)


# ---------------------------------------------------------------------------
# Context Builders
# ---------------------------------------------------------------------------


async def _build_available_resources_context(
    user_id: str,
    db: AsyncSession,
) -> str:
    """Query user's KBs and connectors, return a descriptive string."""
    lines: list[str] = []

    kb_result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
    )
    kbs = kb_result.scalars().all()
    if kbs:
        lines.append(f"Available knowledge bases ({len(kbs)}):")
        for kb in kbs:
            lines.append(f"  - [{kb.id}] {kb.name}: {kb.description or 'no description'}")
    else:
        lines.append("No knowledge bases available.")

    conn_result = await db.execute(
        select(Connector).where(Connector.user_id == user_id)
    )
    connectors = conn_result.scalars().all()
    if connectors:
        lines.append(f"\nAvailable connectors ({len(connectors)}):")
        for c in connectors:
            lines.append(f"  - [{c.id}] {c.name}: {c.description or 'no description'}")
    else:
        lines.append("\nNo connectors available.")

    return "\n".join(lines)


def _build_agent_context(agent: Agent) -> str:
    """Describe the current agent configuration."""
    lines = [
        f"Agent: {agent.name}",
        f"Icon: {agent.icon or 'N/A'}",
        f"Description: {agent.description or 'N/A'}",
        f"Instructions: {agent.instructions or 'N/A'}",
        f"Tool categories: {json.dumps(agent.tool_categories)}",
        f"KB IDs: {json.dumps(agent.kb_ids)}",
        f"Connector IDs: {json.dumps(agent.connector_ids)}",
        f"Suggested prompts: {json.dumps(agent.suggested_prompts)}",
        f"Grounding config: {json.dumps(agent.grounding_config)}",
        f"Status: {agent.status}",
    ]
    return "\n".join(lines)


def _validate_tool_categories(categories: list[str] | None) -> list[str] | None:
    """Filter tool_categories to only valid values."""
    if categories is None:
        return None
    return [c for c in categories if c in _VALID_TOOL_CATEGORIES]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ai/create", response_model=ApiResponse)
async def ai_create_agent(
    body: AICreateAgentRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Create a new agent using AI from natural language instruction."""
    lang_directive = get_language_directive(current_user.preferred_language)

    resources_ctx = await _build_available_resources_context(current_user.id, db)
    user_msg = f"{resources_ctx}\n\nUser instruction: {body.instruction}"

    data = await _llm_call(_CREATE_SYSTEM_PROMPT, user_msg, language_directive=lang_directive)
    if not isinstance(data, dict):
        raise AppError("llm_unexpected_format", status_code=422)

    # Validate tool_categories
    tool_categories = _validate_tool_categories(data.get("tool_categories"))

    # Extract and validate binding IDs
    kb_ids = data.get("kb_ids") or None
    connector_ids = data.get("connector_ids") or None

    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=connector_ids,
        kb_ids=kb_ids,
    )

    icon = data.get("icon")
    if isinstance(icon, str):
        icon = icon[:100]

    agent = Agent(
        user_id=current_user.id,
        name=str(data.get("name", "New Agent"))[:200],
        icon=icon,
        description=data.get("description"),
        instructions=data.get("instructions"),
        tool_categories=tool_categories,
        kb_ids=kb_ids,
        connector_ids=connector_ids,
        suggested_prompts=data.get("suggested_prompts"),
        grounding_config=data.get("grounding_config"),
        status="draft",
    )
    db.add(agent)
    await db.commit()

    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()

    return ApiResponse(
        data=AICreateAgentResult(
            agent=_agent_to_response(agent),
            message="Agent created successfully.",
            message_key="ai_agent_created",
        ).model_dump()
    )


@router.post("/{agent_id}/ai/refine", response_model=ApiResponse)
async def ai_refine_agent(
    agent_id: str,
    body: AIRefineAgentRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Refine an existing agent using AI instruction."""
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    lang_directive = get_language_directive(current_user.preferred_language)

    resources_ctx = await _build_available_resources_context(current_user.id, db)
    agent_ctx = _build_agent_context(agent)
    user_msg = (
        f"Current agent configuration:\n{agent_ctx}\n\n"
        f"{resources_ctx}\n\n"
        f"User instruction: {body.instruction}"
    )

    data = await _llm_call(_REFINE_SYSTEM_PROMPT, user_msg, language_directive=lang_directive)
    if not isinstance(data, dict):
        raise AppError("llm_unexpected_format", status_code=422)

    updatable_fields = {
        "name", "icon", "description", "instructions", "tool_categories",
        "suggested_prompts", "kb_ids", "connector_ids", "grounding_config",
    }

    modified_fields: list[str] = []
    for field, value in data.items():
        if field not in updatable_fields:
            continue

        if field == "tool_categories":
            value = _validate_tool_categories(value)

        if field == "name" and isinstance(value, str):
            value = value[:200]

        setattr(agent, field, value)
        modified_fields.append(field)

    # Validate ownership of any changed bindings
    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=data.get("connector_ids"),
        kb_ids=data.get("kb_ids"),
    )

    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()

    if modified_fields:
        refine_message_key = "ai_agent_updated"
        refine_message_args = {"count": len(modified_fields), "fields": ", ".join(modified_fields)}
    else:
        refine_message_key = "ai_no_changes"
        refine_message_args = {}

    return ApiResponse(
        data=AIRefineAgentResult(
            agent=_agent_to_response(agent),
            modified_fields=modified_fields,
            message=f"Updated {len(modified_fields)} field(s)." if modified_fields else "No changes applied.",
            message_key=refine_message_key,
            message_args=refine_message_args,
        ).model_dump()
    )
