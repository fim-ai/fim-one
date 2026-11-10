"""Recording what was done to a resource, the same way from every entry.

An agent, a connector, one of its actions, an MCP server or a database
connector's schema can be changed by an HTTP route and by a builder tool an
agent calls in a chat. Both record through the functions here, with the
field lists here, so that the two paths cannot drift: which fields are
compared, which are secret, and how a value is made safe to store are
decided once.

Callers add the record to their own session and commit it with the change.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from . import actions
from . import writer as audit
from .events import Actor, Outcome
from .redact import CHANGED, diff_changes, safe_path

__all__ = [
    "ACTION_AUDITED_FIELDS",
    "AGENT_AUDITED_FIELDS",
    "CONNECTOR_AUDITED_FIELDS",
    "FENCE_FIELDS",
    "THROUGH_THE_BUILDER",
    "action_deleted_detail",
    "agent_state",
    "builder_actor",
    "completion_channel_id",
    "connector_created_changes",
    "note_fence",
    "record_fences",
    "record_resource",
    "record_resource_change",
    "snapshot",
]

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
# ... of a connector: whether it is on, where it calls, how it signs in, and
# whether others may borrow the owner's credential. The credential itself is
# recorded only as changed.
CONNECTOR_AUDITED_FIELDS = ("is_active", "allow_fallback", "type", "base_url", "auth_type")
# ... of one of its actions: what it calls and whether a human is asked first.
ACTION_AUDITED_FIELDS = ("requires_confirmation", "method", "path")
# The switches on a table or column of a database connector that decide what
# the model is shown: hidden or visible, masked as personal data or not.
FENCE_FIELDS = ("is_visible", "is_pii")
_MAX_RECORDED_FENCES = 50

# Marks a record written by a builder tool an agent called in a chat, as
# opposed to the same change made through the portal's forms.
THROUGH_THE_BUILDER = "through the builder"


def snapshot(resource: Any, fields: Collection[str]) -> dict[str, Any]:
    """The values of *fields* on *resource* now, for comparing after a change."""
    state: dict[str, Any] = {}
    for field in fields:
        value = getattr(resource, field, None)
        # A JSON column hands out the list it will be mutated through.
        state[field] = list(value) if isinstance(value, list) else value
    return state


def completion_channel_id(model_config_json: Any) -> str | None:
    """The channel an agent config sends completion notifications to, if enabled."""
    try:
        on_complete = model_config_json["notifications"]["on_complete"]
        channel_id = on_complete["channel_id"] if on_complete.get("enabled") else None
    except (KeyError, TypeError, AttributeError):
        return None
    return channel_id if isinstance(channel_id, str) and channel_id else None


def agent_state(agent: Any) -> dict[str, Any]:
    """What an audit record compares of an agent.

    Its audited fields, and the channel its completion notifications go
    to: that one is kept inside the model config, and decides where what
    the agent produced is sent.
    """
    return {
        **snapshot(agent, AGENT_AUDITED_FIELDS),
        "completion_channel_id": completion_channel_id(agent.model_config_json),
    }


async def builder_actor(db: Any, user_id: str) -> Actor:
    """The actor for a builder tool: the person chatting with the builder agent.

    How they were signed in comes from the chat request the tool runs in.
    """
    from sqlalchemy import select

    from fim_one.db.models.user import User

    found = (
        await db.execute(
            select(User.username, User.email, User.is_admin).where(User.id == user_id)
        )
    ).one_or_none()
    user = type("_User", (), {})()
    user.id = user_id
    user.username, user.email, user.is_admin = found if found is not None else (None, None, False)
    return audit.actor_of(user)


async def record_resource(
    db: Any,
    actor: Actor,
    action: str,
    resource_type: str,
    resource: Any,
    *,
    org_id: str | None = None,
    changes: Mapping[str, Mapping[str, Any]] | None = None,
    detail: str | None = None,
    outcome: Outcome = "success",
) -> None:
    """*actor* did *action* to *resource* (a row with ``id`` and ``name``).

    The event belongs to the organization the resource is published to
    (``resource.org_id``) unless *org_id* says otherwise.
    """
    if getattr(resource, "id", None) is None:
        # A row just added gets its id when it is flushed.
        await db.flush()
    await audit.record(
        action,
        actor=actor,
        org_id=org_id if org_id is not None else getattr(resource, "org_id", None),
        target_type=resource_type,
        target_id=resource.id,
        target_label=getattr(resource, "name", None),
        outcome=outcome,
        changes=changes,
        detail=detail,
        db=db,
    )


async def record_resource_change(
    db: Any,
    actor: Actor,
    action: str,
    resource_type: str,
    resource: Any,
    before: Mapping[str, Any],
    *,
    secret_fields: Collection[str] = (),
    also_changed: Collection[str] = (),
    detail: str | None = None,
    org_id: str | None = None,
    after: Mapping[str, Any] | None = None,
) -> bool:
    """Record what changed on *resource* among the fields *before* holds.

    Nothing is recorded when none of them changed: an edit of a resource's
    content (its name, its instructions) is not a security event, a change
    of who or what it may reach is. *also_changed* names secrets that were
    replaced and are not fields of the row (a stored credential); each is
    recorded as changed and nothing more. *after* is the state to compare
    with when it is not simply the fields of the row (a value kept inside a
    JSON column). Returns whether a record was written.
    """
    now = after if after is not None else snapshot(resource, list(before))
    changes = diff_changes(before, now, secret_fields=secret_fields) or {}
    changes.update({name: dict(CHANGED) for name in also_changed})
    if not changes:
        return False
    await record_resource(
        db, actor, action, resource_type, resource, org_id=org_id, changes=changes, detail=detail
    )
    return True


async def connector_created_changes(db: Any, connector: Any) -> dict[str, dict[str, Any]] | None:
    """The ``changes`` of a new connector, however it came to exist.

    Its audited fields from nothing (where it calls, how it signs in,
    whether others may borrow the owner's credential), and whether a default
    credential was stored with it; the credential itself is not recorded.
    """
    from sqlalchemy import select

    from fim_one.db.models import ConnectorCredential

    await db.flush()
    changes = diff_changes(None, snapshot(connector, CONNECTOR_AUDITED_FIELDS)) or {}
    stored = (
        await db.execute(
            select(ConnectorCredential.id).where(
                ConnectorCredential.connector_id == connector.id,
                ConnectorCredential.user_id.is_(None),
            )
        )
    ).first()
    if stored is not None:
        changes["default_credential"] = dict(CHANGED)
    return changes or None


def action_deleted_detail(connector_id: str, action: Any) -> str:
    """The detail of a deleted action: what it called, never its raw path."""
    return f"connector={connector_id}; {action.method} {safe_path(action.path)}"


def note_fence(
    fences: dict[str, dict[str, Any]], owner: str, field: str, before: Any, after: Any
) -> None:
    """Remember a fence switch of table or column *owner* that is being changed."""
    if field in FENCE_FIELDS and bool(before) != bool(after):
        fences[f"{owner}.{field}"] = {"from": bool(before), "to": bool(after)}


async def record_fences(
    db: Any,
    actor: Actor,
    connector: Any,
    fences: dict[str, dict[str, Any]],
    *,
    detail: str | None = None,
) -> None:
    """Record the fence switches a save changed; nothing when it changed none."""
    if not fences:
        return
    count = f"{len(fences)} switch(es) changed"
    await record_resource(
        db,
        actor,
        actions.CONNECTOR_SCHEMA_FENCE_UPDATE,
        "connector",
        connector,
        changes=dict(list(fences.items())[:_MAX_RECORDED_FENCES]),
        detail=f"{count}; {detail}" if detail else count,
    )
