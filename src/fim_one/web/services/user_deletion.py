"""Unified user-deletion service.

Both the admin "delete user" endpoint (:func:`fim_one.web.api.admin.delete_user`)
and the self-serve "delete my account" endpoint
(:func:`fim_one.web.api.auth.delete_own_account`) funnel through
:func:`purge_user_data` so that the two paths can never drift in *what* they
remove.  Historically they diverged: the admin path relied purely on ORM
cascade (and so leaked ``ApiKey`` / ``LoginHistory`` / eval / ``ConnectorCallLog``
/ ``ResourceSubscription`` / ``OrgMembership`` rows once FK enforcement is on),
while the self-serve path did the explicit row deletes but cleaned a different
set of files — and *both* computed their on-disk paths from a ``parents[3]``
root at different module depths, so each silently missed half the files it
meant to delete.

This module is the single source of truth.

Order: the rows go first, in the caller's transaction, together with the
caller's audit record; the files go after that commit.

FK-cascade notes
----------------
SQLite now runs with ``PRAGMA foreign_keys=ON`` (see ``db/engine.py``) so the
dev database mirrors Postgres.  Every ``User`` relationship is ``lazy="raise"``
+ ``cascade="all, delete-orphan"`` with **no** ``passive_deletes``, which means
SQLAlchemy *must* eagerly load each cascade collection before deleting the user
or the lazy-load raises.  :func:`purge_user_data` therefore deep-preloads the
full owned-resource tree.  Tables that have a FK to ``users`` but **no** ORM
relationship on ``User`` (api keys, eval data, subscriptions, org
memberships, workflow runs) are removed with explicit,
FK-safe-ordered ``DELETE`` statements *before* the ORM cascade runs.

Records that outlive the account
--------------------------------
The audit log, the sign-in history, review trails, call records and the
usage ledger are kept until their retention period ends
(``core/audit/retention.py``); none has a foreign key to ``users``. What
identifies the person is taken out of them here
(:func:`_deidentify_records`): the username and the email address become
``deleted-user-<first 8 characters of the id>``, and the address and device
of their own requests are cleared. The user id stays: with the account
gone it names nobody, and it keeps a deleted user's records apart from
each other's and from those of an account that never existed.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.core.assembly.confirmation import close_requests_of_runs
from fim_one.db.models.api_key import ApiKey
from fim_one.db.models.audit_log import AuditLog
from fim_one.db.models.connector import Connector
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.database_schema import DatabaseSchema
from fim_one.db.models.email_verification import EmailVerification
from fim_one.db.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.login_history import LoginHistory
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.review_log import ReviewLog
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowRun, WorkflowVersion
from fim_one.web.exceptions import AppError

logger = logging.getLogger(__name__)

# --- Canonical on-disk locations (must match the *write* side) --------------
# Conversation sandboxes are written to ``<repo>/data/sandbox/{conv_id}`` — see
# ``fim_one.web.api.chat._conversation_sandbox_root`` (the source of truth).
# This module lives at ``src/fim_one/web/services/`` so ``parents[3]`` is
# ``<repo>/src`` and ``.parent`` is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3].parent
_CONVERSATIONS_SANDBOX_DIR = _REPO_ROOT / "data" / "sandbox"


def _uploads_base() -> Path:
    """Uploads root, resolved exactly like every write path (cwd-relative)."""
    return Path(os.environ.get("UPLOADS_DIR", "uploads"))


def _vector_store_base() -> Path:
    return Path(os.environ.get("VECTOR_STORE_DIR", "./data/vector_store"))


DELETED_USER_PREFIX = "deleted-user-"


def deleted_user_label(user_id: str) -> str:
    """What a deleted user is called in the records that outlive the account."""
    return f"{DELETED_USER_PREFIX}{user_id[:8]}"


def is_reserved_username(name: str | None) -> bool:
    """Whether *name* reads as a deleted user's label; no account may take one."""
    return name is not None and name.strip().lower().startswith(DELETED_USER_PREFIX)


# Keys of an audit row's ``changes`` that hold a name of the account the
# row is about.
_IDENTITY_KEYS = frozenset({"username", "email", "display_name"})


def _patterns(
    quoted: list[str | None] | set[str], *, any_case: bool = False
) -> tuple[re.Pattern[str], ...]:
    """Each of *quoted* as a word of its own, longest first; short ones are left.

    *any_case* for email addresses, which people type in any case.
    """
    kept = sorted({name for name in quoted if name and len(name) >= 3}, key=len, reverse=True)
    flags = re.IGNORECASE if any_case else 0
    return tuple(
        re.compile(rf"(?<![\w@.-]){re.escape(name)}(?![\w@-])", flags) for name in kept
    )


async def _earlier_names(
    db: AsyncSession, user_id: str, is_target: Any
) -> tuple[set[str], set[str]]:
    """The addresses, and the other names, the account has had.

    Read before anything is relabelled: from the sign-in rows of the
    account (each holds the address and username it had then) and from the
    ``changes`` of the audit rows about it (an email change, a rename, an
    administrator's edit keep the old and the new value).
    """
    addresses: set[str] = set()
    names: set[str] = set()
    signed_in = await db.execute(
        select(LoginHistory.email, LoginHistory.username)
        .where(LoginHistory.user_id == user_id)
        .distinct()
    )
    for address, username in signed_in.all():
        if address:
            addresses.add(address)
        if username:
            names.add(username)
    changed = await db.execute(
        select(AuditLog.changes).where(is_target, AuditLog.changes.isnot(None))
    )
    for (changes,) in changed.all():
        if not isinstance(changes, dict):
            continue
        for key in _IDENTITY_KEYS:
            change = changes.get(key)
            if not isinstance(change, dict):
                continue
            for side in ("from", "to"):
                value = change.get(side)
                if isinstance(value, str) and value:
                    (addresses if key == "email" else names).add(value)
    return addresses, names


def _without_names(value: Any, names: tuple[re.Pattern[str], ...], label: str) -> Any:
    """*value* (text, or the lists and mappings of a JSON column) with every name replaced."""
    if isinstance(value, str):
        for name in names:
            value = name.sub(label, value)
        return value
    if isinstance(value, dict):
        return {key: _without_names(item, names, label) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_names(item, names, label) for item in value]
    return value


async def _deidentify_records(
    db: AsyncSession, user_id: str, *, username: str | None, email: str, display_name: str | None
) -> set[str]:
    """Take a user's identity out of the records that stay after the account.

    Returns every address the account has had.

    Rows are kept, with the user id. In ``login_history`` the username and
    email become the deleted-user label and the address and device are
    cleared. In ``audit_logs`` the same is done to the rows the user is the
    actor of; rows the user is the target of get the label as the target's
    name. In either kind the email address is replaced wherever it is
    quoted; the username and display name where they are a value under a
    name key of ``changes`` or the whole of ``detail``. In ``review_log`` the actor's name is replaced. Call records
    and the usage ledger hold an id and nothing else of the user.

    An address on a row somebody else is the actor of (an administrator who
    reset this user's password) is that person's, and stays.
    """
    label = deleted_user_label(user_id)
    is_actor = AuditLog.actor_id == user_id
    is_target = and_(AuditLog.target_type == "user", AuditLog.target_id == user_id)

    earlier_addresses, earlier_names = await _earlier_names(db, user_id, is_target)
    all_addresses = {email, *earlier_addresses} - {""}
    # Compared without case: the sign-in form takes the address as typed,
    # and ``ZELDA@EXAMPLE.TEST`` is recorded as an unknown account.
    lowered = sorted({address.lower() for address in all_addresses})

    await db.execute(
        update(LoginHistory)
        .where(LoginHistory.user_id == user_id)
        .values(username=label, email=label, ip_address=None, user_agent=None)
    )
    # A sign-in attempt with one of the account's addresses that was not
    # matched to it (before the account existed, typed in another case,
    # or after the address was changed): the address typed is the user's,
    # where it came from is not known to be.
    await db.execute(
        update(LoginHistory)
        .where(LoginHistory.user_id.is_(None), func.lower(LoginHistory.email).in_(lowered))
        .values(email=label)
    )
    await db.execute(
        update(AuditLog)
        .where(is_actor)
        .values(actor_label=label, ip_address=None, user_agent=None)
    )
    await db.execute(update(AuditLog).where(is_target).values(target_label=label))

    # The email address is replaced wherever it stands: nothing else looks
    # like it. A username or display name can be an ordinary word ("member",
    # "admin", "true") that is also a role or a flag in the same row, so it
    # is replaced only where a writer puts a name: under a name key of
    # ``changes``, or as the whole of ``detail``. The addresses are every
    # one the account has had, in any case.
    addresses = _patterns(all_addresses, any_case=True)
    # Addresses first: a username that is the start of an address does not
    # leave the rest of the address behind.
    names = addresses + _patterns([username, display_name, *earlier_names])
    whole = {name for name in (username, display_name, *earlier_names) if name}
    rows = await db.execute(
        select(AuditLog.id, AuditLog.detail, AuditLog.changes, is_target.label("about_user")).where(
            or_(is_actor, is_target),
            or_(AuditLog.detail.isnot(None), AuditLog.changes.isnot(None)),
        )
    )
    for row_id, detail, changes, about_user in rows.all():
        new_detail = label if detail in whole else _without_names(detail, addresses, label)
        new_changes = changes
        if isinstance(changes, dict):
            new_changes = {
                # The old and the new value of a name of this account.
                key: {"changed": True}
                if about_user and key in _IDENTITY_KEYS
                else _without_names(change, names if key in _IDENTITY_KEYS else addresses, label)
                for key, change in changes.items()
            }
        # Only what changed is written: a row without ``changes`` keeps its
        # SQL NULL (writing ``None`` to a JSON column stores the JSON null).
        values: dict[str, Any] = {}
        if new_detail != detail:
            values["detail"] = new_detail
        if new_changes != changes:
            values["changes"] = new_changes
        if values:
            await db.execute(update(AuditLog).where(AuditLog.id == row_id).values(**values))

    await db.execute(
        update(ReviewLog).where(ReviewLog.actor_id == user_id).values(actor_username=label)
    )
    return all_addresses


class PurgedUser(dict[str, int]):
    """What :func:`purge_user_data` staged: the summary, and the files to remove.

    The mapping itself is the summary ``{"conversations": int,
    "knowledge_bases": int}`` for the caller's audit detail line.
    """

    def __init__(
        self,
        summary: dict[str, int],
        *,
        user_id: str,
        conversation_ids: tuple[str, ...],
        knowledge_base_ids: tuple[str, ...],
    ) -> None:
        super().__init__(summary)
        self.user_id = user_id
        # What the caller's own record of the deletion calls the user.
        self.label = deleted_user_label(user_id)
        self.conversation_ids = conversation_ids
        self.knowledge_base_ids = knowledge_base_ids

    def remove_files(self) -> None:
        """Remove the deleted user's files. Call once the deletion is committed.

        Never raises: the account is already gone, and a path that could not
        be removed is logged for the operator.
        """
        try:
            uploads_base = _uploads_base()
            uploads_conversations = uploads_base / "conversations"
            dag_checkpoints_dir = _REPO_ROOT / "data" / "dag_checkpoints"
            workspaces_dir = _REPO_ROOT / "data" / "workspaces"
            for conv_id in self.conversation_ids:
                shutil.rmtree(_CONVERSATIONS_SANDBOX_DIR / conv_id, ignore_errors=True)
                shutil.rmtree(uploads_conversations / conv_id, ignore_errors=True)
                shutil.rmtree(workspaces_dir / conv_id, ignore_errors=True)
                (dag_checkpoints_dir / f"{conv_id}.json").unlink(missing_ok=True)

            kb_uploads = uploads_base / "kb"
            for kb_id in self.knowledge_base_ids:
                shutil.rmtree(kb_uploads / kb_id, ignore_errors=True)

            shutil.rmtree(_vector_store_base() / f"user_{self.user_id}", ignore_errors=True)
            shutil.rmtree(uploads_base / f"user_{self.user_id}", ignore_errors=True)

            avatar_dir = uploads_base / "avatars"
            if avatar_dir.exists():
                for pattern in (f"{self.user_id}_*", f"{self.user_id}.*"):
                    for avatar_file in avatar_dir.glob(pattern):
                        avatar_file.unlink(missing_ok=True)
        except Exception:
            logger.error(
                "Files of deleted user %s could not all be removed", self.user_id, exc_info=True
            )


async def purge_user_data(db: AsyncSession, user_id: str) -> PurgedUser:
    """Stage the removal of a user and *all* of their rows.

    Does **not** commit and does **not** write an audit record; the caller owns
    transaction boundaries and audit semantics (the actor/action differ between
    the admin and self-serve paths). It does **not** touch the disk either:
    the caller commits, then calls :meth:`PurgedUser.remove_files`. In that
    order a failed commit leaves the account whole, and a crash after the
    commit leaves only files nothing refers to.

    Returns:
        The summary ``{"conversations": int, "knowledge_bases": int}`` for
        the caller's audit detail line, with the files to remove.

    Raises:
        AppError(``user_not_found``): no such user.
        AppError(``cannot_delete_org_owner``): the user owns one or more
            organizations.  Ownership must be transferred (or the org deleted)
            first — consistent with the "an owner must transfer before leaving"
            rule already enforced on org membership.
    """
    # 1. Load the user with the FULL owned-resource tree preloaded so the
    #    lazy="raise" cascade relationships can be deleted without raising.
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.conversations).selectinload(Conversation.messages),
            selectinload(User.agents),
            selectinload(User.knowledge_bases).selectinload(KnowledgeBase.documents),
            selectinload(User.model_configs),
            selectinload(User.connectors).selectinload(Connector.actions),
            selectinload(User.connectors)
            .selectinload(Connector.database_schemas)
            .selectinload(DatabaseSchema.columns),
            selectinload(User.mcp_servers),
            selectinload(User.oauth_bindings),
            selectinload(User.workflows),
            selectinload(User.skills),
            selectinload(User.notification_preferences),
            selectinload(User.subscription),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError("user_not_found", status_code=404)

    user_email = user.email

    # 2. Guard: refuse to delete an organization owner (would otherwise be a
    #    hard FK violation on organizations.owner_id, or — far worse — a silent
    #    cascade that wipes other members' org).
    owned = (
        await db.execute(
            select(Organization.name).where(Organization.owner_id == user_id)
        )
    ).scalars().all()
    if owned:
        raise AppError(
            "cannot_delete_org_owner",
            status_code=409,
            detail=(
                f"User owns {len(owned)} organization(s): {', '.join(owned)}. "
                "Transfer ownership or delete the organization first."
            ),
            detail_args={"count": len(owned), "orgs": ", ".join(owned)},
        )

    # 3. Note the file-system resources to remove. They are removed by the
    #    caller AFTER the deletion is committed (:meth:`PurgedUser.remove_files`):
    #    a file left behind by a crash in between can still be cleaned up, a
    #    user whose deletion rolled back cannot get their files back.
    #    NOTE: any new user-owned module that writes to disk MUST be added to
    #    :meth:`PurgedUser.remove_files` AND to the registry in
    #    .claude/skills/user-owned-module/SKILL.md and
    #    .agents/skills/user-owned-module/SKILL.md.
    conv_ids = (
        await db.execute(
            select(Conversation.id).where(Conversation.user_id == user_id)
        )
    ).scalars().all()
    kb_ids = (
        await db.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
        )
    ).scalars().all()

    # 4. Explicit, FK-safe deletes for tables that have a FK to users but no
    #    cascade relationship on the User model (so the ORM cascade in step 5
    #    won't touch them).  Order matters where these tables FK each other.

    # Eval chain: results -> runs -> cases -> datasets.  (EvalRun also FKs
    # agents/datasets with no ondelete, so it MUST be cleared before the ORM
    # cascade deletes the user's agents/datasets in step 5.)
    user_run_ids = select(EvalRun.id).where(EvalRun.user_id == user_id)
    await db.execute(
        delete(EvalCaseResult).where(EvalCaseResult.run_id.in_(user_run_ids))
    )
    await db.execute(delete(EvalRun).where(EvalRun.user_id == user_id))
    await db.execute(delete(EvalCase).where(EvalCase.user_id == user_id))
    await db.execute(delete(EvalDataset).where(EvalDataset.user_id == user_id))

    await db.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
    await db.execute(
        delete(ResourceSubscription).where(ResourceSubscription.user_id == user_id)
    )

    # Workflow runs this user triggered (incl. on *shared* workflows owned by
    # others — those won't be caught by the owned-workflow cascade).  An
    # approval names its run without a foreign key, so the open ones are
    # closed here, for these runs and for the runs of the user's own
    # workflows (which go with the workflow in step 5).
    await close_requests_of_runs(
        db,
        select(WorkflowRun.id).where(
            or_(
                WorkflowRun.user_id == user_id,
                WorkflowRun.workflow_id.in_(select(Workflow.id).where(Workflow.user_id == user_id)),
            )
        ),
    )
    await db.execute(delete(WorkflowRun).where(WorkflowRun.user_id == user_id))
    # Versions this user authored on *others'* workflows: keep the row, drop the
    # actor reference (versions on the user's own workflows cascade-delete).
    await db.execute(
        update(WorkflowVersion)
        .where(WorkflowVersion.created_by == user_id)
        .values(created_by=None)
    )

    # Org memberships: drop this user's memberships; null out the "invited_by"
    # back-reference on memberships they created for others.
    await db.execute(delete(OrgMembership).where(OrgMembership.user_id == user_id))
    await db.execute(
        update(OrgMembership)
        .where(OrgMembership.invited_by == user_id)
        .values(invited_by=None)
    )

    # The records that outlive the account (audit log, sign-in history,
    # review trails, call records, usage ledger) keep their rows and the
    # user id, and lose what identifies the person.
    addresses = await _deidentify_records(
        db, user_id, username=user.username, email=user_email, display_name=user.display_name
    )
    # Codes sent to any address of the account, in whatever case it was typed.
    await db.execute(
        delete(EmailVerification).where(
            func.lower(EmailVerification.email).in_(sorted(a.lower() for a in addresses))
        )
    )

    # 5. Delete the user — ORM cascade removes the owned-resource tree
    #    (conversations+messages, agents, KBs+documents, connectors+actions+
    #    schemas+columns, model configs, mcp servers, oauth bindings, workflows
    #    [+runs/versions via DB cascade], skills, notification prefs,
    #    subscription) and DB FK cascade removes credential rows.
    await db.delete(user)

    logger.info(
        "Purged user %s: %d conversations, %d knowledge bases, files & rows",
        user_id,
        len(conv_ids),
        len(kb_ids),
    )
    return PurgedUser(
        {"conversations": len(conv_ids), "knowledge_bases": len(kb_ids)},
        user_id=user_id,
        conversation_ids=tuple(conv_ids),
        knowledge_base_ids=tuple(kb_ids),
    )
