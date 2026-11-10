"""Recording an audit event from an endpoint, in its transaction.

Thin shapes over :func:`fim_one.core.audit.writer.record` for the two
cases endpoints have over and over: a user acting on their own account,
and a user acting on a resource row (an agent, a connector, a workflow …).
Both add the record to the endpoint's session, so it is committed with the
change it describes; call them before ``db.commit()``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.core.audit import writer as audit
from fim_one.core.audit.events import Outcome

__all__ = ["label_of", "record_account", "record_resource"]


def label_of(user: Any) -> str | None:
    """What a user is called in a record: the username, or the email."""
    label: str | None = user.username or user.email
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
