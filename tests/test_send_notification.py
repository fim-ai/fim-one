"""Tests for the ``send_notification`` built-in tool and who is offered it.

The tool sends to the enabled channels of the organizations its acting
user belongs to. The instance-wide webhooks from environment variables
remain selectable for one release as ``instance:<provider>``.
"""

from __future__ import annotations

import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401  (register models)
from fim_one.core.channels import ChannelSendResult
from fim_one.core.tool.builtin.send_notification import SendNotificationTool
from fim_one.core.tool.registry import ToolRegistry
from fim_one.db.base import Base
from fim_one.db.models.channel import Channel
from fim_one.db.models.organization import Organization, OrgMembership
from fim_one.db.models.user import User

SLACK_A = "https://hooks.slack.com/services/A/A/org-a"
SLACK_B = "https://hooks.slack.com/services/B/B/org-b"
SLACK_ENV = "https://hooks.slack.com/services/E/E/instance"

_NO_INSTANCE = {"SLACK_WEBHOOK_URL": "", "LARK_WEBHOOK_URL": "", "WECOM_WEBHOOK_URL": ""}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    import fim_one.core.security.encryption as enc

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-notify-tool-key-abcdefghijklmn")
    enc._CREDENTIAL_KEY_RAW = "test-notify-tool-key-abcdefghijklmn"
    enc._cred_fernet_instance = None
    for name, value in _NO_INSTANCE.items():
        monkeypatch.setenv(name, value)


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
    """Alice is a member of org A; org B is somebody else's.

    Org A: ``Ops`` (Slack webhook), ``Alerts`` (Feishu app), ``Retired``
    (switched off). Org B: ``Board`` (Slack webhook).
    """
    ids = {
        key: str(uuid.uuid4())
        for key in ("alice", "bob", "org_a", "org_b", "ops", "alerts", "retired", "board")
    }
    async with session_factory() as db:
        db.add(User(id=ids["alice"], username="alice", email="alice@a.test"))
        db.add(User(id=ids["bob"], username="bob", email="bob@b.test"))
        for org, owner in (("org_a", "alice"), ("org_b", "bob")):
            db.add(
                Organization(
                    id=ids[org], name=org, slug=f"{org}-{ids[org][:6]}", owner_id=ids[owner]
                )
            )
            db.add(OrgMembership(org_id=ids[org], user_id=ids[owner], role="member"))

        def channel(key: str, org: str, name: str, kind: str, config: dict[str, Any], **kw: Any) -> None:
            db.add(
                Channel(
                    id=ids[key],
                    name=name,
                    type=kind,
                    org_id=ids[org],
                    created_by=ids["alice"],
                    config=config,
                    **kw,
                )
            )

        channel("ops", "org_a", "Ops", "slack_webhook", {"webhook_url": SLACK_A}, is_active=True)
        channel(
            "alerts",
            "org_a",
            "Alerts",
            "feishu",
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_alerts"},
            is_active=True,
        )
        channel(
            "retired", "org_a", "Retired", "slack_webhook", {"webhook_url": SLACK_A}, is_active=False
        )
        channel("board", "org_b", "Board", "slack_webhook", {"webhook_url": SLACK_B}, is_active=True)
        await db.commit()
    return ids


def _tool(session_factory: Any, user_id: str | None) -> SendNotificationTool:
    return SendNotificationTool(user_id=user_id, session_factory=session_factory)


def _webhook_send() -> Any:
    """Patch the one method every webhook channel posts through.

    The mock records ``(url, payload)`` so a test can tell whose webhook
    was used.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake(self: Any, payload: dict[str, Any]) -> ChannelSendResult:
        calls.append((self.config["webhook_url"], payload))
        return ChannelSendResult(ok=True)

    return patch("fim_one.core.channels.webhook.WebhookChannel.send_message", new=fake), calls


class TestToolShape:
    def test_properties(self) -> None:
        tool = SendNotificationTool()
        assert tool.name == "send_notification"
        assert tool.category == "general"
        schema = tool.parameters_schema
        assert set(schema["properties"]) == {"channel", "title", "body"}
        assert schema["required"] == ["title", "body"]

    @pytest.mark.asyncio
    async def test_title_and_body_are_required_to_send(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        tool = _tool(session_factory, seed["alice"])
        assert "[Error] 'title'" in await tool.run(channel="Ops", title="", body="b")
        assert "[Error] 'body'" in await tool.run(channel="Ops", title="t", body="")


class TestOrganizationChannels:
    @pytest.mark.asyncio
    async def test_sends_to_a_channel_of_the_callers_organization_by_name(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        with patcher:
            result = await _tool(session_factory, seed["alice"]).run(
                channel="ops", title="Deploy", body="v2 is out"
            )
        assert result == "Notification sent to channel 'Ops'."
        assert [url for url, _ in calls] == [SLACK_A]
        assert calls[0][1]["blocks"][0]["text"]["text"] == "Deploy"

    @pytest.mark.asyncio
    async def test_sends_by_id_and_through_a_feishu_app(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=True))
        with patch("fim_one.core.channels.feishu.FeishuChannel.send_message", new=send):
            result = await _tool(session_factory, seed["alice"]).run(
                channel=seed["alerts"], title="Deploy", body="v2 is out"
            )
        assert result == "Notification sent to channel 'Alerts'."
        payload = send.await_args.args[0]
        assert payload["chat_id"] == "oc_alerts"
        assert payload["content"] == "Deploy\n\nv2 is out"

    @pytest.mark.asyncio
    async def test_another_organizations_channel_is_out_of_reach(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        tool = _tool(session_factory, seed["alice"])
        with patcher:
            by_name = await tool.run(channel="Board", title="t", body="b")
            by_id = await tool.run(channel=seed["board"], title="t", body="b")
        assert calls == []
        for result in (by_name, by_id):
            assert result.startswith("[Error] No channel named")
            assert "Board" not in result.split("Available channels:")[1]
            assert seed["board"] not in result.split("Available channels:")[1]

    @pytest.mark.asyncio
    async def test_a_switched_off_channel_is_out_of_reach(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        with patcher:
            result = await _tool(session_factory, seed["alice"]).run(
                channel="Retired", title="t", body="b"
            )
        assert calls == []
        assert result.startswith("[Error] No channel named 'Retired'")

    @pytest.mark.asyncio
    async def test_omitting_the_channel_lists_them_and_sends_nothing(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        with patcher:
            listing = await _tool(session_factory, seed["alice"]).run(title="t", body="b")
        assert calls == []
        assert listing.splitlines() == [
            "Available channels:",
            f"- Alerts (feishu, id {seed['alerts']})",
            f"- Ops (slack_webhook, id {seed['ops']})",
        ]

    @pytest.mark.asyncio
    async def test_leaving_the_organization_ends_access(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        tool = _tool(session_factory, seed["alice"])
        async with session_factory() as db:
            await db.execute(delete(OrgMembership).where(OrgMembership.user_id == seed["alice"]))
            await db.commit()
        patcher, calls = _webhook_send()
        with patcher:
            result = await tool.run(channel="Ops", title="t", body="b")
        assert calls == []
        assert "No channels are available" in result

    @pytest.mark.asyncio
    async def test_two_channels_with_one_name_need_an_id(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        twin = str(uuid.uuid4())
        async with session_factory() as db:
            db.add(
                Channel(
                    id=twin,
                    name="Ops",
                    type="slack_webhook",
                    org_id=seed["org_a"],
                    created_by=seed["alice"],
                    config={"webhook_url": SLACK_A},
                    is_active=True,
                )
            )
            await db.commit()
        patcher, calls = _webhook_send()
        tool = _tool(session_factory, seed["alice"])
        with patcher:
            ambiguous = await tool.run(channel="Ops", title="t", body="b")
            assert calls == []
            exact = await tool.run(channel=twin, title="t", body="b")
        assert "2 channels are named 'Ops'" in ambiguous
        assert twin in ambiguous and seed["ops"] in ambiguous
        assert exact == "Notification sent to channel 'Ops'."

    @pytest.mark.asyncio
    async def test_a_failed_delivery_is_reported(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        send = AsyncMock(return_value=ChannelSendResult(ok=False, error="Slack API returned 404"))
        with patch("fim_one.core.channels.webhook.WebhookChannel.send_message", new=send):
            result = await _tool(session_factory, seed["alice"]).run(
                channel="Ops", title="t", body="b"
            )
        assert result == "[Error] Slack API returned 404"

    @pytest.mark.asyncio
    async def test_a_tool_bound_to_no_user_has_no_organization_channels(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        with patcher:
            result = await _tool(session_factory, None).run(channel="Ops", title="t", body="b")
        assert calls == []
        assert "No channels are available" in result


class TestTitle:
    @pytest.mark.asyncio
    async def test_a_title_with_line_breaks_reaches_an_email_channel_as_one_line(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mail delivery raised on a title with a line break in it."""
        import email as email_lib
        from unittest.mock import MagicMock

        for name, value in {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "bot@example.com",
            "SMTP_PASS": "secret",
            "SMTP_SSL": "ssl",
            "SMTP_ALLOWED_DOMAINS": "a.test",
            "SMTP_ALLOWED_ADDRESSES": "",
        }.items():
            monkeypatch.setenv(name, value)
        async with session_factory() as db:
            db.add(
                Channel(
                    id=str(uuid.uuid4()),
                    name="Mail",
                    type="email",
                    org_id=seed["org_a"],
                    created_by=seed["alice"],
                    config={"to": "ops@a.test"},
                    is_active=True,
                )
            )
            await db.commit()

        server = MagicMock()
        with patch("fim_one.core.channels.email.smtplib.SMTP_SSL") as secure:
            secure.return_value.__enter__ = MagicMock(return_value=server)
            secure.return_value.__exit__ = MagicMock(return_value=False)
            result = await _tool(session_factory, seed["alice"]).run(
                channel="Mail", title="Build failed\nBcc: x@evil.test\r\n", body="details"
            )
        assert result == "Notification sent to channel 'Mail'."
        message = email_lib.message_from_string(server.sendmail.call_args.args[2])
        assert message["Subject"] == "Build failed Bcc: x@evil.test"
        assert message["Bcc"] is None
        assert server.sendmail.call_args.args[1] == ["ops@a.test"]

    @pytest.mark.asyncio
    async def test_a_webhook_channel_gets_the_title_on_one_line(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        patcher, calls = _webhook_send()
        with patcher:
            await _tool(session_factory, seed["alice"]).run(
                channel="Ops", title=" Build\n failed ", body="b"
            )
        assert calls[0][1]["blocks"][0]["text"]["text"] == "Build failed"

    @pytest.mark.asyncio
    async def test_a_title_of_only_line_breaks_is_missing(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        result = await _tool(session_factory, seed["alice"]).run(
            channel="Ops", title="\n\r\n", body="b"
        )
        assert result == "[Error] 'title' is required."


class TestInstanceChannels:
    """The webhooks from environment variables, kept for one release."""

    @pytest.mark.asyncio
    async def test_env_webhook_is_selectable_as_an_instance_channel(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", SLACK_ENV)
        tool = _tool(session_factory, seed["alice"])
        patcher, calls = _webhook_send()
        with patcher, caplog.at_level(logging.WARNING):
            listing = await tool.run(title="t", body="b")
            result = await tool.run(channel="instance:slack", title="t", body="b")
        assert "- instance:slack (slack_webhook, instance-wide, deprecated)" in listing
        assert result == "Notification sent to channel 'instance:slack'."
        assert [url for url, _ in calls] == [SLACK_ENV]
        assert any("deprecated environment variable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_env_webhook_is_never_the_default_target(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Naming an organization channel does not reach the instance webhook."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", SLACK_ENV)
        patcher, calls = _webhook_send()
        with patcher:
            await _tool(session_factory, seed["alice"]).run(channel="Ops", title="t", body="b")
        assert [url for url, _ in calls] == [SLACK_A]

    @pytest.mark.asyncio
    async def test_unset_env_webhook_is_not_offered(
        self, session_factory: async_sessionmaker[AsyncSession], seed: dict[str, str]
    ) -> None:
        result = await _tool(session_factory, seed["alice"]).run(
            channel="instance:slack", title="t", body="b"
        )
        assert result.startswith("[Error] No channel named 'instance:slack'")

    @pytest.mark.asyncio
    async def test_the_old_provider_argument_maps_to_the_instance_channel(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        seed: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", SLACK_ENV)
        tool = _tool(session_factory, None)
        patcher, calls = _webhook_send()
        with patcher:
            sent = await tool.run(provider="slack", title="t", body="b")
            mail = await tool.run(provider="email", title="t", body="b", channel="")
        assert sent == "Notification sent to channel 'instance:slack'."
        assert [url for url, _ in calls] == [SLACK_ENV]
        assert mail.startswith("[Error] send_notification no longer takes an email address")


class TestBinding:
    """A run's tool acts for the run's acting user."""

    def test_the_tool_is_rebound_to_the_user(self) -> None:
        from fim_one.web.services.agent_runtime import bind_send_notification

        registry = ToolRegistry()
        registry.register(SendNotificationTool())
        bound = bind_send_notification(registry, "user-1").get("send_notification")
        assert isinstance(bound, SendNotificationTool)
        assert bound._user_id == "user-1"
        # The shared registry's own instance is left as it was.
        assert registry.get("send_notification")._user_id is None  # type: ignore[union-attr]

    def test_a_registry_without_the_tool_is_left_alone(self) -> None:
        from fim_one.web.services.agent_runtime import bind_send_notification

        registry = ToolRegistry()
        assert bind_send_notification(registry, "user-1") is registry

    @pytest.mark.asyncio
    async def test_chat_tools_are_bound_to_the_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.web.api.chat import _resolve_tools

        monkeypatch.setenv("NOTIFICATION_TOOL_ENABLED", "true")
        monkeypatch.setattr("fim_one.web.deps._cached_registry", None)
        monkeypatch.setattr("fim_one.web.deps._cached_registry_version", -1)
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(
            "fim_one.db.create_session", async_sessionmaker(engine, expire_on_commit=False)
        )
        try:
            # Through an agent owned by someone else, the caller is still the user.
            agent = {"agent_id": "a1", "owner_user_id": "owner-9", "tool_categories": ["general"]}
            for agent_cfg in (None, agent):
                tools = await _resolve_tools(agent_cfg, None, user_id="caller-1")
                tool = tools.get("send_notification")
                assert isinstance(tool, SendNotificationTool)
                assert tool._user_id == "caller-1"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_a_workflow_step_with_no_agent_has_no_notification_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.assembly.principal import CallContext, provisional_principal
        from fim_one.web.services import agent_runtime

        registry = ToolRegistry()
        registry.register(SendNotificationTool())
        monkeypatch.setattr("fim_one.web.deps.get_tools", lambda: registry)
        monkeypatch.setattr(
            "fim_one.web.deps.get_effective_fast_llm", AsyncMock(return_value=SimpleNamespace())
        )
        monkeypatch.setattr(agent_runtime, "_disabled_builtin_tools", AsyncMock(return_value=[]))
        monkeypatch.setattr(agent_runtime, "build_hooks", AsyncMock(return_value=None))

        class _Session:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: object) -> None:
                return None

        monkeypatch.setattr("fim_one.db.create_session", lambda: _Session())
        ctx = CallContext(
            principal=provisional_principal("runner-7"), binding=None, entry="workflow_node"
        )
        runtime = await agent_runtime.bare_runtime(ctx)
        # A run nobody may be watching does not message people: the step
        # selects an agent that has the tool instead.
        assert "send_notification" not in runtime.tools


# ---------------------------------------------------------------------------
# Exposure: the operator opts in to offering the tool
# ---------------------------------------------------------------------------


class TestNotificationExposure:
    def _discovered_names(self) -> set[str]:
        from fim_one.core.tool.builtin import discover_builtin_tools

        return {t.name for t in discover_builtin_tools()}

    def test_tool_not_offered_by_default(self) -> None:
        with patch.dict(os.environ, {"LARK_WEBHOOK_URL": "https://open.feishu.cn/x"}):
            os.environ.pop("NOTIFICATION_TOOL_ENABLED", None)
            assert "send_notification" not in self._discovered_names()

    def test_tool_offered_when_operator_opts_in(self):
        with patch.dict(os.environ, {"NOTIFICATION_TOOL_ENABLED": "true"}):
            assert "send_notification" in self._discovered_names()

    def test_test_endpoint_requires_admin(self):
        import inspect

        from fim_one.web.api.notifications import test_notification
        from fim_one.web.auth import get_current_admin

        dep = inspect.signature(test_notification).parameters["user"].default
        assert dep.dependency is get_current_admin


_SMTP_ENV = {"SMTP_HOST": "smtp.x.io", "SMTP_USER": "u@x.io", "SMTP_PASS": "p"}


class TestEmailToolExposure:

    def _discovered_names(self, env: dict[str, str]) -> set[str]:
        from fim_one.core.tool.builtin import discover_builtin_tools

        with patch.dict(os.environ, env):
            for k in ("SMTP_ALLOWED_DOMAINS", "SMTP_ALLOWED_ADDRESSES", "EMAIL_TOOL_ENABLED"):
                if k not in env:
                    os.environ.pop(k, None)
            return {t.name for t in discover_builtin_tools()}

    def test_not_offered_without_allowlist(self):
        assert "email_send" not in self._discovered_names(dict(_SMTP_ENV))

    def test_offered_with_domain_allowlist(self):
        env = {**_SMTP_ENV, "SMTP_ALLOWED_DOMAINS": "corp.internal"}
        assert "email_send" in self._discovered_names(env)

    def test_offered_with_address_allowlist(self):
        env = {**_SMTP_ENV, "SMTP_ALLOWED_ADDRESSES": "ops@corp.internal"}
        assert "email_send" in self._discovered_names(env)

    def test_offered_when_operator_opts_in(self):
        env = {**_SMTP_ENV, "EMAIL_TOOL_ENABLED": "true"}
        assert "email_send" in self._discovered_names(env)
