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

FK-cascade notes
----------------
SQLite now runs with ``PRAGMA foreign_keys=ON`` (see ``db/engine.py``) so the
dev database mirrors Postgres.  Every ``User`` relationship is ``lazy="raise"``
+ ``cascade="all, delete-orphan"`` with **no** ``passive_deletes``, which means
SQLAlchemy *must* eagerly load each cascade collection before deleting the user
or the lazy-load raises.  :func:`purge_user_data` therefore deep-preloads the
full owned-resource tree.  Tables that have a FK to ``users`` but **no** ORM
relationship on ``User`` (api keys, login history, eval data, call logs,
subscriptions, org memberships, workflow runs) are removed with explicit,
FK-safe-ordered ``DELETE`` statements *before* the ORM cascade runs.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db.models.api_key import ApiKey
from fim_one.db.models.audit_log import AuditLog
from fim_one.db.models.connector import Connector
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.database_schema import DatabaseSchema
from fim_one.db.models.email_verification import EmailVerification
from fim_one.db.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from fim_one.db.models.knowledge_base import KnowledgeBase
from fim_one.db.models.login_history import LoginHistory
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.user import User
from fim_one.db.models.workflow import WorkflowRun, WorkflowVersion
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


async def purge_user_data(db: AsyncSession, user_id: str) -> dict[str, int]:
    """Remove a user and *all* of their data — DB rows and on-disk files.

    Does **not** commit and does **not** write an audit record; the caller owns
    transaction boundaries and audit semantics (the actor/action differ between
    the admin and self-serve paths).

    Returns:
        A small summary ``{"conversations": int, "knowledge_bases": int}`` for
        the caller's audit detail line.

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

    # 3. Clean up file-system resources before the DB delete.
    #    NOTE: any new user-owned module that writes to disk MUST be added here
    #    AND to the "User Deletion File Cleanup" registry in CLAUDE.md.
    conv_ids = (
        await db.execute(
            select(Conversation.id).where(Conversation.user_id == user_id)
        )
    ).scalars().all()
    uploads_base = _uploads_base()
    uploads_conversations = uploads_base / "conversations"
    dag_checkpoints_dir = _REPO_ROOT / "data" / "dag_checkpoints"
    for conv_id in conv_ids:
        shutil.rmtree(_CONVERSATIONS_SANDBOX_DIR / conv_id, ignore_errors=True)
        shutil.rmtree(uploads_conversations / conv_id, ignore_errors=True)
        (dag_checkpoints_dir / f"{conv_id}.json").unlink(missing_ok=True)

    kb_ids = (
        await db.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id)
        )
    ).scalars().all()
    kb_uploads = uploads_base / "kb"
    for kb_id in kb_ids:
        shutil.rmtree(kb_uploads / kb_id, ignore_errors=True)

    shutil.rmtree(_vector_store_base() / f"user_{user_id}", ignore_errors=True)
    shutil.rmtree(uploads_base / f"user_{user_id}", ignore_errors=True)

    avatar_dir = uploads_base / "avatars"
    if avatar_dir.exists():
        for pattern in (f"{user_id}_*", f"{user_id}.*"):
            for avatar_file in avatar_dir.glob(pattern):
                avatar_file.unlink(missing_ok=True)

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
    await db.execute(delete(LoginHistory).where(LoginHistory.user_id == user_id))
    await db.execute(delete(ConnectorCallLog).where(ConnectorCallLog.user_id == user_id))
    await db.execute(
        delete(ResourceSubscription).where(ResourceSubscription.user_id == user_id)
    )
    await db.execute(delete(EmailVerification).where(EmailVerification.email == user_email))

    # Workflow runs this user triggered (incl. on *shared* workflows owned by
    # others — those won't be caught by the owned-workflow cascade).  Approvals
    # cascade off the run at the DB level (ondelete=CASCADE).
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

    # Preserve audit history but remove the actor reference.
    await db.execute(
        update(AuditLog).where(AuditLog.admin_id == user_id).values(admin_id=None)
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
    return {"conversations": len(conv_ids), "knowledge_bases": len(kb_ids)}
