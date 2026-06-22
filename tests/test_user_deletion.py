"""Tests for the unified user-deletion service.

Exercises :func:`fim_one.web.services.user_deletion.purge_user_data` against an
in-memory SQLite database with **foreign-key enforcement enabled** so that the
``lazy="raise"`` ORM cascade and the explicit FK-safe ``DELETE`` statements are
verified together exactly as they run against Postgres.

The headline test (:func:`test_purge_fully_loaded_user_removes_everything`)
builds ONE user owning one of every user-linked resource type and asserts the
whole tree disappears in a single purge — proving the preload list keeps the
lazy-raise cascade from blowing up under FK enforcement.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.api_key import ApiKey
from fim_one.db.models.audit_log import AuditLog
from fim_one.db.models.billing import BillingPlan, Subscription
from fim_one.db.models.connector import Connector, ConnectorAction
from fim_one.db.models.connector_call_log import ConnectorCallLog
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.database_schema import DatabaseSchema, SchemaColumn
from fim_one.db.models.email_verification import EmailVerification
from fim_one.db.models.eval import (
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalRun,
)
from fim_one.db.models.knowledge_base import KBDocument, KnowledgeBase
from fim_one.db.models.login_history import LoginHistory
from fim_one.db.models.mcp_server import MCPServer
from fim_one.db.models.message import Message
from fim_one.db.models.model_config import ModelConfig
from fim_one.db.models.notification_preference import NotificationPreference
from fim_one.db.models.oauth_binding import UserOAuthBinding
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.resource_subscription import ResourceSubscription
from fim_one.db.models.skill import Skill
from fim_one.db.models.user import User
from fim_one.db.models.workflow import Workflow, WorkflowVersion
from fim_one.web.exceptions import AppError
from fim_one.web.services.user_deletion import purge_user_data


# ---------------------------------------------------------------------------
# Fixtures — in-memory SQLite with FK enforcement
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cred_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Stable encryption key for models with encrypted columns (ModelConfig,
    MCPServer, User.totp_secret)."""
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-deletion-key-1234567890")
    enc._CREDENTIAL_KEY_RAW = "test-deletion-key-1234567890"
    enc._cred_fernet_instance = None
    yield
    enc._cred_fernet_instance = None


@pytest.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async session with PRAGMA foreign_keys=ON."""
    import fim_one.db.models  # noqa: F401 — register all models on Base.metadata

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: object, _: object) -> None:
        cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


async def _count(db: AsyncSession, model: type, **filters: object) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in filters.items():
        stmt = stmt.where(getattr(model, col) == val)
    return (await db.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Test 1 — full-tree purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_fully_loaded_user_removes_everything(db: AsyncSession) -> None:
    """A user owning one of every linked resource is fully purged in one call."""
    user_id = _uid()
    user_email = f"{user_id}@example.com"
    user = User(id=user_id, username=f"u_{user_id}", email=user_email, is_admin=False)

    # --- A DIFFERENT user that owns an org our user is merely a member of ---
    other_id = _uid()
    other = User(id=other_id, username=f"o_{other_id}", email=f"{other_id}@x.com")
    org_id = _uid()
    org = Organization(id=org_id, name="Other Org", slug=f"org-{org_id}", owner_id=other_id)

    db.add_all([user, other, org])
    await db.flush()

    # --- Conversation (+ Message) ---
    conv = Conversation(id=_uid(), user_id=user_id, title="c", mode="chat")
    db.add(conv)
    await db.flush()
    msg = Message(id=_uid(), conversation_id=conv.id, role="user", content="hi")

    # --- Agent (needed by EvalRun FK) ---
    agent = Agent(id=_uid(), user_id=user_id, name="A")

    # --- KnowledgeBase (+ KBDocument) ---
    kb = KnowledgeBase(id=_uid(), user_id=user_id, name="KB")
    db.add(kb)
    await db.flush()
    kbdoc = KBDocument(
        id=_uid(), kb_id=kb.id, filename="f.txt", file_path="/tmp/f.txt", file_type="txt"
    )

    # --- Connector (+ Action + DatabaseSchema + SchemaColumn) ---
    connector = Connector(id=_uid(), user_id=user_id, name="Conn")
    db.add(connector)
    await db.flush()
    action = ConnectorAction(id=_uid(), connector_id=connector.id, name="act", path="/p")
    schema = DatabaseSchema(id=_uid(), connector_id=connector.id, table_name="t")
    db.add(schema)
    await db.flush()
    col = SchemaColumn(
        id=_uid(), schema_id=schema.id, column_name="c", data_type="text"
    )

    # --- ModelConfig ---
    mc = ModelConfig(
        id=_uid(), user_id=user_id, name="m", provider="openai", model_name="gpt"
    )

    # --- MCPServer ---
    mcp = MCPServer(id=_uid(), user_id=user_id, name="mcp")

    # --- UserOAuthBinding ---
    oauth = UserOAuthBinding(
        id=_uid(), user_id=user_id, provider="google", oauth_id=_uid()
    )

    # --- Workflow ---
    wf = Workflow(id=_uid(), user_id=user_id, name="wf", blueprint={"nodes": [], "edges": []})

    # --- Skill ---
    skill = Skill(id=_uid(), user_id=user_id, name="s", content="do x")

    # --- NotificationPreference ---
    notif = NotificationPreference(
        id=_uid(), user_id=user_id, event_type="run_done", channel="email"
    )

    # --- Subscription (needs a BillingPlan) ---
    plan = BillingPlan(slug=f"pro-{_uid()}", name="Pro", monthly_token_quota=1000)
    db.add(plan)
    await db.flush()
    now = _now()
    sub = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        stripe_subscription_id=_uid(),
        stripe_price_id="price_x",
        status="active",
        current_period_start=now,
        current_period_end=now,
        updated_at=now,
    )

    # --- ApiKey ---
    apikey = ApiKey(
        id=_uid(), user_id=user_id, name="k", key_prefix="ab", key_hash=_uid()
    )

    # --- LoginHistory ---
    login = LoginHistory(id=_uid(), user_id=user_id, success=True)

    # --- Eval chain: Dataset + Case + Run + CaseResult ---
    ds = EvalDataset(id=_uid(), user_id=user_id, name="ds")
    db.add(ds)
    await db.flush()
    case = EvalCase(
        id=_uid(),
        dataset_id=ds.id,
        user_id=user_id,
        prompt="p",
        expected_behavior="b",
    )
    db.add_all([agent, case])
    await db.flush()
    run = EvalRun(id=_uid(), user_id=user_id, agent_id=agent.id, dataset_id=ds.id)
    db.add(run)
    await db.flush()
    caseresult = EvalCaseResult(id=_uid(), run_id=run.id, case_id=case.id, status="pass")

    # --- ConnectorCallLog ---
    calllog = ConnectorCallLog(
        id=_uid(),
        connector_id=connector.id,
        connector_name="Conn",
        action_name="act",
        user_id=user_id,
        request_method="GET",
        request_url="https://x",
        success=True,
    )

    # --- ResourceSubscription ---
    rsub = ResourceSubscription(
        id=_uid(),
        user_id=user_id,
        resource_type="connector",
        resource_id=_uid(),
        org_id=org_id,
    )

    # --- EmailVerification (matched by email) ---
    ev = EmailVerification(
        id=_uid(), email=user_email, code="123456", expires_at=_now()
    )

    # --- OrgMembership: user is a MEMBER of the OTHER user's org ---
    membership = OrgMembership(id=_uid(), org_id=org_id, user_id=user_id, role="member")

    # --- AuditLog with admin_id == this user ---
    audit = AuditLog(
        id=_uid(), admin_id=user_id, admin_username="u", action="something"
    )

    db.add_all(
        [
            msg, kbdoc, action, col, mc, mcp, oauth, wf, skill, notif, sub,
            apikey, login, caseresult, calllog, rsub, ev, membership, audit,
        ]
    )
    await db.commit()

    # --- Act ---
    summary = await purge_user_data(db, user_id)
    await db.commit()

    assert summary == {"conversations": 1, "knowledge_bases": 1}

    # --- Assert the user and a representative row of every table are gone ---
    assert await _count(db, User, id=user_id) == 0
    assert await _count(db, Conversation, id=conv.id) == 0
    assert await _count(db, Message, id=msg.id) == 0
    assert await _count(db, Agent, id=agent.id) == 0
    assert await _count(db, KnowledgeBase, id=kb.id) == 0
    assert await _count(db, KBDocument, id=kbdoc.id) == 0
    assert await _count(db, Connector, id=connector.id) == 0
    assert await _count(db, ConnectorAction, id=action.id) == 0
    assert await _count(db, DatabaseSchema, id=schema.id) == 0
    assert await _count(db, SchemaColumn, id=col.id) == 0
    assert await _count(db, ModelConfig, id=mc.id) == 0
    assert await _count(db, MCPServer, id=mcp.id) == 0
    assert await _count(db, UserOAuthBinding, id=oauth.id) == 0
    assert await _count(db, Workflow, id=wf.id) == 0
    assert await _count(db, Skill, id=skill.id) == 0
    assert await _count(db, NotificationPreference, id=notif.id) == 0
    assert await _count(db, Subscription, id=sub.id) == 0
    assert await _count(db, ApiKey, id=apikey.id) == 0
    assert await _count(db, LoginHistory, id=login.id) == 0
    assert await _count(db, EvalDataset, id=ds.id) == 0
    assert await _count(db, EvalCase, id=case.id) == 0
    assert await _count(db, EvalRun, id=run.id) == 0
    assert await _count(db, EvalCaseResult, id=caseresult.id) == 0
    assert await _count(db, ConnectorCallLog, id=calllog.id) == 0
    assert await _count(db, ResourceSubscription, id=rsub.id) == 0
    assert await _count(db, EmailVerification, id=ev.id) == 0
    assert await _count(db, OrgMembership, id=membership.id) == 0

    # --- Survivors ---
    # The other user's organization (and the other user) still exist.
    assert await _count(db, Organization, id=org_id) == 1
    assert await _count(db, User, id=other_id) == 1
    # The audit log row survives but its actor reference is nulled.
    assert await _count(db, AuditLog, id=audit.id) == 1
    surviving_audit = (
        await db.execute(select(AuditLog).where(AuditLog.id == audit.id))
    ).scalar_one()
    assert surviving_audit.admin_id is None


# ---------------------------------------------------------------------------
# Test 2 — refuse to delete an org owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_blocks_org_owner(db: AsyncSession) -> None:
    """Purging a user who owns an organization is rejected with 409."""
    user_id = _uid()
    user = User(id=user_id, username=f"u_{user_id}", email=f"{user_id}@x.com")
    org_id = _uid()
    org = Organization(id=org_id, name="My Org", slug=f"org-{org_id}", owner_id=user_id)
    db.add_all([user, org])
    await db.flush()
    membership = OrgMembership(id=_uid(), org_id=org_id, user_id=user_id, role="owner")
    db.add(membership)
    await db.commit()

    with pytest.raises(AppError) as exc_info:
        await purge_user_data(db, user_id)

    assert exc_info.value.error_code == "cannot_delete_org_owner"
    assert exc_info.value.status_code == 409

    await db.rollback()
    # Nothing deleted.
    assert await _count(db, User, id=user_id) == 1
    assert await _count(db, Organization, id=org_id) == 1


# ---------------------------------------------------------------------------
# Test 3 — null out actor back-references on other users' resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_nulls_invited_by_and_version_author(db: AsyncSession) -> None:
    """invited_by / WorkflowVersion.created_by referencing the purged user are
    nulled — the rows belong to other users and must survive."""
    u_id = _uid()
    u = User(id=u_id, username=f"u_{u_id}", email=f"{u_id}@x.com")

    other_id = _uid()
    other = User(id=other_id, username=f"o_{other_id}", email=f"{other_id}@x.com")
    org_id = _uid()
    org = Organization(id=org_id, name="Org", slug=f"org-{org_id}", owner_id=other_id)
    db.add_all([u, other, org])
    await db.flush()

    # OrgMembership of `other`, invited by U.
    membership = OrgMembership(
        id=_uid(), org_id=org_id, user_id=other_id, role="member", invited_by=u_id
    )

    # WorkflowVersion on another user's workflow, authored by U.
    wf = Workflow(
        id=_uid(), user_id=other_id, name="wf", blueprint={"nodes": [], "edges": []}
    )
    db.add(wf)
    await db.flush()
    version = WorkflowVersion(
        id=_uid(),
        workflow_id=wf.id,
        version_number=1,
        blueprint={"nodes": [], "edges": []},
        created_by=u_id,
    )
    db.add_all([membership, version])
    await db.commit()

    await purge_user_data(db, u_id)
    await db.commit()

    assert await _count(db, User, id=u_id) == 0

    surviving_membership = (
        await db.execute(
            select(OrgMembership).where(OrgMembership.id == membership.id)
        )
    ).scalar_one()
    assert surviving_membership.invited_by is None

    surviving_version = (
        await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.id == version.id)
        )
    ).scalar_one()
    assert surviving_version.created_by is None


# ---------------------------------------------------------------------------
# Test 4 — missing user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_missing_user_raises(db: AsyncSession) -> None:
    """Purging a non-existent user raises a 404 user_not_found AppError."""
    with pytest.raises(AppError) as exc_info:
        await purge_user_data(db, "nonexistent")

    assert exc_info.value.error_code == "user_not_found"
    assert exc_info.value.status_code == 404
