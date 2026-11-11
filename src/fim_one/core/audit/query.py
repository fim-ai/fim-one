"""The one place that reads audit records.

Four tables hold them: ``audit_logs`` (events), ``login_history``
(sign-ins), ``review_log`` (an organization's review trail) and
``connector_call_logs`` (calls a tool made to an outside resource).
:func:`list_events` reads any one of them as the same :class:`EventRow`,
for one of three scopes:

- ``platform``: a platform administrator. Everything, with the address
  and device the request came from.
- ``org``: an administrator or the owner of an organization
  (:func:`may_read_org_audit`). The records that carry that organization's
  id: events, the review trail, call records. Never an address or a
  device, and never what a member did to their own account.
- ``self``: any user. The security events of their own account, with
  their own address. What an administrator did to the account is shown
  without the administrator's name, address or device.

What a scope may see is decided here and nowhere else: the endpoints pass
the scope and render what comes back.

This module imports nothing from ``fim_one.web``; ORM models are imported
inside functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from . import actions
from .writer import PENDING_SECOND_FACTOR, login_outcome

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "ADMIN_ON_ACCOUNT_ACTIONS",
    "ORG_SOURCES",
    "OWN_ACCOUNT_ACTIONS",
    "SOURCES",
    "EventFilter",
    "EventPage",
    "EventRow",
    "Scope",
    "Source",
    "list_events",
    "may_read_org_audit",
]

Scope = Literal["platform", "org", "self"]
Source = Literal["audit", "login", "review", "call"]

SOURCES: tuple[Source, ...] = ("audit", "login", "review", "call")
# Sign-ins are personal: an organization is not shown them.
ORG_SOURCES: tuple[Source, ...] = ("audit", "review", "call")

# The action a sign-in row reads as, and the prefixes of the other two.
LOGIN_ACTION = "auth.login"
REVIEW_PREFIX = "review."
CALL_PREFIX = "call."

# What a user did to their own account: shown to them, never to their
# organization. The API key names are shared with the administrators'
# endpoints; a row counts when the user is its actor.
OWN_ACCOUNT_ACTIONS: frozenset[str] = frozenset(
    {
        actions.AUTH_REGISTER,
        actions.AUTH_SETUP_ADMIN,
        actions.AUTH_OAUTH_SIGNUP,
        actions.AUTH_PASSWORD_CHANGE,
        actions.AUTH_PASSWORD_SET,
        actions.AUTH_PASSWORD_RESET,
        actions.AUTH_TWO_FACTOR_ENABLE,
        actions.AUTH_TWO_FACTOR_DISABLE,
        actions.AUTH_BACKUP_CODES_REGENERATE,
        actions.AUTH_EMAIL_CHANGE,
        actions.AUTH_OAUTH_BIND,
        actions.AUTH_OAUTH_UNBIND,
        actions.AUTH_USERNAME_CHANGE,
        actions.AUTH_SESSIONS_REVOKE,
        "api_key.create",
        "api_key.enable",
        "api_key.disable",
        "api_key.delete",
    }
)
# What a platform administrator did to somebody's account: the account's
# owner is told that it happened, not by whom or from where.
ADMIN_ON_ACCOUNT_ACTIONS: frozenset[str] = frozenset(
    {
        "user.reset_password",
        "user.force_logout",
        "user.enable",
        "user.disable",
        "user.grant_admin",
        "user.revoke_admin",
        "user.update",
    }
)

# Keys of ``changes`` that hold where a request came from.
_ADDRESS_KEYS = frozenset({"ip", "ip_address", "user_agent"})
_MASKED = "[removed]"

# The roles of an organization that may read its audit records. The one
# place to change when a read-only auditor role is added.
_ORG_AUDIT_ROLES = frozenset({"owner", "admin"})


def may_read_org_audit(role: str | None) -> bool:
    """Whether a member with *role* may read their organization's audit records."""
    return role in _ORG_AUDIT_ROLES


@dataclass(frozen=True)
class EventFilter:
    """What to look for. Every field narrows; an empty one does not."""

    source: Source = "audit"
    action: str | None = None
    actor_id: str | None = None
    org_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    # ``success`` / ``denied`` / ``failed``.
    outcome: str | None = None
    # Platform scope only: the other scopes are not asked about addresses.
    ip_address: str | None = None
    # Whole days, in UTC, both included.
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class EventRow:
    """One record of any of the four tables, as it is shown."""

    id: str
    source: Source
    created_at: datetime | None
    action: str
    actor_id: str | None
    actor_label: str | None
    # Empty on rows written before the column existed: an administrator.
    actor_kind: str | None
    org_id: str | None
    target_type: str | None
    target_id: str | None
    target_label: str | None
    outcome: str
    changes: dict[str, Any] | None
    detail: str | None
    ip_address: str | None
    user_agent: str | None
    # What only this source has: a sign-in's method, a call's request line.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventPage:
    items: list[EventRow]
    total: int


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def _day_bounds(
    date_from: date | None, date_to: date | None, *, aware: bool
) -> tuple[datetime | None, datetime | None]:
    """``[start, end)`` in UTC for the two days, for an aware or a naive column."""
    tz = UTC if aware else None
    start = (
        datetime(date_from.year, date_from.month, date_from.day, tzinfo=tz) if date_from else None
    )
    end = None
    if date_to:
        after = date_to + timedelta(days=1)
        end = datetime(after.year, after.month, after.day, tzinfo=tz)
    return start, end


def _between(column: Any, filters: EventFilter, *, aware: bool = True) -> list[Any]:
    start, end = _day_bounds(filters.date_from, filters.date_to, aware=aware)
    clauses = []
    if start is not None:
        clauses.append(column >= start)
    if end is not None:
        clauses.append(column < end)
    return clauses


# ---------------------------------------------------------------------------
# What a scope is shown of a row
# ---------------------------------------------------------------------------


def _without(text: str | None, *values: str | None) -> str | None:
    """*text* with every one of *values* cut out of it."""
    if not text:
        return text
    for value in values:
        if value and value in text:
            text = text.replace(value, _MASKED)
    return text


def _changes_without(changes: Any, *values: str | None) -> dict[str, Any] | None:
    """*changes* with no address key, and none of *values* in a string of it."""
    if not isinstance(changes, dict):
        return None

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            return _without(value, *values)
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if str(k) not in _ADDRESS_KEYS}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    scrubbed = scrub(changes)
    return scrubbed or None


def _for_org(row: EventRow) -> EventRow:
    """*row* as an organization's administrator is shown it.

    No address and no device: not in their own fields, and not where the
    row quotes them (the target's name, a detail line, a value in ``changes``).
    """
    return replace(
        row,
        ip_address=None,
        user_agent=None,
        target_label=_without(row.target_label, row.ip_address, row.user_agent),
        detail=_without(row.detail, row.ip_address, row.user_agent),
        changes=_changes_without(row.changes, row.ip_address, row.user_agent),
        extra={k: v for k, v in row.extra.items() if k not in _ADDRESS_KEYS},
    )


def _for_self(row: EventRow, user_id: str) -> EventRow:
    """*row* as the owner of the account is shown it.

    Their own action keeps their own address. What somebody else did to the
    account says only that an administrator did it.
    """
    if row.actor_id == user_id:
        return row
    return replace(
        row,
        actor_id=None,
        actor_label=None,
        actor_kind="admin",
        detail=None,
        ip_address=None,
        user_agent=None,
        changes=_changes_without(row.changes, row.ip_address, row.user_agent),
        extra={},
    )


# ---------------------------------------------------------------------------
# The four tables
# ---------------------------------------------------------------------------


def _audit_query(
    scope: Scope, filters: EventFilter, org_id: str | None, user_id: str | None
) -> tuple[Any, list[Any]]:
    from sqlalchemy import and_, not_, or_

    from fim_one.db.models.audit_log import AuditLog

    clauses: list[Any] = _between(AuditLog.created_at, filters)
    if filters.action:
        clauses.append(AuditLog.action == filters.action)
    if filters.actor_id:
        clauses.append(AuditLog.actor_id == filters.actor_id)
    if filters.target_type:
        clauses.append(AuditLog.target_type == filters.target_type)
    if filters.target_id:
        clauses.append(AuditLog.target_id == filters.target_id)
    if filters.outcome:
        clauses.append(AuditLog.outcome == filters.outcome)

    if scope == "platform":
        if filters.org_id:
            clauses.append(AuditLog.org_id == filters.org_id)
        if filters.ip_address:
            clauses.append(AuditLog.ip_address == filters.ip_address)
    elif scope == "org":
        clauses.append(AuditLog.org_id == org_id)
        # What a member did to their own account is theirs, whatever
        # organization id a row of it might carry.
        clauses.append(not_(AuditLog.action.in_(sorted(OWN_ACCOUNT_ACTIONS))))
        clauses.append(AuditLog.action != actions.ACCOUNT_SELF_DELETE)
    else:
        clauses.append(
            or_(
                and_(
                    AuditLog.actor_id == user_id,
                    AuditLog.action.in_(sorted(OWN_ACCOUNT_ACTIONS)),
                ),
                and_(
                    AuditLog.target_type == "user",
                    AuditLog.target_id == user_id,
                    AuditLog.action.in_(sorted(ADMIN_ON_ACCOUNT_ACTIONS)),
                ),
            )
        )
    return AuditLog, clauses


def _audit_row(r: Any) -> EventRow:
    return EventRow(
        id=r.id,
        source="audit",
        created_at=r.created_at,
        action=r.action,
        actor_id=r.actor_id,
        actor_label=r.actor_label,
        actor_kind=r.actor_kind,
        org_id=r.org_id,
        target_type=r.target_type,
        target_id=r.target_id,
        target_label=r.target_label,
        outcome=r.outcome or "success",
        changes=r.changes,
        detail=r.detail,
        ip_address=r.ip_address,
        user_agent=r.user_agent,
    )


def _login_query(filters: EventFilter) -> tuple[Any, list[Any]] | None:
    from sqlalchemy import and_, or_

    from fim_one.db.models.login_history import LoginHistory

    if filters.org_id or filters.target_type or filters.target_id:
        return None  # a sign-in has no organization and no target
    if filters.action and filters.action != LOGIN_ACTION:
        return None
    clauses: list[Any] = _between(LoginHistory.created_at, filters)
    if filters.actor_id:
        clauses.append(LoginHistory.user_id == filters.actor_id)
    if filters.ip_address:
        clauses.append(LoginHistory.ip_address == filters.ip_address)
    if filters.outcome:
        # A first step that still owes the second factor is neither.
        settled = or_(
            LoginHistory.failure_reason.is_(None),
            LoginHistory.failure_reason != PENDING_SECOND_FACTOR,
        )
        if filters.outcome == "success":
            clauses.append(and_(LoginHistory.success.is_(True), settled))
        elif filters.outcome == "failed":
            clauses.append(and_(LoginHistory.success.is_(False), settled))
        else:
            return None
    return LoginHistory, clauses


def _login_row(r: Any) -> EventRow:
    return EventRow(
        id=r.id,
        source="login",
        created_at=r.created_at,
        action=LOGIN_ACTION,
        actor_id=r.user_id,
        actor_label=r.username or r.email,
        actor_kind="user" if r.user_id else "anonymous",
        org_id=None,
        target_type=None,
        target_id=None,
        target_label=None,
        outcome=login_outcome(r.success, r.failure_reason),
        changes=None,
        detail=r.failure_reason,
        ip_address=r.ip_address,
        user_agent=r.user_agent,
        extra={"method": r.method},
    )


def _review_query(
    scope: Scope, filters: EventFilter, org_id: str | None
) -> tuple[Any, list[Any]] | None:
    from fim_one.db.models.review_log import ReviewLog

    if filters.ip_address or (filters.outcome and filters.outcome != "success"):
        return None  # a review entry has no address and is never a failure
    # ``created_at`` of this table carries no timezone.
    clauses: list[Any] = _between(ReviewLog.created_at, filters, aware=False)
    if filters.action:
        if not filters.action.startswith(REVIEW_PREFIX):
            return None
        clauses.append(ReviewLog.action == filters.action[len(REVIEW_PREFIX) :])
    if filters.actor_id:
        clauses.append(ReviewLog.actor_id == filters.actor_id)
    if filters.target_type:
        clauses.append(ReviewLog.resource_type == filters.target_type)
    if filters.target_id:
        clauses.append(ReviewLog.resource_id == filters.target_id)
    if scope == "org":
        clauses.append(ReviewLog.org_id == org_id)
    elif filters.org_id:
        clauses.append(ReviewLog.org_id == filters.org_id)
    return ReviewLog, clauses


def _review_row(r: Any) -> EventRow:
    return EventRow(
        id=r.id,
        source="review",
        created_at=r.created_at,
        action=REVIEW_PREFIX + r.action,
        actor_id=r.actor_id,
        actor_label=r.actor_username,
        actor_kind="user",
        org_id=r.org_id,
        target_type=r.resource_type,
        target_id=r.resource_id,
        target_label=r.resource_name,
        outcome="success",
        changes=None,
        detail=r.note,
        ip_address=None,
        user_agent=None,
    )


def _call_query(
    scope: Scope, filters: EventFilter, org_id: str | None
) -> tuple[Any, list[Any]] | None:
    from sqlalchemy import or_

    from fim_one.db.models.connector_call_log import ConnectorCallLog

    if filters.ip_address:
        return None  # a call record has no address
    clauses: list[Any] = _between(ConnectorCallLog.created_at, filters)
    kind = filters.target_type
    if filters.action:
        if not filters.action.startswith(CALL_PREFIX):
            return None
        named = filters.action[len(CALL_PREFIX) :]
        if kind and kind != named:
            return None
        kind = named
    if kind == "connector":
        # Rows written before the column existed are connector calls.
        clauses.append(
            or_(
                ConnectorCallLog.resource_kind.is_(None),
                ConnectorCallLog.resource_kind == "connector",
            )
        )
    elif kind:
        clauses.append(ConnectorCallLog.resource_kind == kind)
    if filters.actor_id:
        clauses.append(ConnectorCallLog.user_id == filters.actor_id)
    if filters.target_id:
        clauses.append(
            or_(
                ConnectorCallLog.connector_id == filters.target_id,
                ConnectorCallLog.mcp_server_id == filters.target_id,
            )
        )
    if filters.outcome == "success":
        clauses.append(ConnectorCallLog.success.is_(True))
    elif filters.outcome == "failed":
        clauses.append(ConnectorCallLog.success.is_(False))
    elif filters.outcome:
        return None
    if scope == "org":
        clauses.append(ConnectorCallLog.org_id == org_id)
    elif filters.org_id:
        clauses.append(ConnectorCallLog.org_id == filters.org_id)
    return ConnectorCallLog, clauses


def _call_row(r: Any, scope: Scope) -> EventRow:
    kind = r.resource_kind or "connector"
    # The error text is the start of what the other system answered. A
    # platform administrator is shown it; an organization is told that the
    # call failed and with what status.
    detail = r.error_message if scope == "platform" else None
    return EventRow(
        id=r.id,
        source="call",
        created_at=r.created_at,
        action=CALL_PREFIX + kind,
        actor_id=r.user_id,
        actor_label=None,
        actor_kind="user" if r.user_id else None,
        org_id=r.org_id,
        target_type=kind,
        target_id=r.mcp_server_id if kind == "mcp" else r.connector_id,
        target_label=r.connector_name,
        outcome="success" if r.success else "failed",
        changes=None,
        detail=detail,
        ip_address=None,
        user_agent=None,
        extra={
            "action_name": r.action_name,
            "request_method": r.request_method,
            "request_url": r.request_url,
            "response_status": r.response_status,
            "response_time_ms": r.response_time_ms,
            "entry": r.entry,
            "credential_source": r.credential_source,
            "conversation_id": r.conversation_id,
            "workflow_run_id": r.workflow_run_id,
            "agent_id": r.agent_id,
        },
    )


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


async def list_events(
    db: AsyncSession,
    *,
    scope: Scope,
    filters: EventFilter | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> EventPage:
    """Records of one source that *scope* may see, newest first, with their count.

    ``org`` needs *org_id* and ``self`` needs *user_id*: the organization
    and the user whose records are read, which the caller has already
    established the reader's right to. A source the scope is not shown
    (sign-ins to an organization, anything but events to ``self``) raises
    ``ValueError``.
    """
    from sqlalchemy import func, select

    filters = filters or EventFilter()
    source = filters.source
    if scope == "org":
        if not org_id:
            raise ValueError("the organization scope needs an organization")
        if source not in ORG_SOURCES:
            raise ValueError(f"an organization is not shown {source!r} records")
    elif scope == "self":
        if not user_id:
            raise ValueError("the self scope needs a user")
        if source != "audit":
            raise ValueError(f"the self scope is not shown {source!r} records")
    if scope != "platform" and filters.ip_address:
        # Not a filter these scopes have: a match would say that an address
        # is in a row they are not shown it in.
        filters = replace(filters, ip_address=None)

    built: tuple[Any, list[Any]] | None
    if source == "audit":
        built = _audit_query(scope, filters, org_id, user_id)
    elif source == "login":
        built = _login_query(filters)
    elif source == "review":
        built = _review_query(scope, filters, org_id)
    else:
        built = _call_query(scope, filters, org_id)
    if built is None:
        return EventPage(items=[], total=0)
    model, clauses = built

    total = (await db.execute(select(func.count()).select_from(model).where(*clauses))).scalar_one()
    rows = (
        (
            await db.execute(
                select(model)
                .where(*clauses)
                # The id settles the order of rows written in the same second.
                .order_by(model.created_at.desc(), model.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    items: list[EventRow]
    if source == "audit":
        items = [_audit_row(r) for r in rows]
    elif source == "login":
        items = [_login_row(r) for r in rows]
    elif source == "review":
        items = [_review_row(r) for r in rows]
    else:
        items = [_call_row(r, scope) for r in rows]

    if scope == "org":
        items = [_for_org(row) for row in items]
    elif scope == "self":
        assert user_id is not None
        items = [_for_self(row, user_id) for row in items]
    return EventPage(items=items, total=int(total))
