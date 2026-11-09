"""What a user may see, and what a call may use.

Two layers:

- **The visibility clause** (:func:`visibility_clause`): a resource is
  visible to a user who owns it, or who holds a subscription to it while it
  is still shared (:func:`is_shared_clause`). The share state is re-read on
  every query, so unpublishing takes effect for running conversations too.
  List pages filter with it directly.
- **The use-time rule** (:func:`authorize_use`, :func:`list_usable`,
  :func:`authorize_entry`): what a running agent, workflow node or tool may
  actually use. One rule for every resource kind and every entry point:

  1. *Subject.* A resource reached through a binding (an agent's or a
     workflow's reference) is judged by the binding's owner: the binding is a
     delegation that holds while its owner can still see the resource. With
     no binding the acting principal is the subject.
  2. *Condition.* Visible to the subject, and switched on. Not visible is
     ``resource_not_found`` (existence is not disclosed); switched off is
     ``resource_inactive``.
  3. *Knowledge bases* are not shareable: the subject must own the KB.
  4. *Database connectors* additionally require the acting principal to be
     the connector's owner (``owner_only``): their raw-SQL tools run on the
     owner's database account with no per-caller scoping.

Entry points that still treat a refusal differently from this rule say so at
the call site with :meth:`UseDecision.including`, and each such difference is
registered in ``tests/assembly/matrix.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, or_, select

from .errors import OWNER_ONLY, RESOURCE_INACTIVE, RESOURCE_NOT_FOUND, AssemblyRefusal
from .principal import CallContext, Principal, user_org_ids

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "ResourceKind",
    "UseDecision",
    "authorize_entry",
    "authorize_use",
    "build_visibility_filter",
    "is_shared_clause",
    "list_usable",
    "resolve_visibility",
    "use_clause_for",
    "visibility_clause",
    "visible_clause_for",
]


class ResourceKind(StrEnum):
    """Resource kinds the use-time rule covers.

    The value is the ``ResourceSubscription.resource_type`` of the kind.
    """

    AGENT = "agent"
    CONNECTOR = "connector"
    MCP_SERVER = "mcp_server"
    KNOWLEDGE_BASE = "knowledge_base"
    SKILL = "skill"
    WORKFLOW = "workflow"


_LABELS: dict[ResourceKind, str] = {
    ResourceKind.AGENT: "Agent",
    ResourceKind.CONNECTOR: "Connector",
    ResourceKind.MCP_SERVER: "MCP server",
    ResourceKind.KNOWLEDGE_BASE: "Knowledge base",
    ResourceKind.SKILL: "Skill",
    ResourceKind.WORKFLOW: "Workflow",
}


def _model_for(kind: ResourceKind) -> Any:
    """The ORM model of *kind* (imported lazily, see the package docstring)."""
    if kind is ResourceKind.AGENT:
        from fim_one.db.models.agent import Agent

        return Agent
    if kind is ResourceKind.CONNECTOR:
        from fim_one.db.models.connector import Connector

        return Connector
    if kind is ResourceKind.MCP_SERVER:
        from fim_one.db.models.mcp_server import MCPServer

        return MCPServer
    if kind is ResourceKind.KNOWLEDGE_BASE:
        from fim_one.db.models.knowledge_base import KnowledgeBase

        return KnowledgeBase
    if kind is ResourceKind.SKILL:
        from fim_one.db.models.skill import Skill

        return Skill
    from fim_one.db.models.workflow import Workflow

    return Workflow


# ---------------------------------------------------------------------------
# The visibility clause
# ---------------------------------------------------------------------------


def is_shared_clause(model: Any) -> Any:
    """WHERE clause: *model*'s row is currently shared.

    ``visibility == "org"`` and not awaiting or refused by review. This is
    the condition under which a subscription grants access and under which
    the Market lists and accepts subscriptions to a resource.
    """
    return and_(
        model.visibility == "org",
        or_(
            model.publish_status.is_(None),
            model.publish_status == "approved",
        ),
    )


def visibility_clause(model: Any, user_id: str, subscribed_ids: Sequence[str] | None) -> Any:
    """WHERE clause: rows of *model* visible to *user_id*.

    - the user owns the resource (any visibility), OR
    - the resource is in the user's subscription list **and** is still
      shared (see :func:`is_shared_clause`).

    A subscription does not survive the owner unpublishing: the share state
    is checked here on every query rather than trusted from the subscription
    row.
    """
    conditions = [
        model.user_id == user_id,  # own resources (any visibility)
    ]
    if subscribed_ids:
        conditions.append(and_(model.id.in_(subscribed_ids), is_shared_clause(model)))
    return or_(*conditions)


def build_visibility_filter(
    model: Any,
    user_id: str,
    user_org_ids: list[str],  # kept for backward compat; unused after marketplace redesign
    subscribed_ids: list[str] | None = None,
) -> Any:
    """:func:`visibility_clause` under the signature the list pages call."""
    return visibility_clause(model, user_id, subscribed_ids)


async def resolve_visibility(
    model: Any,
    user_id: str,
    resource_type: str,
    db: AsyncSession,
) -> tuple[Any, list[str], list[str]]:
    """Visibility clause for *user_id*, with the lookups it was built from.

    Parameters
    ----------
    model:
        SQLAlchemy model class (Agent, Connector, MCPServer, etc.)
    user_id:
        The user whose view is wanted.
    resource_type:
        Subscription resource type (``"agent"``, ``"connector"``,
        ``"mcp_server"``, ``"knowledge_base"``, ``"skill"``, ``"workflow"``).
    db:
        Async DB session.

    Returns
    -------
    (filter_clause, user_org_ids, subscribed_ids)
        - ``filter_clause``: ready-to-use SQLAlchemy WHERE clause
        - ``user_org_ids``: list of org IDs the user belongs to
        - ``subscribed_ids``: list of subscribed resource IDs
    """
    from fim_one.db.models.resource_subscription import ResourceSubscription

    org_ids = await user_org_ids(db, user_id)
    sub_result = await db.execute(
        select(ResourceSubscription.resource_id).where(
            ResourceSubscription.user_id == user_id,
            ResourceSubscription.resource_type == resource_type,
        )
    )
    subscribed_ids: list[str] = list(sub_result.scalars().all())
    clause = visibility_clause(model, user_id, subscribed_ids)
    return clause, org_ids, subscribed_ids


async def visible_clause_for(db: AsyncSession, kind: ResourceKind, user_id: str) -> Any:
    """WHERE clause: rows of *kind* visible to *user_id*."""
    clause, _, _ = await resolve_visibility(_model_for(kind), user_id, kind.value, db)
    return clause


# ---------------------------------------------------------------------------
# The use-time rule
# ---------------------------------------------------------------------------


def _not_found(kind: ResourceKind, resource_id: str) -> AssemblyRefusal:
    return AssemblyRefusal(
        RESOURCE_NOT_FOUND,
        404,
        f"{_LABELS[kind]} '{resource_id}' not found or not accessible to you",
    )


def _inactive(kind: ResourceKind, row: Any) -> AssemblyRefusal:
    name = getattr(row, "name", None) or getattr(row, "id", "")
    return AssemblyRefusal(RESOURCE_INACTIVE, 409, f"{_LABELS[kind]} '{name}' is disabled")


def _owner_only(row: Any) -> AssemblyRefusal:
    name = getattr(row, "name", None) or getattr(row, "id", "")
    return AssemblyRefusal(
        OWNER_ONLY, 403, f"Database connector '{name}' can only be used by its owner"
    )


async def use_clause_for(db: AsyncSession, kind: ResourceKind, subject_id: str) -> Any:
    """WHERE clause: rows of *kind* the subject can see for the purpose of use.

    The visibility clause, narrowed for the kinds that cannot be shared.
    Use-time checks start from it, and so does the check made when a
    resource is bound to an agent: what may be bound is what would be usable.
    """
    clause = await visible_clause_for(db, kind, subject_id)
    if kind is ResourceKind.KNOWLEDGE_BASE:
        # Not shareable: a subscription row, should one exist, grants nothing.
        return and_(clause, _model_for(kind).user_id == subject_id)
    return clause


def _row_refusal(kind: ResourceKind, row: Any, *, principal: Principal) -> AssemblyRefusal | None:
    """Why a row the subject can see is still not usable, or ``None``."""
    if not row.is_active:
        return _inactive(kind, row)
    if kind is ResourceKind.CONNECTOR and getattr(row, "type", None) == "database":
        from fim_one.core.security.connector_credentials import db_raw_sql_tool_allowed

        if not db_raw_sql_tool_allowed(row, principal.user_id):
            return _owner_only(row)
    return None


@dataclass
class UseDecision:
    """Outcome of :func:`authorize_use` for a list of resource ids."""

    # Usable ORM rows, in the order the ids were asked for.
    usable: list[Any] = field(default_factory=list)
    # id → why it may not be used.
    refused: dict[str, AssemblyRefusal] = field(default_factory=dict)
    # Rows the subject can see, usable or not, by id. Not part of the
    # contract: it exists for :meth:`including` and goes when the last entry
    # point that needs it is brought under the rule.
    _seen: dict[str, Any] = field(default_factory=dict, repr=False)
    _order: list[str] = field(default_factory=list, repr=False)

    def including(self, *error_codes: str) -> list[Any]:
        """Usable rows plus visible rows refused with one of *error_codes*.

        For an entry point that does not yet apply part of the rule (it
        still serves a switched-off agent, say): the call site names the
        refusal it overrides, and the difference is pinned in the contract
        matrix until the entry point is brought into line.
        """
        rows: list[Any] = []
        for rid in self._order:
            row = self._seen.get(rid)
            if row is None:
                continue
            refusal = self.refused.get(rid)
            if refusal is None or refusal.error_code in error_codes:
                rows.append(row)
        return rows


async def authorize_use(
    db: AsyncSession,
    ctx: CallContext,
    kind: ResourceKind,
    resource_ids: Sequence[str],
    *,
    options: Sequence[Any] = (),
) -> UseDecision:
    """Decide which of *resource_ids* this call may use.

    Never raises for a refusal: the caller decides whether a refused
    resource is dropped or fails the operation. *options* are loader options
    for the row query (eager loads the caller needs after the session
    closes).
    """
    ids = list(dict.fromkeys(resource_ids))
    decision = UseDecision(_order=ids)
    if not ids:
        return decision

    model = _model_for(kind)
    subject_id = ctx.subject_id
    clause = await use_clause_for(db, kind, subject_id)

    # ``is_active`` is read from the row, not filtered in SQL, so that
    # "switched off" and "not visible" stay distinguishable.
    if len(ids) == 1:
        stmt = select(model).where(model.id == ids[0], clause)
        if options:
            stmt = stmt.options(*options)
        row = (await db.execute(stmt)).scalar_one_or_none()
        # Keyed by the id asked for: the query matched on it.
        decision._seen = {ids[0]: row} if row is not None else {}
    else:
        stmt = select(model).where(model.id.in_(ids), clause)
        if options:
            stmt = stmt.options(*options)
        decision._seen = {row.id: row for row in (await db.execute(stmt)).scalars().all()}

    for rid in ids:
        seen = decision._seen.get(rid)
        if seen is None:
            decision.refused[rid] = _not_found(kind, rid)
            continue
        refusal = _row_refusal(kind, seen, principal=ctx.principal)
        if refusal is not None:
            decision.refused[rid] = refusal
        else:
            decision.usable.append(seen)
    return decision


async def list_usable(
    db: AsyncSession,
    ctx: CallContext,
    kind: ResourceKind,
    *,
    options: Sequence[Any] = (),
    columns: Sequence[Any] | None = None,
    where: Sequence[Any] = (),
) -> list[Any]:
    """Everything of *kind* this call may use (auto-discovery).

    Returns ORM rows, or ``Row`` tuples of *columns* when given. *columns*
    is not available for connectors, whose rule reads the row. *where* adds
    conditions of the caller's own (a tool that only offers published
    workflows, say); they narrow the result and are not part of the rule.
    """
    model = _model_for(kind)
    clause = await use_clause_for(db, kind, ctx.subject_id)
    conditions = [clause, model.is_active == True, *where]  # noqa: E712

    if columns is not None:
        if kind is ResourceKind.CONNECTOR:
            raise ValueError(f"list_usable(columns=...) is not supported for {kind.value}")
        col_stmt = select(*columns).where(*conditions)
        return list((await db.execute(col_stmt)).all())

    stmt = select(model).where(*conditions)
    if options:
        stmt = stmt.options(*options)
    rows = (await db.execute(stmt)).scalars().unique().all()
    return [row for row in rows if _row_refusal(kind, row, principal=ctx.principal) is None]


async def authorize_entry(
    db: AsyncSession,
    principal: Principal,
    kind: Literal[ResourceKind.AGENT, ResourceKind.WORKFLOW],
    resource_id: str,
) -> Any:
    """The agent or workflow *principal* is entering, or a refusal.

    An entry resource has no binding: the principal's own view decides.
    Raises :class:`AssemblyRefusal` ``resource_not_found`` (404) or
    ``resource_inactive`` (409); returns the ORM row.
    """
    model = _model_for(kind)
    clause = await visible_clause_for(db, kind, principal.user_id)
    row = (
        await db.execute(select(model).where(model.id == resource_id, clause))
    ).scalar_one_or_none()
    if row is None:
        raise _not_found(kind, resource_id)
    if not row.is_active:
        raise _inactive(kind, row)
    return row
