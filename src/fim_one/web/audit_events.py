"""Recording an audit event from an endpoint, in its transaction.

Thin shapes over :func:`fim_one.core.audit.writer.record` for the two
cases endpoints have over and over: a user acting on their own account,
and a user acting on a resource row (an agent, a connector, a workflow …).
Both add the record to the endpoint's session, so it is committed with the
change it describes; call them before ``db.commit()``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.audit import writer as audit
from fim_one.core.audit.events import Outcome
from fim_one.core.audit.redact import CHANGED, diff_changes

__all__ = [
    "label_of",
    "record_account",
    "record_plan_change",
    "record_resource",
    "record_resource_change",
    "record_unpublish_by_other",
    "snapshot",
]


def label_of(user: Any) -> str | None:
    """What a user is called in a record: the username, or the email."""
    label: str | None = getattr(user, "username", None) or getattr(user, "email", None)
    return label


async def record_account(
    db: AsyncSession,
    user: Any,
    action: str,
    *,
    changes: Mapping[str, Mapping[str, Any]] | None = None,
    detail: str | None = None,
    auth: str | None = None,
) -> None:
    """*user* changed something about their own account.

    *auth* says how the user proved who they are when the request was not a
    signed-in one (a password reset by emailed code).
    """
    actor = audit.actor_of(user)
    if auth is not None:
        actor = replace(actor, auth=auth, credential_ref=None)
    await audit.record(
        action,
        actor=actor,
        target_type="user",
        target_id=user.id,
        target_label=label_of(user),
        changes=changes,
        detail=detail,
        db=db,
    )


async def record_resource(
    db: AsyncSession,
    user: Any,
    action: str,
    resource_type: str,
    resource: Any,
    *,
    org_id: str | None = None,
    changes: Mapping[str, Mapping[str, Any]] | None = None,
    detail: str | None = None,
    outcome: Outcome = "success",
) -> None:
    """*user* did *action* to *resource* (a row with ``id`` and ``name``).

    The event belongs to the organization the resource is published to
    (``resource.org_id``) unless *org_id* says otherwise.
    """
    if getattr(resource, "id", None) is None:
        # A row just added gets its id when it is flushed.
        await db.flush()
    await audit.record(
        action,
        actor=audit.actor_of(user),
        org_id=org_id if org_id is not None else getattr(resource, "org_id", None),
        target_type=resource_type,
        target_id=resource.id,
        target_label=getattr(resource, "name", None),
        outcome=outcome,
        changes=changes,
        detail=detail,
        db=db,
    )


def snapshot(resource: Any, fields: Collection[str]) -> dict[str, Any]:
    """The values of *fields* on *resource* now, for comparing after a change."""
    state: dict[str, Any] = {}
    for field in fields:
        value = getattr(resource, field, None)
        # A JSON column hands out the list it will be mutated through.
        state[field] = list(value) if isinstance(value, list) else value
    return state


async def record_resource_change(
    db: AsyncSession,
    user: Any,
    action: str,
    resource_type: str,
    resource: Any,
    before: Mapping[str, Any],
    *,
    secret_fields: Collection[str] = (),
    also_changed: Collection[str] = (),
    detail: str | None = None,
    org_id: str | None = None,
) -> bool:
    """Record what changed on *resource* among the fields *before* holds.

    Nothing is recorded when none of them changed: an edit of a resource's
    content (its name, its instructions) is not a security event, a change
    of who or what it may reach is. *also_changed* names secrets that were
    replaced and are not fields of the row (a stored credential); each is
    recorded as changed and nothing more. Returns whether a record was
    written.
    """
    changes = (
        diff_changes(before, snapshot(resource, list(before)), secret_fields=secret_fields) or {}
    )
    changes.update({name: dict(CHANGED) for name in also_changed})
    if not changes:
        return False
    await record_resource(
        db, user, action, resource_type, resource, org_id=org_id, changes=changes, detail=detail
    )
    return True


async def record_unpublish_by_other(
    db: AsyncSession, user: Any, action: str, resource_type: str, resource: Any
) -> None:
    """Someone other than its owner took *resource* back out of its organization.

    An organization's administrator or a platform administrator may do so.
    The owner unpublishing their own resource is in the review trail and
    needs no more; this records who unpublished whose. Call it while the
    resource still names its organization.
    """
    owner_id = getattr(resource, "user_id", None)
    if owner_id == user.id:
        return
    await record_resource(
        db, user, action, resource_type, resource, detail=f"owner={owner_id}"
    )


async def record_plan_change(
    db: AsyncSession,
    user: Any,
    action: str,
    *,
    plan_from: int | str | None,
    plan_to: int | str | None,
    source: str,
    detail: str | None = None,
) -> None:
    """*user*'s plan was changed by the platform itself, not by a person.

    *source* names what did it (the payment processor's event, the
    period-end sweep): it is the actor. Nothing is recorded when the plan
    is the one the user already had. Committed with the change.
    """
    if plan_from == plan_to:
        return
    await audit.record(
        action,
        actor=audit.system_actor(source),
        target_type="user",
        target_id=user.id,
        target_label=label_of(user),
        changes=diff_changes({"plan_id": plan_from}, {"plan_id": plan_to}),
        detail=detail,
        db=db,
    )
