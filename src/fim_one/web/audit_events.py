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
from fim_one.core.audit.context import current_request_meta
from fim_one.core.audit.events import Outcome
from fim_one.core.audit import resources
from fim_one.core.audit.redact import diff_changes
from fim_one.core.audit.resources import snapshot

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
    """*user* did *action* to *resource*; see :func:`fim_one.core.audit.resources.record_resource`."""
    await resources.record_resource(
        db,
        audit.actor_of(user),
        action,
        resource_type,
        resource,
        org_id=org_id,
        changes=changes,
        detail=detail,
        outcome=outcome,
    )


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
    after: Mapping[str, Any] | None = None,
) -> bool:
    """What *user* changed on *resource*; see the function of the same name in
    :mod:`fim_one.core.audit.resources`, which the builder tools call too."""
    return await resources.record_resource_change(
        db,
        audit.actor_of(user),
        action,
        resource_type,
        resource,
        before,
        secret_fields=secret_fields,
        also_changed=also_changed,
        detail=detail,
        org_id=org_id,
        after=after,
    )


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
    if owner_id == user.id or not getattr(resource, "org_id", None):
        # The owner's own, or nothing was published: nothing was taken back.
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
        # The payment processor's call does come from an address; the
        # period-end sweep runs outside any request and has none.
        request=current_request_meta(),
        db=db,
    )
