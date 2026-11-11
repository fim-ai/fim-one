"""Workflow trigger keys: generation, hashing and lookup.

A trigger key lets an external system start one workflow without a user
session. Only its SHA-256 hash and a short display prefix are stored, the
same scheme as user API keys (``ApiKey.key_hash``): the plain key is shown
once, when generated.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db.models import User, Workflow

TRIGGER_KEY_PREFIX = "wf_"
# Characters of the key kept for display ("wf_" plus 7 of the secret part).
DISPLAY_PREFIX_LEN = 10


def hash_trigger_key(raw_key: str) -> str:
    """Hex SHA-256 of *raw_key*, the value stored in ``api_key_hash``."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_trigger_key() -> tuple[str, str, str]:
    """Return ``(raw_key, key_hash, display_prefix)`` for a new trigger key."""
    raw_key = f"{TRIGGER_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return raw_key, hash_trigger_key(raw_key), raw_key[:DISPLAY_PREFIX_LEN]


def parse_bearer(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class TriggerKeyRejectedError(Exception):
    """The presented key does not authorize a run.

    ``error_code`` is ``invalid_api_key`` for an unknown key and
    ``workflow_owner_inactive`` when the workflow's author is disabled. Both
    map to 401. ``workflow`` is the workflow a valid key belongs to.
    """

    def __init__(self, error_code: str, detail: str, workflow: Workflow | None = None) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail
        self.workflow = workflow


async def resolve_trigger_key(raw_key: str | None, db: AsyncSession) -> Workflow:
    """Return the workflow *raw_key* triggers, or raise :class:`TriggerKeyRejectedError`.

    The lookup is by hash, so the comparison never touches the secret
    itself; ``hmac.compare_digest`` re-checks the stored hash in constant
    time. A key whose author account is disabled is rejected: a disabled
    user's keys stop working, as their API keys do.
    """
    if not raw_key:
        raise TriggerKeyRejectedError("invalid_api_key", "Invalid API key")
    key_hash = hash_trigger_key(raw_key)
    wf = (
        await db.execute(select(Workflow).where(Workflow.api_key_hash == key_hash))
    ).scalar_one_or_none()
    if wf is None or not hmac.compare_digest(wf.api_key_hash or "", key_hash):
        raise TriggerKeyRejectedError("invalid_api_key", "Invalid API key")

    owner_active = (
        await db.execute(select(User.is_active).where(User.id == wf.user_id))
    ).scalar_one_or_none()
    if not owner_active:
        raise TriggerKeyRejectedError(
            "workflow_owner_inactive", "The workflow owner's account is disabled", wf
        )
    return wf
