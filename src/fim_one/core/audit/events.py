"""What an audit record says.

One shape for every security-relevant event: who did what to which thing,
in whose organization, from where, and how it ended. The writer
(:mod:`.writer`) is the only code that turns one into a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["Actor", "ActorKind", "AuditEvent", "EventContext", "Outcome", "RequestMeta"]

# ``denied``: refused by a rule (a blocked address, an unknown key);
# ``failed``: attempted and did not go through.
Outcome = Literal["success", "denied", "failed"]

# ``admin`` is a platform administrator, ``user`` anyone else signed in;
# ``system`` is the platform acting on its own (a sweep, a billing event);
# ``anonymous`` is a caller nobody was established for; ``external`` is a
# caller known only to an outside system or by a key: a channel member with
# no account, the holder of a workflow trigger key (with ``user_id`` the
# account the key acts for).
ActorKind = Literal["user", "admin", "system", "anonymous", "external"]


@dataclass(frozen=True)
class Actor:
    """Who acted, as it was known at the time."""

    # ``None`` for a failed anonymous attempt and for the system.
    user_id: str | None
    # The username or email then; a snapshot, kept when the account is gone.
    label: str | None
    kind: ActorKind
    # How the identity was established: a ``core.assembly.principal.AuthMethod`` value.
    auth: str | None = None
    # The API key's id, or a trigger key's prefix. Never the key.
    credential_ref: str | None = None


@dataclass(frozen=True)
class RequestMeta:
    """Where a request came from."""

    ip: str | None
    user_agent: str | None
    request_id: str | None


@dataclass(frozen=True)
class EventContext:
    """What an event happened in, for a caller that holds no ``CallContext``.

    The writer reads the same three ids from either.
    """

    conversation_id: str | None = None
    workflow_run_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """One audit record."""

    # A name registered in :mod:`.actions`, ``domain.verb``.
    action: str
    actor: Actor
    # Whose organization the thing acted on belongs to; decides which
    # organization's administrators may be shown the record.
    org_id: str | None
    target_type: str | None
    target_id: str | None
    target_label: str | None
    outcome: Outcome
    # ``{"role": {"from": "member", "to": "admin"}}``; a secret field says
    # only ``{"changed": True}``.
    changes: dict[str, dict[str, Any]] | None
    detail: str | None
    request: RequestMeta | None
    conversation_id: str | None = None
    workflow_run_id: str | None = None
    agent_id: str | None = None
