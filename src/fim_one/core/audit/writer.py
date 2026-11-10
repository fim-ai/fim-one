"""The one place that writes audit rows.

Three tables hold audit-class records: ``audit_logs`` (the general event
table), ``login_history`` (sign-in attempts) and ``review_log`` (the
publish-and-review trail of an organization). Each has one writer here:
:func:`record`, :func:`record_login`, :func:`record_review`. Nothing else
constructs their rows (``tests/test_architecture.py``).

Two ways to write, chosen by the caller of :func:`record`:

- with ``db``: the row joins the caller's session and is committed with the
  change it describes. If it cannot be written, the change fails too. For
  changes to permissions and secrets, where a change with no record is the
  worse outcome.
- without ``db``: the row is written in a session of its own; a failure is
  logged and swallowed. For events whose main transaction rolls back (a
  refusal) and for events that must not be held up by auditing (an export,
  a view).

This module imports nothing from ``fim_one.web``; ORM models are imported
inside functions.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .actions import is_registered
from .context import current_request_auth, current_request_meta
from .events import Actor, EventContext, Outcome, RequestMeta

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fim_one.core.assembly.principal import CallContext, Principal

__all__ = [
    "actor_from_principal",
    "actor_of",
    "record",
    "record_login",
    "record_review",
    "system_actor",
    "typed_identifier",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------


def actor_of(user: Any) -> Actor:
    """The actor for a signed-in user's row (``id``, ``username``, ``email``, ``is_admin``).

    How the user was authenticated comes from the current request, when the
    authentication dependency recorded it.
    """
    auth = current_request_auth()
    return Actor(
        user_id=getattr(user, "id", None),
        label=getattr(user, "username", None) or getattr(user, "email", None),
        kind="admin" if getattr(user, "is_admin", False) else "user",
        auth=auth[0] if auth else None,
        credential_ref=auth[1] if auth else None,
    )


def actor_from_principal(
    principal: Principal, *, label: str | None = None, credential_ref: str | None = None
) -> Actor:
    """The actor for an assembly-layer principal."""
    auth = str(principal.auth)
    kind: Any = "system" if auth == "system" else ("admin" if principal.is_admin else "user")
    return Actor(
        user_id=principal.user_id or None,
        label=label,
        kind=kind,
        auth=auth,
        credential_ref=credential_ref,
    )


def system_actor(name: str) -> Actor:
    """The platform acting on its own: a sweep, a billing event, a scheduled job."""
    return Actor(user_id=None, label=name, kind="system", auth="system")


# ---------------------------------------------------------------------------
# audit_logs
# ---------------------------------------------------------------------------


def _own_session() -> Any:
    """A session for a record written outside the caller's transaction."""
    from fim_one.db import create_session

    return create_session()


def _strict_actions() -> bool:
    """Whether an unregistered action name is an error (it is under test)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _check_registered(action: str) -> None:
    if is_registered(action):
        return
    if _strict_actions():
        raise ValueError(
            f"audit action {action!r} is not registered in fim_one.core.audit.actions"
        )
    logger.warning("Audit action %r is not registered; written as it is", action)


def _fit(value: Any, limit: int) -> str | None:
    """*value* as text no longer than the column that stores it.

    PostgreSQL rejects an over-long value, and the record with it.
    """
    if value is None:
        return None
    return str(value)[:limit]


def _without_url_secrets(detail: str | None) -> str | None:
    """*detail* with the credential cut from any URL it quotes.

    Free text is where a webhook URL or an error message that quotes one
    ends up. The stripper is the one call records and logs go through.
    """
    if not detail or ("//" not in detail and "?" not in detail):
        return detail
    from fim_one.core.assembly.mcp_address import strip_url_secrets

    return strip_url_secrets(detail)


async def record(
    action: str,
    *,
    actor: Actor,
    org_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
    outcome: Outcome = "success",
    changes: Mapping[str, Mapping[str, Any]] | None = None,
    detail: str | None = None,
    context: CallContext | EventContext | None = None,
    request: RequestMeta | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Write one audit event.

    With *db* the row is added to that session and the caller commits it
    together with the change; without, it is committed in a session of its
    own and a failure is logged, never raised.

    Where the request came from is read from the request being served
    (:mod:`.context`); *request* overrides it. An event whose actor is the
    system carries none unless *request* is given. *context* supplies the
    conversation, workflow run and agent the event happened in. *changes*
    comes from :func:`fim_one.core.audit.redact.diff_changes`, which keeps
    secrets out of it; a URL quoted in *detail* loses its user-info, query
    string and token-shaped path segments.
    """
    from fim_one.db.models.audit_log import AuditLog

    _check_registered(action)
    meta = request
    if meta is None and actor.kind != "system":
        # The platform acting on its own did not come from an address. A
        # sweep or a task started while a request was being served would
        # otherwise carry that request's address and id.
        meta = current_request_meta()
    row = AuditLog(
        actor_id=actor.user_id,
        # NOT NULL in the database; the system and an anonymous caller
        # have no name of their own.
        actor_label=_fit(actor.label or actor.kind, 255),
        actor_kind=actor.kind,
        auth_method=_fit(actor.auth, 20),
        credential_ref=_fit(actor.credential_ref, 64),
        action=action,
        org_id=org_id,
        target_type=_fit(target_type, 50),
        target_id=_fit(target_id, 36),
        target_label=_fit(target_label, 255),
        outcome=outcome,
        changes={name: dict(change) for name, change in changes.items()} if changes else None,
        detail=_without_url_secrets(detail),
        ip_address=_fit(meta.ip, 45) if meta else None,
        user_agent=_fit(meta.user_agent, 500) if meta else None,
        request_id=_fit(meta.request_id, 36) if meta else None,
        conversation_id=context.conversation_id if context else None,
        workflow_run_id=context.workflow_run_id if context else None,
        agent_id=context.agent_id if context else None,
    )
    if db is not None:
        db.add(row)
        return
    try:
        async with _own_session() as session:
            session.add(row)
            await session.commit()
    except Exception as exc:
        # Only the error's type: its text can echo the row.
        logger.error("Failed to record audit event %s: %s", action, type(exc).__name__)


# ---------------------------------------------------------------------------
# login_history
# ---------------------------------------------------------------------------


_EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def typed_identifier(value: str | None) -> str | None:
    """What may be stored of the name typed for an account that does not exist.

    People type a password into the email field. The value is kept when it
    is shaped like an email address and not like a credential; otherwise
    only that something was typed, and how long it was.
    """
    if not value:
        return value
    from fim_one.core.assembly.mcp_address import value_is_secret_shaped

    text = str(value)
    local = text.split("@", 1)[0]
    if len(text) <= 255 and _EMAIL_SHAPE.fullmatch(text) and not value_is_secret_shaped(local):
        return text
    return f"(not an email address; {len(text)} characters)"


async def record_login(
    *,
    user_id: str | None,
    username: str | None,
    email: str | None,
    success: bool,
    method: str | None = None,
    failure_reason: str | None = None,
    request: RequestMeta | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Write one sign-in attempt.

    *method* is how it was attempted: ``password``, ``email_code`` or
    ``oauth:<provider>``.

    *request* defaults to the request being served. With *db* the row joins
    that session (the caller commits); without, it is written in a session
    of its own and a failure is logged, never raised.
    """
    from fim_one.db.models.login_history import LoginHistory

    meta = request if request is not None else current_request_meta()
    row = LoginHistory(
        user_id=user_id,
        username=_fit(username, 50),
        # For an account that exists this is its own address. For one that
        # does not it is whatever was typed into the field.
        email=_fit(email, 255) if user_id is not None else typed_identifier(email),
        ip_address=_fit(meta.ip, 45) if meta else None,
        user_agent=meta.user_agent if meta else None,
        success=success,
        failure_reason=_fit(failure_reason, 100),
        method=_fit(method, 40),
    )
    if db is not None:
        db.add(row)
        return
    try:
        async with _own_session() as session:
            session.add(row)
            await session.commit()
    except Exception as exc:
        logger.error("Failed to record a sign-in attempt: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# review_log
# ---------------------------------------------------------------------------


async def record_review(
    db: AsyncSession,
    *,
    org_id: str,
    resource_type: str,
    resource_id: str,
    resource_name: str,
    action: str,
    actor: Actor | None,
    note: str | None = None,
) -> None:
    """Add one entry to an organization's review trail. The caller commits."""
    from fim_one.db.models.review_log import ReviewLog

    db.add(
        ReviewLog(
            org_id=org_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            action=action,
            actor_id=actor.user_id if actor else None,
            actor_username=actor.label if actor else None,
            note=note,
        )
    )
