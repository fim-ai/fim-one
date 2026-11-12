"""What the first proof of an address switches off, and the notice about it.

When an emailed code is first accepted for an account whose address was
unproven (``web/api/auth.py``, ``_first_proof_of_address``), whatever the
account had set up to act or send data without its holder present is
switched off. On a deployment with email verification off every account
starts unproven, so the person proving the address is usually the one who
set the account up. Nothing here is lost: each item is switched off by a
flag and can be turned back on, and the account holder is shown a notice
(``users.security_notice``) that lists what was switched off and how.

The one deletion: the account's own stored credentials for connectors and
MCP servers that belong to another user or to an organization. Those are
third-party tokens of whoever held the account before; kept, they would
send the new holder's requests to that third party. The holder authorises
again.

Memberships of organizations and their roles are left as they are and
listed in the notice.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import User

# How many names the notice lists per kind; the count is always exact.
_NAMES_SHOWN = 50

# The keys of ``security_notice["saved"]`` that can be restored.
RESTORABLE = ("system_instructions", "default_agent")


def _item(kind: str, rows: list[tuple[str, str | None]], **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "count": len(rows),
        "items": [{"id": rid, "name": name or rid} for rid, name in rows[:_NAMES_SHOWN]],
        **extra,
    }


def _off(stmt: Any) -> Any:
    return stmt.execution_options(synchronize_session=False)


async def switch_off_earlier_setup(
    db: AsyncSession,
    user: User,
    now: datetime,
    *,
    sign_in: dict[str, Any],
) -> dict[str, Any]:
    """Switch off what *user* set up before *now*; store and return the notice.

    *sign_in* says which ways in were closed by the caller (password, second
    factor, provider bindings, API keys); it goes into the notice as given.
    Not committed here.
    """
    from fim_one.core.assembly.confirmation import close_requests_of_runs
    from fim_one.core.assembly.model_endpoint import (
        has_custom_model_endpoint,
        user_endpoint_config_ids,
    )
    from fim_one.db.models import Agent, Workflow, WorkflowRun
    from fim_one.db.models.channel import Channel, ConfirmationRequest
    from fim_one.db.models.connector import Connector
    from fim_one.db.models.connector_credential import ConnectorCredential
    from fim_one.db.models.mcp_server import MCPServer
    from fim_one.db.models.mcp_server_credential import MCPServerCredential
    from fim_one.db.models.model_config import ModelConfig
    from fim_one.db.models.organization import Organization, OrgMembership

    items: list[dict[str, Any]] = []

    # Workflows: schedules off; webhook and trigger key suspended, kept.
    workflows = (
        await db.execute(
            select(
                Workflow.id,
                Workflow.name,
                Workflow.schedule_enabled,
                Workflow.webhook_url,
                Workflow.api_key_hash,
            ).where(Workflow.user_id == user.id, Workflow.created_at <= now)
        )
    ).all()
    scheduled = [(w.id, w.name) for w in workflows if w.schedule_enabled]
    reachable = [(w.id, w.name) for w in workflows if w.webhook_url or w.api_key_hash]
    if scheduled:
        await db.execute(
            _off(
                update(Workflow)
                .where(Workflow.id.in_([wid for wid, _ in scheduled]))
                .values(schedule_enabled=False)
            )
        )
        items.append(_item("workflow_schedules", scheduled))
    if reachable:
        await db.execute(
            _off(
                update(Workflow)
                .where(Workflow.id.in_([wid for wid, _ in reachable]))
                .values(access_suspended_at=now)
            )
        )
        items.append(_item("workflow_access", reachable))

    # Agents: custom model endpoint deactivated; completion notices off.
    agents = list(
        (
            await db.scalars(
                select(Agent).where(
                    Agent.user_id == user.id,
                    Agent.model_config_json.is_not(None),
                    Agent.created_at <= now,
                )
            )
        ).all()
    )
    user_endpoints = await user_endpoint_config_ids(db, [a.model_config_json for a in agents])
    custom: list[tuple[str, str | None]] = []
    notifying: list[tuple[str, str | None]] = []
    for agent in agents:
        cfg = agent.model_config_json
        if agent.is_active and has_custom_model_endpoint(cfg, user_endpoints):
            agent.is_active = False
            custom.append((agent.id, agent.name))
        on_complete = (
            (cfg.get("notifications") or {}).get("on_complete")
            if isinstance(cfg, dict) and isinstance(cfg.get("notifications"), dict)
            else None
        )
        if isinstance(on_complete, dict) and on_complete.get("enabled"):
            # A new value, so the JSON column is seen as changed; the
            # channel stays named and only the switch is turned off.
            changed = copy.deepcopy(cfg)
            changed["notifications"]["on_complete"]["enabled"] = False
            agent.model_config_json = changed
            notifying.append((agent.id, agent.name))
    if custom:
        items.append(_item("agents_custom_endpoint", custom))
    if notifying:
        items.append(_item("agent_notifications", notifying))

    model_configs = (
        await db.execute(
            select(ModelConfig.id, ModelConfig.name).where(
                ModelConfig.user_id == user.id,
                ModelConfig.is_active.is_(True),
                ModelConfig.base_url.is_not(None),
                ModelConfig.base_url != "",
                ModelConfig.created_at <= now,
            )
        )
    ).all()
    if model_configs:
        await db.execute(
            _off(
                update(ModelConfig)
                .where(ModelConfig.id.in_([r.id for r in model_configs]))
                .values(is_active=False)
            )
        )
        items.append(_item("model_configs", [(r.id, r.name) for r in model_configs]))

    for kind, model, owner in (
        ("connectors", Connector, Connector.user_id),
        ("mcp_servers", MCPServer, MCPServer.user_id),
        ("channels", Channel, Channel.created_by),
    ):
        rows = (
            await db.execute(
                select(model.id, model.name).where(
                    owner == user.id, model.is_active.is_(True), model.created_at <= now
                )
            )
        ).all()
        if rows:
            await db.execute(
                _off(update(model).where(model.id.in_([r.id for r in rows])).values(is_active=False))
            )
            items.append(_item(kind, [(r.id, r.name) for r in rows]))

    # The one deletion: the account's credentials stored on others' tools.
    removed: list[tuple[str, str | None]] = []
    for cred, tool, link in (
        (ConnectorCredential, Connector, ConnectorCredential.connector_id),
        (MCPServerCredential, MCPServer, MCPServerCredential.server_id),
    ):
        foreign_tools = select(tool.id).where(
            or_(tool.user_id.is_(None), tool.user_id != user.id, tool.org_id.is_not(None))
        )
        cred_rows = (
            await db.execute(
                select(cred.id, tool.id, tool.name)
                .join(tool, tool.id == link)
                .where(cred.user_id == user.id, cred.created_at <= now, link.in_(foreign_tools))
            )
        ).all()
        if cred_rows:
            await db.execute(_off(delete(cred).where(cred.id.in_([r[0] for r in cred_rows]))))
            removed.extend((r[1], r[2]) for r in cred_rows)
    if removed:
        items.append(_item("credentials_removed", removed))

    # Runs and requests the account started and that are still open.
    open_runs = select(WorkflowRun.id).where(
        WorkflowRun.user_id == user.id,
        WorkflowRun.status.in_(("pending", "running", "waiting")),
        WorkflowRun.created_at <= now,
    )
    run_ids = list((await db.scalars(open_runs)).all())
    if run_ids:
        # A run executing in another process sees the mark at its next
        # heartbeat and stops, as with a cancel from the run page.
        await db.execute(
            _off(
                update(WorkflowRun)
                .where(WorkflowRun.id.in_(run_ids))
                .values(
                    status="cancelled",
                    error="Cancelled when the account's email address was verified",
                    completed_at=now,
                    suspend_state=None,
                    waiting_on=None,
                    queued_at=None,
                )
            )
        )
        await close_requests_of_runs(db, run_ids)
        items.append({"kind": "runs_cancelled", "count": len(run_ids), "items": []})
    requests = list(
        (
            await db.scalars(
                select(ConfirmationRequest.id).where(
                    ConfirmationRequest.user_id == user.id,
                    ConfirmationRequest.status == "pending",
                    ConfirmationRequest.created_at <= now,
                )
            )
        ).all()
    )
    if requests:
        await db.execute(
            _off(
                update(ConfirmationRequest)
                .where(ConfirmationRequest.id.in_(requests))
                .values(status="expired", responded_at=now)
            )
        )
        items.append({"kind": "requests_closed", "count": len(requests), "items": []})

    # Memberships are listed, not changed.
    memberships = (
        await db.execute(
            select(Organization.id, Organization.name, OrgMembership.role, Organization.owner_id)
            .join(OrgMembership, OrgMembership.org_id == Organization.id)
            .where(OrgMembership.user_id == user.id)
        )
    ).all()
    if memberships:
        items.append(
            {
                "kind": "memberships",
                "count": len(memberships),
                "items": [
                    {"id": oid, "name": name, "role": "owner" if owner_id == user.id else role}
                    for oid, name, role, owner_id in memberships[:_NAMES_SHOWN]
                ],
            }
        )

    # Kept in the notice, restorable from it.
    previous = user.security_notice if isinstance(user.security_notice, dict) else {}
    saved = dict(previous.get("saved") or {})
    if user.system_instructions:
        saved["system_instructions"] = user.system_instructions
        user.system_instructions = None
    if user.default_agent_id:
        saved["default_agent"] = user.default_agent_id
        user.default_agent_id = None

    notice = {
        "at": now.isoformat(),
        "sign_in": sign_in,
        "items": items,
        "saved": saved,
        "dismissed_at": None,
    }
    user.security_notice = notice
    return notice


def notice_for_display(user: User) -> dict[str, Any] | None:
    """The notice as ``/api/auth/me`` returns it; ``None`` once dismissed.

    The saved values are not repeated: only which of them can be restored.
    """
    notice = user.security_notice
    if not isinstance(notice, dict) or notice.get("dismissed_at"):
        return None
    saved = notice.get("saved") or {}
    return {
        "at": notice.get("at"),
        "sign_in": notice.get("sign_in") or {},
        "items": notice.get("items") or [],
        "restorable": [key for key in RESTORABLE if saved.get(key)],
    }
