"""Who is acting, and through what.

:class:`Principal` is the identity a rule is checked against; every
``Principal`` in the code base is built in this module. :class:`Binding`
names the agent or workflow a resource is being used through, and
:class:`CallContext` carries both plus where the call entered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from .errors import ACCOUNT_DISABLED, AssemblyRefusal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "AuthMethod",
    "Binding",
    "CallContext",
    "Entry",
    "Principal",
    "provisional_principal",
    "resolve_external_actor",
    "resolve_principal",
    "user_org_ids",
]


class AuthMethod(StrEnum):
    """How the acting identity was established."""

    SESSION = "session"
    API_KEY = "api_key"
    TRIGGER_KEY = "trigger_key"
    SCHEDULE = "schedule"
    CHANNEL = "channel"
    SYSTEM = "system"


@dataclass(frozen=True)
class Principal:
    """The acting user: whose credentials, quota and call records apply."""

    user_id: str
    org_ids: frozenset[str]
    is_admin: bool
    auth: AuthMethod
    # An API key's permission scopes; ``None`` means unrestricted.
    scopes: frozenset[str] | None = None


@dataclass(frozen=True)
class Binding:
    """The agent or workflow a resource is used through.

    A binding is a delegation: the resource is usable while the binding's
    owner can still see it, whoever is running the agent or workflow.
    """

    kind: Literal["agent", "workflow"]
    id: str
    owner_id: str
    org_id: str | None = None


Entry = Literal[
    "chat",
    "chat_noagent",
    "sub_agent",
    "workflow_node",
    "run_workflow",
    "schedule",
    "trigger",
    "channel",
]


@dataclass(frozen=True)
class CallContext:
    """One use of resources: who, through which binding, from which entry."""

    principal: Principal
    # ``None``: the principal uses the resource directly (chat with no agent).
    binding: Binding | None
    entry: Entry
    conversation_id: str | None = None
    agent_id: str | None = None
    workflow_run_id: str | None = None
    node_id: str | None = None

    @property
    def subject_id(self) -> str:
        """Whose view of a resource decides whether it is usable."""
        return self.binding.owner_id if self.binding is not None else self.principal.user_id


async def user_org_ids(db: AsyncSession, user_id: str) -> list[str]:
    """All organization ids *user_id* is a member of."""
    from sqlalchemy import select

    from fim_one.db.models.organization import OrgMembership

    result = await db.execute(select(OrgMembership.org_id).where(OrgMembership.user_id == user_id))
    return list(result.scalars().all())


async def resolve_principal(
    db: AsyncSession,
    *,
    user_id: str,
    auth: AuthMethod,
    scopes: frozenset[str] | None = None,
    user: Any | None = None,
) -> Principal:
    """Load the acting user and return their :class:`Principal`.

    Raises :class:`AssemblyRefusal` ``account_disabled`` (403) when the
    account does not exist or is switched off. Decoding a token, its type
    and its revocation stay in the web auth layer; this starts from a user
    id an entry point has already authenticated. *user* is that user's row
    when the entry point has already loaded it, which saves reading it again.
    """
    from sqlalchemy import select

    from fim_one.db.models.user import User

    if user is not None and getattr(user, "id", None) == user_id:
        state: tuple[Any, Any] | None = (user.is_active, user.is_admin)
    else:
        row = (
            await db.execute(select(User.is_active, User.is_admin).where(User.id == user_id))
        ).one_or_none()
        state = (row[0], row[1]) if row is not None else None
    if state is None or not state[0]:
        raise AssemblyRefusal(ACCOUNT_DISABLED, 403, "Account disabled")
    return Principal(
        user_id=user_id,
        org_ids=frozenset(await user_org_ids(db, user_id)),
        is_admin=bool(state[1]),
        auth=auth,
        scopes=scopes,
    )


async def resolve_external_actor(
    db: AsyncSession,
    *,
    provider: str,
    subject: str,
) -> Principal | None:
    """The local user an outside system's actor is linked to, if any.

    Looks the actor up in ``user_oauth_bindings``; ``None`` when no account
    is linked. A linked account that is missing or switched off raises
    ``account_disabled`` like any other principal.
    """
    from sqlalchemy import select

    from fim_one.db.models.oauth_binding import UserOAuthBinding

    linked_user_id = (
        await db.execute(
            select(UserOAuthBinding.user_id).where(
                UserOAuthBinding.provider == provider,
                UserOAuthBinding.oauth_id == subject,
            )
        )
    ).scalar_one_or_none()
    if linked_user_id is None:
        return None
    return await resolve_principal(db, user_id=linked_user_id, auth=AuthMethod.CHANNEL)


def provisional_principal(user_id: str, *, auth: AuthMethod = AuthMethod.SESSION) -> Principal:
    """A principal for a user id the entry point already authenticated.

    Carries the id only: no organizations, not an admin, account state not
    re-checked. The visibility rule reads nothing else, which is what lets
    the existing entry points adopt it without changing what they load.
    Entry points switch to :func:`resolve_principal` as they are folded into
    the assembly layer.
    """
    return Principal(user_id=user_id, org_ids=frozenset(), is_admin=False, auth=auth)
