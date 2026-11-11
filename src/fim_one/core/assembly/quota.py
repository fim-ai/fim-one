"""Whether the acting user's plan and token quota allow a model call.

The order of the checks and the refusal codes are decided here; the numbers
come from a backend the web layer registers at start-up (the billing tables
and the ``access_model`` setting live there). With no backend registered a
check raises: nothing is let through by default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from .errors import (
    ACCOUNT_DISABLED,
    QUOTA_UNAVAILABLE,
    SUBSCRIPTION_REQUIRED,
    TOKEN_QUOTA_EXCEEDED,
    WORKFLOW_OWNER_INACTIVE,
    AssemblyRefusal,
)

if TYPE_CHECKING:
    from .principal import Binding, Principal

logger = logging.getLogger(__name__)

__all__ = [
    "AssemblyRunGate",
    "QuotaBackend",
    "QuotaStatus",
    "check_quota",
    "get_quota_backend",
    "quota_reset_at",
    "quota_status",
    "set_quota_backend",
]


class QuotaBackend(Protocol):
    """Where the numbers come from. Registered by the web layer."""

    async def usage_and_cap(self, user_id: str) -> tuple[int, int]:
        """Tokens used in the current window and the cap; a cap of 0 is no cap."""
        ...

    async def unentitled(self, user_id: str) -> bool:
        """The instance sells access by plan and this user has none."""
        ...

    async def reset_at(self, user_id: str) -> datetime:
        """When the user's usage window next resets."""
        ...


_backend: QuotaBackend | None = None


def set_quota_backend(backend: QuotaBackend | None) -> None:
    """Register the quota backend (``None`` unregisters it)."""
    global _backend
    _backend = backend


def get_quota_backend() -> QuotaBackend:
    """The registered backend; raises when the web layer has not registered one."""
    if _backend is None:
        raise RuntimeError(
            "No quota backend is registered. The web layer registers one at start-up "
            "(fim_one.web.services.assembly_ports.register_assembly_ports)."
        )
    return _backend


@dataclass(frozen=True)
class QuotaStatus:
    """A user's usage against their cap in the current window."""

    used: int
    cap: int
    # Only looked up when asked for: the reset time costs a query.
    reset_at: datetime | None = None

    @property
    def unlimited(self) -> bool:
        return self.cap <= 0

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.used >= self.cap

    def exhausted_after(self, consumed: int) -> bool:
        """Whether the cap is reached once *consumed* is added to what was used
        at the check: the mid-run test of a long piece of work (a chat
        turn's answer, an evaluation's cases, a batch's items), made from
        its own meter without asking the database again."""
        return not self.unlimited and self.used + consumed >= self.cap


async def quota_status(user_id: str, *, with_reset: bool = False) -> QuotaStatus:
    """Usage and cap of *user_id*; the pre-run check and the mid-stream guard share it."""
    backend = get_quota_backend()
    used, cap = await backend.usage_and_cap(user_id)
    return QuotaStatus(
        used=int(used),
        cap=int(cap),
        reset_at=await backend.reset_at(user_id) if with_reset else None,
    )


async def quota_reset_at(user_id: str) -> datetime:
    """When *user_id*'s usage window next resets."""
    return await get_quota_backend().reset_at(user_id)


async def check_quota(principal: Principal) -> QuotaStatus:
    """Refuse a model call the principal's plan or quota does not cover.

    In order: ``subscription_required`` (402) when access is sold by plan
    and the user has none, then ``token_quota_exceeded`` (429). When the
    plan or the usage cannot be read the call is refused as
    ``quota_unavailable`` (503): an unknown is not a pass.

    Returns the usage and cap the call was let through on, so that a long
    turn can keep watching the cap without reading them again.
    """
    backend = get_quota_backend()
    try:
        status = await quota_status(principal.user_id)
        unentitled = await backend.unentitled(principal.user_id)
    except AssemblyRefusal:
        raise
    except Exception:
        logger.warning(
            "Plan or quota lookup failed for user %s; refusing", principal.user_id, exc_info=True
        )
        raise AssemblyRefusal(
            QUOTA_UNAVAILABLE,
            503,
            "Your plan and token quota could not be checked. Try again shortly.",
        ) from None
    if unentitled:
        raise AssemblyRefusal(SUBSCRIPTION_REQUIRED, 402)
    if status.exhausted:
        raise AssemblyRefusal(TOKEN_QUOTA_EXCEEDED, 429)
    return status


class AssemblyRunGate:
    """Whether a run through a binding may start.

    The workflow run service's gate (``core.workflow.run_service.RunGate``):
    the binding owner's account, the acting account, then plan and quota.
    """

    async def check(self, principal: Principal, binding: Binding | None = None) -> None:
        from sqlalchemy import select

        from fim_one.db import create_session
        from fim_one.db.models import User

        accounts = {principal.user_id}
        if binding is not None:
            accounts.add(binding.owner_id)
        async with create_session() as db:
            rows = await db.execute(
                select(User.id, User.is_active).where(User.id.in_(accounts))
            )
            active = {uid: bool(is_active) for uid, is_active in rows.all()}
        # A disabled author's workflows stop running for everyone. With no
        # binding there is no owner to speak of: only the acting account.
        if binding is not None and not active.get(binding.owner_id, False):
            raise AssemblyRefusal(
                WORKFLOW_OWNER_INACTIVE, 403, "The workflow owner's account is disabled"
            )
        if not active.get(principal.user_id, False):
            raise AssemblyRefusal(ACCOUNT_DISABLED, 403, "Account disabled")
        await check_quota(principal)
