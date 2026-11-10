"""Agent CRUD endpoints with publish/unpublish lifecycle."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.audit import actions
from fim_one.db import get_session
from fim_one.web.exceptions import AppError
from fim_one.web.audit_events import (
    record_resource,
    record_resource_change,
    record_unpublish_by_other,
    snapshot,
)
from fim_one.web.auth import get_current_user, get_user_org_ids
from fim_one.web.platform import MARKET_ORG_ID, is_market_org
from fim_one.db.models import Agent, User
from fim_one.db.models.connector import Connector
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.web.schemas.agent import AgentCreate, AgentForkRequest, AgentResponse, AgentUpdate
from fim_one.web.schemas.common import ApiResponse, PaginatedResponse, PublishRequest
from fim_one.web.visibility import build_visibility_filter

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _agent_to_response(agent: Agent, *, is_owner: bool) -> AgentResponse:
    # Non-owners: strip internal content (instructions, model config are IP)
    if is_owner:
        instructions = agent.instructions
        compact_instructions = agent.compact_instructions
        model_config_json = agent.model_config_json
    else:
        instructions = None
        compact_instructions = None
        model_config_json = None
    return AgentResponse(
        id=agent.id,
        user_id=agent.user_id or "",
        name=agent.name,
        icon=agent.icon,
        description=agent.description,
        instructions=instructions,
        model_config_json=model_config_json,
        tool_categories=agent.tool_categories,
        suggested_prompts=agent.suggested_prompts,
        kb_ids=agent.kb_ids,
        connector_ids=agent.connector_ids,
        mcp_server_ids=agent.mcp_server_ids,
        grounding_config=agent.grounding_config,
        sandbox_config=agent.sandbox_config,
        execution_mode=agent.execution_mode,
        status=agent.status,
        published_at=(
            agent.published_at.isoformat() if agent.published_at else None
        ),
        is_active=agent.is_active,
        is_builder=agent.is_builder,
        compact_instructions=compact_instructions,
        visibility=getattr(agent, "visibility", "personal"),
        org_id=getattr(agent, "org_id", None),
        publish_status=getattr(agent, "publish_status", None),
        reviewed_by=getattr(agent, "reviewed_by", None),
        reviewed_at=(
            agent.reviewed_at.isoformat() if agent.reviewed_at else None
        ),
        review_note=getattr(agent, "review_note", None),
        forked_from=getattr(agent, "forked_from", None),
        confirmation_mode=getattr(agent, "confirmation_mode", "auto") or "auto",
        confirmation_approver_scope=getattr(
            agent, "confirmation_approver_scope", "initiator"
        ) or "initiator",
        require_confirmation_for_all=bool(
            getattr(agent, "require_confirmation_for_all", False)
        ),
        approval_channel_id=getattr(agent, "approval_channel_id", None),
        suggest_followups=bool(getattr(agent, "suggest_followups", False)),
        created_at=agent.created_at.isoformat() if agent.created_at else "",
        updated_at=agent.updated_at.isoformat() if agent.updated_at else None,
    )


# What an audit record compares of an agent: whether it runs, when it asks
# a human, who may answer and through which channel, and what it may reach.
# A change to anything else (its name, its instructions) is a content edit.
AGENT_AUDITED_FIELDS = (
    "is_active",
    "confirmation_mode",
    "confirmation_approver_scope",
    "require_confirmation_for_all",
    "approval_channel_id",
    "connector_ids",
    "kb_ids",
    "mcp_server_ids",
    "skill_ids",
    "tool_categories",
    "sandbox_config",
)


async def _get_owned_agent(
    agent_id: str,
    user_id: str,
    db: AsyncSession,
) -> Agent:
    """Fetch an agent that the user owns."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)
    return agent


async def _get_accessible_agent(
    agent_id: str,
    user_id: str,
    db: AsyncSession,
) -> Agent:
    """Fetch an agent the user owns, org-shared, or Market-installed."""
    from fim_one.web.visibility import resolve_visibility
    vis_filter, _, _ = await resolve_visibility(Agent, user_id, "agent", db)
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, vis_filter)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)
    return agent


async def _validate_binding_ownership(
    user_id: str,
    db: AsyncSession,
    connector_ids: list[str] | None = None,
    kb_ids: list[str] | None = None,
    mcp_server_ids: list[str] | None = None,
    approval_channel_id: str | None = None,
    completion_channel_id: str | None = None,
    approval_channel_changed: bool = True,
    agent_org_id: str | None = None,
) -> None:
    """Verify the user can *access* every referenced connector / KB / MCP server.

    Access is what the agent could use at run time, judged for the user
    saving it (``use_clause_for``, the clause the use-time rule starts from):
    the user owns the resource, or holds a subscription to one that is still
    shared, so a subscribed connector can be bound rather than 403-ing as
    "not owned". A knowledge base cannot be shared, so it must be the user's
    own; one that is not would be dropped from the agent on every run.

    A channel is judged the way it is resolved when it is used
    (``resolve_org_channel``): once the agent has an organization
    (*agent_org_id*), the channel must belong to that one; being in another
    organization of the user is not enough. The approval channel must also
    be of a type that can carry approvals. *approval_channel_changed* is
    ``False`` when an update keeps the stored approval channel: the form
    sends it with every save, and a binding stored before these checks
    existed must not block saving something else (it is treated as unbound
    when used).

    Raises HTTP 403 if any referenced resource is not accessible to the user
    or a channel lies outside the agent's organization, HTTP 400 if the
    approval channel cannot carry approvals.
    """
    from fim_one.core.assembly.visibility import ResourceKind, use_clause_for

    async def _assert_usable(
        model: Any, kind: ResourceKind, ids: list[str] | None, err_code: str
    ) -> None:
        if not ids:
            return
        clause = await use_clause_for(db, kind, user_id)
        result = await db.execute(
            select(func.count())
            .select_from(model)
            .where(model.id.in_(ids), clause)
        )
        if result.scalar_one() != len(set(ids)):
            raise AppError(err_code, status_code=403)

    await _assert_usable(
        Connector, ResourceKind.CONNECTOR, connector_ids, "connector_ownership_denied"
    )
    await _assert_usable(
        KnowledgeBase, ResourceKind.KNOWLEDGE_BASE, kb_ids, "kb_ownership_denied"
    )
    await _assert_usable(
        MCPServer, ResourceKind.MCP_SERVER, mcp_server_ids, "mcp_server_ownership_denied"
    )

    # An approval channel posts tool arguments into its chat, and a completion
    # channel the user's message and the answer, so either may only come from
    # an organization the user belongs to, and from the agent's own once it
    # has one: that is the only place ``resolve_org_channel`` looks at run
    # time, so a channel from another of the user's organizations would be
    # saved and then never used.
    from fim_one.core.channels import ApprovalCarrier, get_channel_class
    from fim_one.db.models.channel import Channel

    async def _channel(channel_id: str, denied: str) -> Any:
        """The ``(org_id, type)`` of a channel in one of the user's organizations."""
        row = (
            await db.execute(
                select(Channel.org_id, Channel.type).where(Channel.id == channel_id)
            )
        ).one_or_none()
        if row is None or row.org_id not in await get_user_org_ids(user_id, db):
            raise AppError(denied, status_code=403)
        return row

    def _in_agent_org(row: Any, err_code: str) -> None:
        if agent_org_id and row.org_id != agent_org_id:
            raise AppError(err_code, status_code=403)

    if approval_channel_id:
        approval = await _channel(approval_channel_id, "approval_channel_denied")
        if approval_channel_changed:
            _in_agent_org(approval, "approval_channel_not_in_agent_org")
        channel_class = get_channel_class(approval.type)
        carries = channel_class is not None and issubclass(channel_class, ApprovalCarrier)
        if approval_channel_changed and not carries:
            # A webhook or an email list has no way back for a decision, so
            # at run time such a binding is treated as no channel at all.
            raise AppError("approval_channel_type_unsupported", status_code=400)
    if completion_channel_id:
        # Passed by an update only when it changes (see ``update_agent``).
        completion = await _channel(completion_channel_id, "completion_channel_denied")
        _in_agent_org(completion, "completion_channel_not_in_agent_org")


def _first_name_collision(
    rows: list[tuple[str, str, str]],
) -> tuple[tuple[str, str], tuple[str, str], str] | None:
    """The first two ``(id, name, key)`` rows that share a non-empty key.

    Returns ``((id, name), (id, name), key)`` for the earlier and the later
    row, or ``None`` when every key is distinct.
    """
    seen: dict[str, tuple[str, str]] = {}
    for rid, name, key in rows:
        if not key:
            continue
        earlier = seen.get(key)
        if earlier is not None:
            return earlier, (rid, name), key
        seen[key] = (rid, name)
    return None


async def _validate_distinct_tool_names(
    db: AsyncSession,
    connector_ids: list[str] | None = None,
    mcp_server_ids: list[str] | None = None,
) -> None:
    """Refuse a binding whose connectors, or MCP servers, would share tool names.

    A connector's tools are named after ``connector_tool_prefix(name)`` and an
    MCP server's after ``server_tool_prefix(name)``. Two bound resources with
    the same prefix collide at run time: the one bound first keeps the plain
    name and the other is renamed or loses the tools of that name, so which
    system a call reaches depends on binding order. API connectors and
    database connectors are offered through separate tools and are compared
    among themselves. A connector whose name has no ASCII letter or digit is
    named after its id and collides with nothing.

    Raises HTTP 400 naming the two resources. Pass only the lists that are
    being changed: an agent saved before this check existed must stay
    editable in every other respect.
    """
    from fim_one.core.mcp.adapter import server_tool_prefix
    from fim_one.core.tool.connector.adapter import connector_tool_prefix

    def _in_binding_order(ids: list[str], rows: Any) -> list[Any]:
        by_id = {row.id: row for row in rows}
        return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]

    if connector_ids:
        connectors = _in_binding_order(
            connector_ids,
            (
                await db.execute(
                    select(Connector.id, Connector.name, Connector.type).where(
                        Connector.id.in_(connector_ids)
                    )
                )
            ).all(),
        )
        for is_database in (False, True):
            clash = _first_name_collision(
                [
                    (row.id, row.name, connector_tool_prefix(row.name))
                    for row in connectors
                    if (row.type == "database") is is_database
                ]
            )
            if clash is not None:
                (first_id, first), (second_id, second), tool_name = clash
                raise AppError(
                    "agent_connector_names_collide",
                    status_code=400,
                    detail=(
                        f'Connectors "{first}" and "{second}" would both be offered '
                        f'to the agent as "{tool_name}". Rename one of them or bind '
                        "only one."
                    ),
                    detail_args={
                        "first": first,
                        "second": second,
                        "tool_name": tool_name,
                        "first_id": first_id,
                        "second_id": second_id,
                    },
                )

    if mcp_server_ids:
        servers = _in_binding_order(
            mcp_server_ids,
            (
                await db.execute(
                    select(MCPServer.id, MCPServer.name).where(MCPServer.id.in_(mcp_server_ids))
                )
            ).all(),
        )
        clash = _first_name_collision(
            [(row.id, row.name, server_tool_prefix(row.name)) for row in servers]
        )
        if clash is not None:
            (first_id, first), (second_id, second), tool_name = clash
            raise AppError(
                "agent_mcp_server_names_collide",
                status_code=400,
                detail=(
                    f'MCP servers "{first}" and "{second}" would both offer their '
                    f'tools to the agent as "{tool_name}". Rename one of them or '
                    "bind only one."
                ),
                detail_args={
                    "first": first,
                    "second": second,
                    "tool_name": tool_name,
                    "first_id": first_id,
                    "second_id": second_id,
                },
            )


def _binding_changed(new: list[str] | None, stored: Any) -> bool:
    """Whether an update binds a different set of resources than is stored."""
    return set(new or []) != set(stored or [])


def _completion_channel_id(model_config_json: Any) -> str | None:
    """The channel an agent config sends completion notifications to, if enabled."""
    try:
        on_complete = model_config_json["notifications"]["on_complete"]
        channel_id = on_complete["channel_id"] if on_complete.get("enabled") else None
    except (KeyError, TypeError, AttributeError):
        return None
    return channel_id if isinstance(channel_id, str) and channel_id else None


def _audited_state(agent: Agent) -> dict[str, Any]:
    """What an audit record compares of an agent.

    Its audited fields, and the channel its completion notifications go
    to: that one is kept inside the model config, and decides where what
    the agent produced is sent.
    """
    return {
        **snapshot(agent, AGENT_AUDITED_FIELDS),
        "completion_channel_id": _completion_channel_id(agent.model_config_json),
    }


@router.post("", response_model=ApiResponse)
async def create_agent(
    body: AgentCreate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=body.connector_ids,
        kb_ids=body.kb_ids,
        mcp_server_ids=body.mcp_server_ids,
        approval_channel_id=body.approval_channel_id,
        completion_channel_id=_completion_channel_id(body.model_config_json),
    )
    await _validate_distinct_tool_names(
        db, connector_ids=body.connector_ids, mcp_server_ids=body.mcp_server_ids
    )
    agent = Agent(
        user_id=current_user.id,
        name=body.name,
        icon=body.icon,
        description=body.description,
        instructions=body.instructions,
        model_config_json=body.model_config_json,
        tool_categories=body.tool_categories,
        suggested_prompts=body.suggested_prompts,
        kb_ids=body.kb_ids,
        connector_ids=body.connector_ids,
        mcp_server_ids=body.mcp_server_ids,
        grounding_config=body.grounding_config,
        sandbox_config=body.sandbox_config,
        execution_mode=body.execution_mode,
        compact_instructions=body.compact_instructions,
        confirmation_mode=body.confirmation_mode,
        confirmation_approver_scope=body.confirmation_approver_scope,
        require_confirmation_for_all=body.require_confirmation_for_all,
        approval_channel_id=body.approval_channel_id,
        suggest_followups=body.suggest_followups,
        status="draft",
    )
    db.add(agent)
    await record_resource(db, current_user, actions.AGENT_CREATE, "agent", agent)
    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    return ApiResponse(data=_agent_to_response(agent, is_owner=True).model_dump())


@router.get("", response_model=PaginatedResponse)
async def list_agents(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    agent_status: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse:
    user_org_ids = await get_user_org_ids(current_user.id, db)

    # Get subscribed agent IDs with org_id for source tagging
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id, ResourceSubscription.org_id).where(
            ResourceSubscription.user_id == current_user.id,
            ResourceSubscription.resource_type == "agent",
        )
    )
    sub_rows = sub_result.all()
    subscribed_agent_ids = [r.resource_id for r in sub_rows]
    sub_org_map = {r.resource_id: r.org_id for r in sub_rows}

    base = select(Agent).where(
        Agent.is_builder == False,  # noqa: E712
        build_visibility_filter(Agent, current_user.id, user_org_ids, subscribed_ids=subscribed_agent_ids),
    )
    if agent_status is not None:
        base = base.where(Agent.status == agent_status)

    count_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base.order_by(Agent.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    agents = result.scalars().all()

    subscribed_agent_ids_set = set(subscribed_agent_ids)
    items = []
    for a in agents:
        _is_owner = a.user_id == current_user.id
        resp = _agent_to_response(a, is_owner=_is_owner)
        if _is_owner:
            resp.source = "own"
        elif a.id in subscribed_agent_ids_set:
            sub_org_id = sub_org_map.get(a.id)
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


@router.get("/{agent_id}", response_model=ApiResponse)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_accessible_agent(agent_id, current_user.id, db)
    _is_owner = agent.user_id == current_user.id
    return ApiResponse(data=_agent_to_response(agent, is_owner=_is_owner).model_dump())


@router.put("/{agent_id}", response_model=ApiResponse)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    before = _audited_state(agent)

    update_data = body.model_dump(exclude_unset=True)
    # Checked when it changes: the settings form sends the whole model config
    # with every save, and a channel that was deleted since must not block
    # saving something else (it is skipped at send time).
    completion_channel_id = _completion_channel_id(update_data.get("model_config_json"))
    if completion_channel_id == _completion_channel_id(agent.model_config_json):
        completion_channel_id = None
    await _validate_binding_ownership(
        current_user.id, db,
        connector_ids=update_data.get("connector_ids"),
        kb_ids=update_data.get("kb_ids"),
        mcp_server_ids=update_data.get("mcp_server_ids"),
        approval_channel_id=update_data.get("approval_channel_id"),
        completion_channel_id=completion_channel_id,
        approval_channel_changed=(
            update_data.get("approval_channel_id") != agent.approval_channel_id
        ),
        agent_org_id=agent.org_id,
    )
    # Checked only for a list this update changes: the form sends both lists
    # with every save, and an agent bound to two same-named resources before
    # this check existed must stay editable otherwise.
    new_connector_ids = update_data.get("connector_ids")
    new_mcp_server_ids = update_data.get("mcp_server_ids")
    await _validate_distinct_tool_names(
        db,
        connector_ids=(
            new_connector_ids
            if _binding_changed(new_connector_ids, agent.connector_ids)
            else None
        ),
        mcp_server_ids=(
            new_mcp_server_ids
            if _binding_changed(new_mcp_server_ids, agent.mcp_server_ids)
            else None
        ),
    )
    for field, value in update_data.items():
        setattr(agent, field, value)

    content_changed = bool(update_data.keys() - {"is_active"})
    if content_changed:
        from fim_one.web.publish_review import check_edit_revert

        reverted = await check_edit_revert(agent, db)
    else:
        reverted = False

    await record_resource_change(
        db, current_user, actions.AGENT_UPDATE, "agent", agent, before, after=_audited_state(agent)
    )
    await db.commit()
    result = await db.execute(select(Agent).where(Agent.id == agent.id))
    agent = result.scalar_one()
    data = _agent_to_response(agent, is_owner=True).model_dump()
    if reverted:
        data["publish_status_reverted"] = True
    return ApiResponse(data=data)


@router.delete("/{agent_id}", response_model=ApiResponse)
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    from fim_one.web.api.market import purge_resource_subscriptions

    await purge_resource_subscriptions(
        db, resource_type="agent", resource_id=agent_id
    )
    await record_resource(db, current_user, actions.AGENT_DELETE, "agent", agent)
    await db.delete(agent)
    await db.commit()
    return ApiResponse(data={"deleted": agent_id})


@router.post("/{agent_id}/publish", response_model=ApiResponse)
async def publish_agent(
    agent_id: str,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Publish agent to personal, org, or global scope."""
    agent = await _get_owned_agent(agent_id, current_user.id, db)

    if body.scope == "org":
        if not body.org_id:
            raise AppError("org_id_required", status_code=400)
        from fim_one.web.auth import require_org_member
        if not is_market_org(body.org_id):
            await require_org_member(body.org_id, current_user, db)
        agent.visibility = "org"
        agent.org_id = body.org_id
        from fim_one.web.publish_review import apply_publish_status
        await apply_publish_status(agent, body.org_id, db, resource_type="agent", publisher_id=current_user.id)
    else:
        raise AppError("invalid_scope", status_code=400)

    agent.published_at = datetime.now(UTC)
    agent.status = "published"

    # Audit log: submitted (org scope only — org_id is set at this point)
    if body.scope == "org" and body.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=body.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="submitted",
            actor=current_user,
        )

    # Check referenced resources and warn about private dependencies
    warnings: list[str] = []
    if agent.kb_ids:
        for kb_id in agent.kb_ids:
            result = await db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb = result.scalar_one_or_none()
            if kb and getattr(kb, "visibility", "personal") == "personal":
                warnings.append(
                    f"KB '{kb.name}' is private — will be accessed via owner delegation"
                )

    if agent.connector_ids:
        for cid in agent.connector_ids:
            result = await db.execute(
                select(Connector).where(Connector.id == cid)
            )
            conn = result.scalar_one_or_none()
            if conn and getattr(conn, "visibility", "personal") == "personal":
                warnings.append(
                    f"Connector '{conn.name}' is private — will be accessed via owner delegation"
                )

    await db.commit()
    await db.refresh(agent)

    data = _agent_to_response(agent, is_owner=True).model_dump()
    data["warnings"] = warnings
    return ApiResponse(data=data)


@router.post("/{agent_id}/resubmit", response_model=ApiResponse)
async def resubmit_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Resubmit a rejected agent for review."""
    agent = await _get_owned_agent(agent_id, current_user.id, db)
    if agent.publish_status != "rejected":
        raise AppError("not_rejected", status_code=400)
    agent.publish_status = "pending_review"
    agent.reviewed_by = None
    agent.reviewed_at = None
    agent.review_note = None

    if agent.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=agent.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="resubmitted",
            actor=current_user,
        )

    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent, is_owner=True).model_dump())


@router.post("/{agent_id}/unpublish", response_model=ApiResponse)
async def unpublish_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Revert agent to personal visibility."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise AppError("agent_not_found", status_code=404)

    is_owner = agent.user_id == current_user.id
    is_admin = current_user.is_admin
    is_org_admin = False

    if agent.visibility == "org" and agent.org_id and not is_owner:
        from fim_one.web.auth import user_is_org_admin

        is_org_admin = await user_is_org_admin(agent.org_id, current_user, db)

    if not (is_owner or is_admin or is_org_admin):
        raise AppError("unpublish_denied", status_code=403)

    # Log BEFORE clearing org_id so we capture which org it was unpublished from
    await record_unpublish_by_other(
        db, current_user, actions.AGENT_UNPUBLISH_BY_ADMIN, "agent", agent
    )
    if agent.org_id:
        from fim_one.web.api.reviews import log_review_event
        await log_review_event(
            db=db,
            org_id=agent.org_id,
            resource_type="agent",
            resource_id=agent.id,
            resource_name=agent.name,
            action="unpublished",
            actor=current_user,
        )

    agent.visibility = "personal"
    agent.org_id = None
    agent.published_at = None
    agent.publish_status = None
    agent.status = "draft"

    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=_agent_to_response(agent, is_owner=agent.user_id == current_user.id).model_dump())


# ---------------------------------------------------------------------------
# Fork (clone)
# ---------------------------------------------------------------------------


@router.post("/{agent_id}/fork", response_model=ApiResponse)
async def fork_agent(
    agent_id: str,
    body: AgentForkRequest | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Clone an existing agent for the current user.

    Copies all configuration fields but NOT org_id, publish_status, or
    is_builder.  The forked agent is set to personal/active ownership.
    Only the owner of the source agent can fork it.
    """
    source = await _get_accessible_agent(agent_id, current_user.id, db)
    if source.user_id != current_user.id:
        raise AppError("fork_denied", status_code=403, detail="Only the owner can fork this resource")

    fork_name = (body.name if body and body.name else f"{source.name} (Copy)")[:200]

    forked = Agent(
        user_id=current_user.id,
        name=fork_name,
        description=source.description,
        icon=source.icon,
        instructions=source.instructions,
        execution_mode=source.execution_mode,
        model_config_json=source.model_config_json,
        tool_categories=source.tool_categories,
        suggested_prompts=source.suggested_prompts,
        connector_ids=source.connector_ids,
        kb_ids=source.kb_ids,
        mcp_server_ids=source.mcp_server_ids,
        skill_ids=source.skill_ids,
        compact_instructions=source.compact_instructions,
        grounding_config=source.grounding_config,
        sandbox_config=source.sandbox_config,
        is_active=True,
        is_builder=False,
        forked_from=source.id,
        visibility="personal",
        org_id=None,
        publish_status=None,
        status="draft",
    )
    db.add(forked)
    await record_resource(
        db, current_user, actions.AGENT_CREATE, "agent", forked, detail=f"fork of {source.id}"
    )
    await db.commit()

    result = await db.execute(
        select(Agent).where(Agent.id == forked.id)
    )
    forked = result.scalar_one()
    return ApiResponse(data=_agent_to_response(forked, is_owner=True).model_dump())
