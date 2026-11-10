"""A completion-notification channel must lie inside the agent's organization.

The notification carries the user's message and the agent's answer. An
agent with an organization may only use that organization's channels; a
personal agent only those of an organization its owner belongs to. The
same rule is checked when the agent is saved.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401  (register models)
from fim_one.db.base import Base
from fim_one.db.models.agent import Agent
from fim_one.db.models.channel import Channel
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User
from fim_one.web.api.agents import (
    _completion_channel_id,
    _validate_binding_ownership,
    create_agent,
    update_agent,
)
from fim_one.web.exceptions import AppError
from fim_one.web.notifications import notify_agent_completion
from fim_one.web.schemas.agent import AgentCreate, AgentUpdate


@pytest.fixture(autouse=True)
def _stable_cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-completion-key-abcdefghijklmn")
    enc._CREDENTIAL_KEY_RAW = "test-completion-key-abcdefghijklmn"
    enc._cred_fernet_instance = None


@pytest.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
async def seed(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """Org A with a member and a channel; an outsider who belongs to org B."""
    ids = {
        key: str(uuid.uuid4())
        for key in ("member", "outsider", "org_a", "org_b", "channel_a", "channel_b")
    }
    async with session_factory() as db:
        db.add(User(id=ids["member"], username="member", email="member@a.test"))
        db.add(User(id=ids["outsider"], username="outsider", email="outsider@b.test"))
        for org, owner in (("org_a", "member"), ("org_b", "outsider")):
            db.add(
                Organization(
                    id=ids[org], name=org, slug=f"{org}-{ids[org][:6]}", owner_id=ids[owner]
                )
            )
            db.add(OrgMembership(org_id=ids[org], user_id=ids[owner], role="owner"))
        for channel, org, owner in (
            ("channel_a", "org_a", "member"),
            ("channel_b", "org_b", "outsider"),
        ):
            db.add(
                Channel(
                    id=ids[channel],
                    name=channel,
                    type="feishu",
                    org_id=ids[org],
                    created_by=ids[owner],
                    is_active=True,
                    config={"app_id": "cli_x", "app_secret": "s", "chat_id": f"oc_{org}"},
                )
            )
        await db.commit()
    return ids


def _agent(*, org_id: str | None, owner_id: str | None, channel_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-1",
        name="Summariser",
        org_id=org_id,
        user_id=owner_id,
        model_config_json=_config(channel_id),
    )


def _config(channel_id: str | None, *, enabled: bool = True) -> dict[str, Any]:
    return {"notifications": {"on_complete": {"enabled": enabled, "channel_id": channel_id}}}


def _feishu_http() -> tuple[MagicMock, AsyncMock]:
    client = AsyncMock()
    token = MagicMock()
    token.json = MagicMock(return_value={"code": 0, "tenant_access_token": "t", "expire": 7200})
    sent = MagicMock()
    sent.json = MagicMock(return_value={"code": 0})
    client.post = AsyncMock(side_effect=[token, sent])
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, client


async def _notify(agent: SimpleNamespace, session_factory: Any) -> AsyncMock:
    factory, client = _feishu_http()
    with patch("httpx.AsyncClient", factory):
        await notify_agent_completion(
            agent=agent,
            conversation_id="conv-1",
            user_message="the user's question",
            final_answer="the agent's answer",
            tools_used=[],
            duration_seconds=1.0,
            session_factory=session_factory,
        )
    return client


class TestSendTime:
    @pytest.mark.asyncio
    async def test_personal_agent_cannot_post_to_an_organization_its_owner_is_not_in(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        """The N5 case: knowing a channel id used to be enough."""
        agent = _agent(org_id=None, owner_id=seed["outsider"], channel_id=seed["channel_a"])
        client = await _notify(agent, session_factory)
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_personal_agent_posts_to_its_owners_organization(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        agent = _agent(org_id=None, owner_id=seed["member"], channel_id=seed["channel_a"])
        client = await _notify(agent, session_factory)
        assert client.post.await_count == 2
        assert client.post.await_args_list[1].kwargs["json"]["receive_id"] == "oc_org_a"

    @pytest.mark.asyncio
    async def test_agent_with_no_organization_and_no_owner_posts_nowhere(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        agent = _agent(org_id=None, owner_id=None, channel_id=seed["channel_a"])
        client = await _notify(agent, session_factory)
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_organization_agent_uses_only_its_own_organizations_channel(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        # The owner belongs to org A, but the agent is org B's.
        foreign = _agent(org_id=seed["org_b"], owner_id=seed["member"], channel_id=seed["channel_a"])
        client = await _notify(foreign, session_factory)
        client.post.assert_not_called()

        own = _agent(org_id=seed["org_a"], owner_id=seed["member"], channel_id=seed["channel_a"])
        client = await _notify(own, session_factory)
        assert client.post.await_count == 2


class TestSaveTime:
    def test_channel_is_read_only_from_an_enabled_notification(self) -> None:
        assert _completion_channel_id(_config("c-1")) == "c-1"
        assert _completion_channel_id(_config("c-1", enabled=False)) is None
        assert _completion_channel_id(_config(None)) is None
        assert _completion_channel_id({"notifications": "junk"}) is None
        assert _completion_channel_id(None) is None

    @pytest.mark.asyncio
    async def test_non_member_cannot_bind(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        async with session_factory() as db:
            for channel_id in (seed["channel_a"], "no-such-channel"):
                with pytest.raises(AppError) as exc:
                    await _validate_binding_ownership(
                        seed["outsider"], db, completion_channel_id=channel_id
                    )
                assert exc.value.error_code == "completion_channel_denied"
                assert exc.value.status_code == 403
            await _validate_binding_ownership(
                seed["member"], db, completion_channel_id=seed["channel_a"]
            )

    @pytest.mark.asyncio
    async def test_create_is_refused_with_a_foreign_channel(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        async with session_factory() as db:
            outsider = await db.get(User, seed["outsider"])
            assert outsider is not None
            with pytest.raises(AppError) as exc:
                await create_agent(
                    AgentCreate(name="leaky", model_config_json=_config(seed["channel_a"])),
                    current_user=outsider,
                    db=db,
                )
            assert exc.value.error_code == "completion_channel_denied"

            created = await create_agent(
                AgentCreate(name="fine", model_config_json=_config(seed["channel_b"])),
                current_user=outsider,
                db=db,
            )
            assert created.data["name"] == "fine"

    @pytest.mark.asyncio
    async def test_update_checks_the_channel_only_when_it_changes(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        agent_id = str(uuid.uuid4())
        async with session_factory() as db:
            # A row saved before the check existed, pointing at a foreign channel.
            db.add(
                Agent(
                    id=agent_id,
                    user_id=seed["outsider"],
                    name="legacy",
                    model_config_json=_config(seed["channel_a"]),
                    status="draft",
                )
            )
            await db.commit()
            outsider = await db.get(User, seed["outsider"])
            assert outsider is not None

            # Saving something else with the stored config is not blocked ...
            await update_agent(
                agent_id,
                AgentUpdate(name="renamed", model_config_json=_config(seed["channel_a"])),
                current_user=outsider,
                db=db,
            )
            # ... pointing it at another foreign channel is.
            other = str(uuid.uuid4())
            db.add(
                Channel(
                    id=other,
                    name="other",
                    type="feishu",
                    org_id=seed["org_a"],
                    created_by=seed["member"],
                    is_active=True,
                    config={},
                )
            )
            await db.commit()
            with pytest.raises(AppError) as exc:
                await update_agent(
                    agent_id,
                    AgentUpdate(model_config_json=_config(other)),
                    current_user=outsider,
                    db=db,
                )
            assert exc.value.error_code == "completion_channel_denied"


class TestApprovalChannelType:
    """An approval channel must be able to bring a decision back."""

    async def _webhook_channel(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str], kind: str
    ) -> str:
        channel_id = str(uuid.uuid4())
        config: dict[str, Any] = (
            {"to": "ops@a.test"}
            if kind == "email"
            else {"webhook_url": "https://hooks.slack.com/services/T/B/x"}
        )
        async with session_factory() as db:
            db.add(
                Channel(
                    id=channel_id,
                    name=kind,
                    type=kind,
                    org_id=seed["org_a"],
                    created_by=seed["member"],
                    is_active=True,
                    config=config,
                )
            )
            await db.commit()
        return channel_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["slack_webhook", "lark_webhook", "wecom_webhook", "email", "wecom"])
    async def test_a_channel_that_cannot_carry_approvals_is_refused_on_create(
        self,
        kind: str,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
    ) -> None:
        channel_id = await self._webhook_channel(session_factory, seed, kind)
        async with session_factory() as db:
            member = await db.get(User, seed["member"])
            assert member is not None
            with pytest.raises(AppError) as exc:
                await create_agent(
                    AgentCreate(name="gated", approval_channel_id=channel_id),
                    current_user=member,
                    db=db,
                )
            assert exc.value.error_code == "approval_channel_type_unsupported"
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_feishu_app_channel_is_accepted(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        async with session_factory() as db:
            member = await db.get(User, seed["member"])
            assert member is not None
            created = await create_agent(
                AgentCreate(name="gated", approval_channel_id=seed["channel_a"]),
                current_user=member,
                db=db,
            )
            assert created.data["approval_channel_id"] == seed["channel_a"]

    @pytest.mark.asyncio
    async def test_a_foreign_channel_is_still_denied_before_its_type_is_looked_at(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        channel_id = await self._webhook_channel(session_factory, seed, "slack_webhook")
        async with session_factory() as db:
            with pytest.raises(AppError) as exc:
                await _validate_binding_ownership(
                    seed["outsider"], db, approval_channel_id=channel_id
                )
            assert exc.value.error_code == "approval_channel_denied"

    @pytest.mark.asyncio
    async def test_update_refuses_a_new_binding_but_not_one_saved_earlier(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        stored = await self._webhook_channel(session_factory, seed, "slack_webhook")
        other = await self._webhook_channel(session_factory, seed, "email")
        agent_id = str(uuid.uuid4())
        async with session_factory() as db:
            # A row saved before the check existed.
            db.add(
                Agent(
                    id=agent_id,
                    user_id=seed["member"],
                    name="legacy",
                    status="draft",
                    approval_channel_id=stored,
                )
            )
            await db.commit()
            member = await db.get(User, seed["member"])
            assert member is not None

            # The form sends the stored binding with every save: not blocked.
            await update_agent(
                agent_id,
                AgentUpdate(name="renamed", approval_channel_id=stored),
                current_user=member,
                db=db,
            )
            # Choosing another channel that cannot carry approvals is.
            with pytest.raises(AppError) as exc:
                await update_agent(
                    agent_id,
                    AgentUpdate(approval_channel_id=other),
                    current_user=member,
                    db=db,
                )
            assert exc.value.error_code == "approval_channel_type_unsupported"
            # A Feishu app channel, or none, is fine.
            for channel_id in (seed["channel_a"], None):
                updated = await update_agent(
                    agent_id,
                    AgentUpdate(approval_channel_id=channel_id),
                    current_user=member,
                    db=db,
                )
                assert updated.data["approval_channel_id"] == channel_id


class TestSaveTimeAgentOrganization:
    """An agent that has an organization may only be bound to that organization's channels."""

    async def _two_org_member(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        """The member of org A joins org B as well."""
        async with session_factory() as db:
            db.add(OrgMembership(org_id=seed["org_b"], user_id=seed["member"], role="member"))
            await db.commit()

    async def _org_agent(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
        **fields: Any,
    ) -> str:
        agent_id = str(uuid.uuid4())
        async with session_factory() as db:
            db.add(
                Agent(
                    id=agent_id,
                    user_id=seed["member"],
                    org_id=seed["org_a"],
                    name="org agent",
                    status="published",
                    **fields,
                )
            )
            await db.commit()
        return agent_id

    @pytest.mark.asyncio
    async def test_completion_channel_of_another_of_the_savers_organizations_is_refused(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        """Send time looks only in the agent's organization; save time now does too."""
        await self._two_org_member(session_factory, seed)
        agent_id = await self._org_agent(session_factory, seed)
        async with session_factory() as db:
            member = await db.get(User, seed["member"])
            assert member is not None
            with pytest.raises(AppError) as exc:
                await update_agent(
                    agent_id,
                    AgentUpdate(model_config_json=_config(seed["channel_b"])),
                    current_user=member,
                    db=db,
                )
            assert exc.value.error_code == "completion_channel_not_in_agent_org"
            assert exc.value.status_code == 403

            updated = await update_agent(
                agent_id,
                AgentUpdate(model_config_json=_config(seed["channel_a"])),
                current_user=member,
                db=db,
            )
            assert _completion_channel_id(updated.data["model_config_json"]) == seed["channel_a"]

    @pytest.mark.asyncio
    async def test_what_is_saved_is_what_is_sent(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        """The channel save time accepts for an organization agent is delivered to."""
        await self._two_org_member(session_factory, seed)
        for channel, delivered in (("channel_a", True), ("channel_b", False)):
            async with session_factory() as db:
                try:
                    await _validate_binding_ownership(
                        seed["member"],
                        db,
                        completion_channel_id=seed[channel],
                        agent_org_id=seed["org_a"],
                    )
                    accepted = True
                except AppError:
                    accepted = False
            agent = _agent(org_id=seed["org_a"], owner_id=seed["member"], channel_id=seed[channel])
            client = await _notify(agent, session_factory)
            assert accepted is delivered
            assert (client.post.await_count == 2) is delivered

    @pytest.mark.asyncio
    async def test_an_agent_with_no_organization_keeps_the_savers_memberships(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        await self._two_org_member(session_factory, seed)
        async with session_factory() as db:
            for channel in ("channel_a", "channel_b"):
                await _validate_binding_ownership(
                    seed["member"], db, completion_channel_id=seed[channel], agent_org_id=None
                )

    @pytest.mark.asyncio
    async def test_approval_channel_follows_the_same_rule_when_it_changes(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        await self._two_org_member(session_factory, seed)
        # A binding saved before the check existed, to the other organization.
        agent_id = await self._org_agent(
            session_factory, seed, approval_channel_id=seed["channel_b"]
        )
        other_b = str(uuid.uuid4())
        async with session_factory() as db:
            db.add(
                Channel(
                    id=other_b,
                    name="other b",
                    type="feishu",
                    org_id=seed["org_b"],
                    created_by=seed["outsider"],
                    is_active=True,
                    config={},
                )
            )
            await db.commit()
            member = await db.get(User, seed["member"])
            assert member is not None
            await update_agent(
                agent_id,
                AgentUpdate(name="renamed", approval_channel_id=seed["channel_b"]),
                current_user=member,
                db=db,
            )
            with pytest.raises(AppError) as exc:
                await update_agent(
                    agent_id,
                    AgentUpdate(approval_channel_id=other_b),
                    current_user=member,
                    db=db,
                )
            assert exc.value.error_code == "approval_channel_not_in_agent_org"
            updated = await update_agent(
                agent_id,
                AgentUpdate(approval_channel_id=seed["channel_a"]),
                current_user=member,
                db=db,
            )
            assert updated.data["approval_channel_id"] == seed["channel_a"]
