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

import json
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
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


def _backends() -> list[Any]:
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param(
            "postgresql",
            id="postgresql",
            marks=pytest.mark.skipif(
                not os.environ.get("FIM_TEST_PG_URL"), reason="FIM_TEST_PG_URL not set"
            ),
        ),
    ]


@pytest.fixture(params=_backends())
async def db(request: pytest.FixtureRequest) -> AsyncGenerator[AsyncSession, None]:
    """A session on an empty database: in-memory SQLite with foreign keys
    enforced, and PostgreSQL as well when ``FIM_TEST_PG_URL`` is set."""
    import fim_one.db.models  # noqa: F401 — register all models on Base.metadata

    if request.param == "postgresql":
        url = make_url(os.environ["FIM_TEST_PG_URL"]).set(drivername="postgresql+asyncpg")
        schema = f"user_deletion_{uuid.uuid4().hex[:12]}"
        admin = create_async_engine(url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
        return

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
    col = SchemaColumn(id=_uid(), schema_id=schema.id, column_name="c", data_type="text")

    # --- ModelConfig ---
    mc = ModelConfig(id=_uid(), user_id=user_id, name="m", provider="openai", model_name="gpt")

    # --- MCPServer ---
    mcp = MCPServer(id=_uid(), user_id=user_id, name="mcp")

    # --- UserOAuthBinding ---
    oauth = UserOAuthBinding(id=_uid(), user_id=user_id, provider="google", oauth_id=_uid())

    # --- Workflow ---
    wf = Workflow(id=_uid(), user_id=user_id, name="wf", blueprint={"nodes": [], "edges": []})

    # --- Skill ---
    skill = Skill(id=_uid(), user_id=user_id, name="s", content="do x")

    # --- NotificationPreference ---
    notif = NotificationPreference(
        id=_uid(), user_id=user_id, event_type="run_done", channel="email"
    )

    # --- Subscription (needs a BillingPlan) ---
    plan = BillingPlan(slug=f"pro-{_uid()[:8]}", name="Pro", monthly_token_quota=1000)
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
    apikey = ApiKey(id=_uid(), user_id=user_id, name="k", key_prefix="ab", key_hash=_uid())

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
    ev = EmailVerification(id=_uid(), email=user_email, code="123456", expires_at=_now())

    # --- OrgMembership: user is a MEMBER of the OTHER user's org ---
    membership = OrgMembership(id=_uid(), org_id=org_id, user_id=user_id, role="member")

    # --- AuditLog whose actor is this user ---
    audit = AuditLog(id=_uid(), actor_id=user_id, actor_label="u", action="something")

    db.add_all(
        [
            msg,
            kbdoc,
            action,
            col,
            mc,
            mcp,
            oauth,
            wf,
            skill,
            notif,
            sub,
            apikey,
            login,
            caseresult,
            calllog,
            rsub,
            ev,
            membership,
            audit,
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
    assert await _count(db, EvalDataset, id=ds.id) == 0
    assert await _count(db, EvalCase, id=case.id) == 0
    assert await _count(db, EvalRun, id=run.id) == 0
    assert await _count(db, EvalCaseResult, id=caseresult.id) == 0
    assert await _count(db, ResourceSubscription, id=rsub.id) == 0
    assert await _count(db, EmailVerification, id=ev.id) == 0
    assert await _count(db, OrgMembership, id=membership.id) == 0

    # --- Survivors ---
    # The other user's organization (and the other user) still exist.
    assert await _count(db, Organization, id=org_id) == 1
    assert await _count(db, User, id=other_id) == 1
    # The records that outlive the account keep their rows and the user's
    # id (what is taken out of them: the tests at the end of this file).
    assert await _count(db, AuditLog, id=audit.id, actor_id=user_id) == 1
    assert await _count(db, LoginHistory, id=login.id, user_id=user_id) == 1
    assert await _count(db, ConnectorCallLog, id=calllog.id, user_id=user_id) == 1


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
    wf = Workflow(id=_uid(), user_id=other_id, name="wf", blueprint={"nodes": [], "edges": []})
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
        await db.execute(select(OrgMembership).where(OrgMembership.id == membership.id))
    ).scalar_one()
    assert surviving_membership.invited_by is None

    surviving_version = (
        await db.execute(select(WorkflowVersion).where(WorkflowVersion.id == version.id))
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


# ---------------------------------------------------------------------------
# Test 5 — the records that outlive the account lose what identifies the user
# ---------------------------------------------------------------------------


async def _person(db: AsyncSession, name: str, **fields: object) -> User:
    user = User(id=str(uuid.uuid4()), username=name, email=f"{name}@example.test", **fields)
    db.add(user)
    await db.flush()
    return user


async def _fresh(db: AsyncSession, model: type, row_id: str) -> Any:
    """The row as the database has it now, not as the session remembers it."""
    query = select(model).where(model.id == row_id)  # type: ignore[attr-defined]
    return (await db.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()


def _everything(row: object) -> str:
    """Every stored column of *row* as one searchable string."""
    columns = row.__mapper__.column_attrs  # type: ignore[attr-defined]
    return json.dumps({c.key: getattr(row, c.key) for c in columns}, default=str)


@pytest.mark.asyncio
async def test_sign_in_history_stays_without_name_or_address(db: AsyncSession) -> None:
    """The rows were deleted with the account: a user could erase the trace
    of their own sign-ins by deleting it."""
    carol = await _person(db, "carol")
    other = await _person(db, "other")
    label = f"deleted-user-{carol.id[:8]}"
    mine = LoginHistory(
        id=_uid(), user_id=carol.id, username="carol", email=carol.email,
        ip_address="198.51.100.5", user_agent="CarolBrowser/1", success=False,
        failure_reason="wrong_password", method="password",
    )  # fmt: skip
    # Typed before the account existed: no user id, her address.
    before = LoginHistory(
        id=_uid(), user_id=None, email=carol.email, ip_address="203.0.113.50", success=False
    )
    theirs = LoginHistory(
        id=_uid(), user_id=other.id, username="other", email=other.email,
        ip_address="198.51.100.6", user_agent="OtherBrowser/1", success=True,
    )  # fmt: skip
    # Her address typed in capitals: the sign-in form does not match it to
    # the account, so the row has no user id.
    shouted = LoginHistory(
        id=_uid(), user_id=None, email=carol.email.upper(), ip_address="203.0.113.51",
        success=False, failure_reason="user_not_found",
    )  # fmt: skip
    # She signed in as carol-old@example.test before an administrator changed
    # her address; attempts with the old address, before and after the
    # change, and one only an audit row knows of.
    as_before = LoginHistory(
        id=_uid(), user_id=carol.id, username="caz", email="carol-old@example.test",
        ip_address="198.51.100.7", user_agent="CarolBrowser/0", success=True,
    )  # fmt: skip
    old_address = LoginHistory(
        id=_uid(), user_id=None, email="Carol-Old@Example.test", ip_address="203.0.113.52",
        success=False, failure_reason="user_not_found",
    )  # fmt: skip
    first_address = LoginHistory(
        id=_uid(), user_id=None, email="cd@first.test", ip_address="203.0.113.53",
        success=False, failure_reason="user_not_found",
    )  # fmt: skip
    changed = AuditLog(
        id=_uid(), action="user.update", actor_id=other.id, actor_label="other",
        actor_kind="admin", target_type="user", target_id=carol.id, target_label="carol",
        changes={"email": {"from": "CD@first.test", "to": "carol-old@example.test"}},
    )  # fmt: skip
    # Somebody else's address, typed: stays.
    stranger = LoginHistory(
        id=_uid(), user_id=None, email="Someone@Example.test", ip_address="203.0.113.54",
        success=False, failure_reason="user_not_found",
    )  # fmt: skip
    codes = [
        EmailVerification(email=address, code="123456", purpose="reset_password",
                          expires_at=_now())
        for address in (carol.email.upper(), "carol-old@example.test", "someone@example.test")
    ]  # fmt: skip
    db.add_all([mine, before, theirs, shouted, as_before, old_address, first_address,
                changed, stranger, *codes])  # fmt: skip
    await db.commit()

    await purge_user_data(db, carol.id)
    await db.commit()

    kept = await _fresh(db, LoginHistory, mine.id)
    assert kept is not None, "the sign-in row was deleted with the account"
    assert (kept.user_id, kept.username, kept.email) == (carol.id, label, label)
    assert kept.ip_address is None and kept.user_agent is None
    # What happened stays.
    assert (kept.success, kept.failure_reason, kept.method) == (False, "wrong_password", "password")
    earlier = await _fresh(db, LoginHistory, before.id)
    assert earlier is not None and earlier.email == label
    assert earlier.ip_address == "203.0.113.50"
    untouched = await _fresh(db, LoginHistory, theirs.id)
    assert untouched is not None
    assert (untouched.username, untouched.ip_address) == ("other", "198.51.100.6")
    for row in (kept, earlier):
        assert "carol" not in _everything(row)
    for row_id in (shouted.id, old_address.id, first_address.id):
        attempt = await _fresh(db, LoginHistory, row_id)
        assert attempt.email == label, attempt.email
        assert attempt.ip_address is not None
    previous = await _fresh(db, LoginHistory, as_before.id)
    assert (previous.username, previous.email, previous.ip_address) == (label, label, None)
    assert (await _fresh(db, LoginHistory, stranger.id)).email == "Someone@Example.test"
    left = (await db.execute(select(EmailVerification.email))).scalars().all()
    assert left == ["someone@example.test"]


@pytest.mark.asyncio
async def test_call_records_and_the_usage_ledger_stay_as_they_are(db: AsyncSession) -> None:
    """Call records were deleted with the account, so an organization's
    record of what was called through its agents shrank with every member
    who left."""
    from fim_one.db.models.usage_ledger import UsageLedger

    carol = await _person(db, "carol")
    call = ConnectorCallLog(
        id=_uid(), connector_id=_uid(), connector_name="CRM", action_name="list",
        user_id=carol.id, org_id="org-a", request_method="GET", request_url="https://x",
        success=True,
    )  # fmt: skip
    usage = UsageLedger(
        id=_uid(), user_id=carol.id, org_id="org-a", source_type="conversation",
        entry="chat", total_tokens=120,
    )  # fmt: skip
    db.add_all([call, usage])
    await db.commit()

    await purge_user_data(db, carol.id)
    await db.commit()

    kept_call = await _fresh(db, ConnectorCallLog, call.id)
    assert kept_call is not None, "the call record was deleted with the account"
    assert (kept_call.user_id, kept_call.org_id, kept_call.connector_name) == (
        carol.id, "org-a", "CRM",
    )  # fmt: skip
    kept_usage = await _fresh(db, UsageLedger, usage.id)
    assert kept_usage is not None
    assert (kept_usage.user_id, kept_usage.total_tokens) == (carol.id, 120)


@pytest.mark.asyncio
async def test_audit_rows_name_the_user_by_the_label_and_lose_their_address(
    db: AsyncSession,
) -> None:
    """Only the actor id was cleared: the username stayed in the actor
    label, the target label, detail lines and changes, with the address."""
    carol = await _person(db, "carol", display_name="Carol Danvers")
    root = await _person(db, "root", is_admin=True)
    label = f"deleted-user-{carol.id[:8]}"

    def event(action: str, **fields: object) -> AuditLog:
        row = AuditLog(id=_uid(), action=action, **fields)
        db.add(row)
        return row

    own = event(
        "auth.username_change", actor_id=carol.id, actor_label="carol", actor_kind="user",
        target_type="user", target_id=carol.id, target_label="carol",
        changes={"username": {"from": "caz", "to": "carol"}},
        ip_address="198.51.100.5", user_agent="CarolBrowser/1",
    )  # fmt: skip
    in_org = event(
        "agent.update", actor_id=carol.id, actor_label="carol", actor_kind="user",
        org_id="org-a", target_type="agent", target_id="a1", target_label="Helper",
        detail="published to carol@example.test",
        changes={"is_active": {"from": False, "to": True}, "notify": {"from": None, "to": "carol@example.test"}},
        ip_address="198.51.100.5", user_agent="CarolBrowser/1",
    )  # fmt: skip
    by_admin = event(
        "user.update", actor_id=root.id, actor_label="root", actor_kind="admin",
        target_type="user", target_id=carol.id, target_label="carol",
        detail="carol",
        changes={"email": {"from": "c@old.test", "to": "carol@example.test"}},
        ip_address="198.51.100.99", user_agent="RootBrowser/9",
    )  # fmt: skip
    role = event(
        "org.member_role_change", actor_id=root.id, actor_label="root", actor_kind="user",
        org_id="org-a", target_type="user", target_id=carol.id, target_label="carol",
        changes={"role": {"from": "member", "to": "admin"}}, ip_address="198.51.100.99",
    )  # fmt: skip
    # Somebody else's rows, one of them about a user whose name starts the same.
    unrelated = event(
        "settings.update", actor_id=root.id, actor_label="root", actor_kind="admin",
        detail="carolina joined; scarol left", ip_address="198.51.100.99",
    )  # fmt: skip
    plain = event("auth.sessions_revoke", actor_id=carol.id, actor_label="carol",
                  ip_address="198.51.100.5")  # fmt: skip
    # She, as an administrator, renamed bob to a name equal to her own and
    # gave him her address: the names are bob's now and stay; her address
    # goes wherever it is.
    on_bob = event(
        "user.update", actor_id=carol.id, actor_label="carol", actor_kind="admin",
        target_type="user", target_id="bob-id", target_label="bob",
        changes={
            "display_name": {"from": "Bob", "to": "Carol Danvers"},
            "username": {"from": "bobby", "to": "carol"},
            "email": {"from": "bob@example.test", "to": "Carol@Example.test"},
        },
    )  # fmt: skip
    await db.commit()

    await purge_user_data(db, carol.id)
    await db.commit()

    rows = {
        name: await _fresh(db, AuditLog, row.id)
        for name, row in {
            "own": own, "in_org": in_org, "by_admin": by_admin, "role": role,
            "unrelated": unrelated, "plain": plain, "on_bob": on_bob,
        }.items()
    }  # fmt: skip
    assert all(row is not None for row in rows.values())
    for name in ("own", "in_org", "by_admin", "role", "plain"):
        text = _everything(rows[name])
        assert "carol" not in text.lower().replace(label, ""), f"{name}: {text}"
        assert "198.51.100.5" not in text and "CarolBrowser" not in text, name

    # Her own rows: her id, the label, no address.
    assert (rows["own"].actor_id, rows["own"].actor_label) == (carol.id, label)
    assert rows["own"].target_label == label and rows["own"].ip_address is None
    assert rows["own"].changes == {"username": {"changed": True}}
    assert rows["in_org"].detail == f"published to {label}"
    assert rows["in_org"].changes == {
        "is_active": {"from": False, "to": True},
        "notify": {"from": None, "to": label},
    }
    # No ``changes`` before, none after: SQL NULL, not the JSON null.
    assert rows["plain"].changes is None
    assert (
        await db.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.id == plain.id, AuditLog.changes.is_(None)
            )
        )
    ).scalar_one() == 1  # fmt: skip
    # An administrator's rows about her: the administrator and their
    # address stay, her names go.
    assert (rows["by_admin"].actor_label, rows["by_admin"].ip_address) == ("root", "198.51.100.99")
    assert rows["by_admin"].user_agent == "RootBrowser/9"
    assert rows["by_admin"].target_label == label
    assert rows["by_admin"].detail == label
    assert rows["by_admin"].changes == {"email": {"changed": True}}
    # What changed that is not a name of hers is kept.
    assert rows["role"].changes == {"role": {"from": "member", "to": "admin"}}
    assert rows["role"].target_label == label
    # A row she acted in about somebody else: her label as the actor, the
    # other user's names as they were, her address replaced.
    assert (rows["on_bob"].actor_label, rows["on_bob"].target_label) == (label, "bob")
    assert rows["on_bob"].changes == {
        "display_name": {"from": "Bob", "to": "Carol Danvers"},
        "username": {"from": "bobby", "to": "carol"},
        "email": {"from": "bob@example.test", "to": label},
    }
    # Rows she is neither the actor nor the target of are not rewritten.
    assert rows["unrelated"].detail == "carolina joined; scarol left"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["member", "admin", "true"])
async def test_a_username_that_is_an_ordinary_word_leaves_the_rest_of_the_row(
    db: AsyncSession, name: str
) -> None:
    """A username was replaced wherever it stood as a word, so deleting a
    user called ``member`` rewrote the roles in the rows about them."""
    person = await _person(db, name, display_name="Admin")
    root = await _person(db, "root", is_admin=True)
    label = f"deleted-user-{person.id[:8]}"
    role = AuditLog(
        id=_uid(), action="org.member_role_change", actor_id=root.id, actor_label="root",
        actor_kind="user", org_id="org-a", target_type="user", target_id=person.id,
        target_label=name, changes={"role": {"from": "member", "to": "admin"}},
        detail="role=admin; was member",
    )  # fmt: skip
    own = AuditLog(
        id=_uid(), action="workflow.schedule_update", actor_id=person.id, actor_label=name,
        actor_kind="user", target_type="workflow", target_id="w1", target_label="Nightly",
        changes={"enabled": {"from": "false", "to": "true"}, "role": {"from": "admin", "to": "member"}},
        detail="schedule_enabled=true; by admin",
    )  # fmt: skip
    renamed = AuditLog(
        id=_uid(), action="auth.username_change", actor_id=person.id, actor_label=name,
        actor_kind="user", target_type="user", target_id=person.id, target_label=name,
        changes={"username": {"from": "old", "to": name}},
    )  # fmt: skip
    db.add_all([role, own, renamed])
    await db.commit()

    await purge_user_data(db, person.id)
    await db.commit()

    role_now = await _fresh(db, AuditLog, role.id)
    assert role_now.target_label == label
    assert role_now.changes == {"role": {"from": "member", "to": "admin"}}
    assert role_now.detail == "role=admin; was member"
    own_now = await _fresh(db, AuditLog, own.id)
    assert own_now.actor_label == label
    assert own_now.changes == {
        "enabled": {"from": "false", "to": "true"},
        "role": {"from": "admin", "to": "member"},
    }
    assert own_now.detail == "schedule_enabled=true; by admin"
    renamed_now = await _fresh(db, AuditLog, renamed.id)
    assert renamed_now.changes == {"username": {"changed": True}}


@pytest.mark.asyncio
async def test_the_review_trail_names_the_user_by_the_label(db: AsyncSession) -> None:
    from fim_one.db.models.review_log import ReviewLog

    carol = await _person(db, "carol")
    entry = ReviewLog(
        org_id="org-a", resource_type="agent", resource_id="a1", resource_name="Helper",
        action="approved", actor_id=carol.id, actor_username="carol", note="fine",
    )  # fmt: skip
    db.add(entry)
    await db.commit()

    await purge_user_data(db, carol.id)
    await db.commit()

    kept = await _fresh(db, ReviewLog, entry.id)
    assert kept is not None
    assert (kept.actor_id, kept.actor_username) == (carol.id, f"deleted-user-{carol.id[:8]}")
    assert (kept.action, kept.note) == ("approved", "fine")


async def test_an_earlier_address_another_account_holds_now_is_left_to_that_account(
    db: AsyncSession,
) -> None:
    """Carol gave up an address and Dana registered with it: what is recorded
    against that address since is Dana's."""
    carol = await _person(db, "carol")
    dana = await _person(db, "dana")
    dana.email = "shared@example.test"
    was_carols = AuditLog(
        id=_uid(), action="user.update", actor_id=dana.id, actor_label="dana",
        actor_kind="admin", target_type="user", target_id=carol.id, target_label="carol",
        changes={"email": {"from": "shared@example.test", "to": carol.email}},
    )  # fmt: skip
    danas_attempt = LoginHistory(
        id=_uid(), user_id=None, email="Shared@Example.test", ip_address="203.0.113.60",
        success=False, failure_reason="user_not_found",
    )  # fmt: skip
    danas_code = EmailVerification(
        email="shared@example.test", code="654321", purpose="reset_password", expires_at=_now()
    )
    db.add_all([was_carols, danas_attempt, danas_code])
    await db.commit()

    await purge_user_data(db, carol.id)
    await db.commit()

    attempt = await _fresh(db, LoginHistory, danas_attempt.id)
    assert attempt is not None and attempt.email == "Shared@Example.test"
    codes = (
        await db.execute(
            select(EmailVerification).where(EmailVerification.email == "shared@example.test")
        )
    ).scalars().all()
    assert len(codes) == 1, "another account's pending code went with the deleted user"
