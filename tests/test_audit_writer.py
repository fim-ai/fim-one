"""The audit writer (``core/audit/writer.py``).

Two ways to write an event: in the caller's transaction, where the record
and the change succeed or fail together, and in a session of its own, where
a record that cannot be written never fails what it describes.
"""

from __future__ import annotations

import ast
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one
import fim_one.db.models  # noqa: F401
from fim_one.core.audit import actions
from fim_one.core.audit import writer as audit
from fim_one.core.audit.events import Actor, RequestMeta
from fim_one.db.base import Base
from fim_one.db.models import AuditLog, LoginHistory, User
from fim_one.db.models.review_log import ReviewLog

SRC = Path(fim_one.__file__).parent
ALICE = Actor(user_id="u-alice", label="alice", kind="user")


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # A file, not ``:memory:``: the writer opens a session of its own.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _create_session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    with patch("fim_one.db.create_session", _create_session):
        yield maker
    await engine.dispose()


async def _audit_rows(factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with factory() as s:
        return list((await s.execute(select(AuditLog))).scalars().all())


class TestInTheCallersTransaction:
    async def test_the_record_is_committed_with_the_change(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as db:
            db.add(User(id="u-bob", email="bob@x.io", username="bob"))
            await audit.record(
                "user.create",
                actor=ALICE,
                target_type="user",
                target_id="u-bob",
                target_label="bob",
                detail="created",
                db=db,
            )
            assert await _audit_rows(factory) == []  # not before the caller commits
            await db.commit()

        (row,) = await _audit_rows(factory)
        assert (row.actor_id, row.actor_label, row.action) == ("u-alice", "alice", "user.create")
        assert (row.target_type, row.target_id, row.target_label) == ("user", "u-bob", "bob")
        assert row.detail == "created"

    async def test_a_rolled_back_change_leaves_no_record(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as db:
            db.add(User(id="u-bob", email="bob@x.io", username="bob"))
            await audit.record("user.create", actor=ALICE, target_id="u-bob", db=db)
            await db.rollback()

        assert await _audit_rows(factory) == []
        async with factory() as db:
            assert await db.get(User, "u-bob") is None

    async def test_a_record_that_cannot_be_written_fails_the_change(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A permission change with no record is the worse outcome."""
        async with factory() as db:
            db.add(User(id="u-bob", email="bob@x.io", username="bob"))
            # A value the JSON column cannot store.
            await audit.record(
                "user.create", actor=ALICE, changes={"role": {"to": object()}}, db=db
            )
            with pytest.raises(Exception, match="not JSON serializable"):
                await db.commit()

        async with factory() as db:
            assert await db.get(User, "u-bob") is None


class TestWhatARowCarries:
    async def test_actor_organization_outcome_changes_and_context(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        from fim_one.core.assembly.principal import CallContext, provisional_principal

        context = CallContext(
            principal=provisional_principal("u-alice"),
            binding=None,
            entry="chat",
            conversation_id="conv-1",
            agent_id="agent-1",
            workflow_run_id="run-1",
        )
        await audit.record(
            "user.set_quota",
            actor=Actor("u-alice", "alice", "admin", auth="api_key", credential_ref="key-1"),
            org_id="org-1",
            target_type="user",
            target_id="u-bob",
            outcome="denied",
            changes={"quota": {"from": 1, "to": 2}},
            context=context,
            request=RequestMeta(ip="203.0.113.7", user_agent="curl/8", request_id="req-1"),
        )

        (row,) = await _audit_rows(factory)
        assert (row.actor_kind, row.auth_method, row.credential_ref) == ("admin", "api_key", "key-1")
        assert (row.org_id, row.outcome) == ("org-1", "denied")
        assert row.changes == {"quota": {"from": 1, "to": 2}}
        assert (row.ip_address, row.user_agent, row.request_id) == ("203.0.113.7", "curl/8", "req-1")
        assert (row.conversation_id, row.agent_id, row.workflow_run_id) == (
            "conv-1",
            "agent-1",
            "run-1",
        )

    async def test_the_defaults(self, factory: async_sessionmaker[AsyncSession]) -> None:
        await audit.record("user.create", actor=ALICE)

        (row,) = await _audit_rows(factory)
        assert (row.outcome, row.org_id, row.changes, row.actor_kind) == (
            "success",
            None,
            None,
            "user",
        )
        # Not an HTTP request: no address.
        assert (row.ip_address, row.user_agent, row.request_id) == (None, None, None)

    async def test_the_system_and_an_anonymous_caller_are_named_by_their_kind(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await audit.record("user.create", actor=Actor(None, None, "anonymous"))
        await audit.record("user.delete", actor=audit.system_actor("retention sweep"))

        rows = {row.action: row for row in await _audit_rows(factory)}
        assert (rows["user.create"].actor_id, rows["user.create"].actor_label) == (
            None,
            "anonymous",
        )
        assert (rows["user.delete"].actor_label, rows["user.delete"].actor_kind) == (
            "retention sweep",
            "system",
        )

    async def test_over_long_values_are_cut_to_their_columns(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """PostgreSQL rejects an over-long value, and the record with it: an
        email longer than 50 characters as the operator's name used to."""
        long_email = "a" * 300 + "@example.test"
        await audit.record(
            "user.create",
            actor=Actor("u-1", long_email, "admin"),
            target_type="t" * 80,
            target_id="i" * 80,
            target_label="l" * 400,
            request=RequestMeta(ip="1" * 80, user_agent="u" * 900, request_id="r" * 80),
        )

        (row,) = await _audit_rows(factory)
        assert len(row.actor_label) == 255
        assert (len(row.target_type or ""), len(row.target_id or "")) == (50, 36)
        assert len(row.target_label or "") == 255
        assert (len(row.ip_address or ""), len(row.user_agent or "")) == (45, 500)
        assert len(row.request_id or "") == 36


class TestInASessionOfItsOwn:
    async def test_the_record_is_written_at_once(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await audit.record("export.users", actor=ALICE, detail="3 users")

        (row,) = await _audit_rows(factory)
        assert (row.action, row.actor_id, row.detail) == ("export.users", "u-alice", "3 users")

    async def test_it_survives_the_callers_rollback(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as db:
            await audit.record("export.users", actor=ALICE)
            await db.rollback()

        assert [row.action for row in await _audit_rows(factory)] == ["export.users"]

    async def test_a_failure_is_logged_and_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _broken() -> AsyncSession:
            raise RuntimeError("db down: INSERT ... 'SECRET'")

        with (
            patch("fim_one.db.create_session", _broken),
            caplog.at_level("ERROR", logger="fim_one.core.audit.writer"),
        ):
            await audit.record("export.users", actor=ALICE, detail="SECRET")

        (error,) = [r for r in caplog.records if r.levelname == "ERROR"]
        assert error.getMessage() == "Failed to record audit event export.users: RuntimeError"
        assert "SECRET" not in caplog.text


class TestActionNames:
    async def test_an_unregistered_name_is_an_error_under_test(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        with pytest.raises(ValueError, match="not registered"):
            await audit.record("user.frobnicate", actor=ALICE)
        assert await _audit_rows(factory) == []

    async def test_in_production_it_is_written_and_reported(
        self,
        factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("PYTEST_CURRENT_TEST")
        with caplog.at_level("WARNING", logger="fim_one.core.audit.writer"):
            await audit.record("user.frobnicate", actor=ALICE)

        assert [row.action for row in await _audit_rows(factory)] == ["user.frobnicate"]
        assert "'user.frobnicate' is not registered" in caplog.text

    def test_names_are_domain_dot_verb_and_fit_the_column(self) -> None:
        for name in actions.ACTIONS:
            domain, _, verb = name.partition(".")
            assert domain and verb and "." not in verb, name
            assert name == name.lower() and len(name) <= 100, name

    def test_every_name_the_code_writes_is_registered(self) -> None:
        """Each literal action handed to ``write_audit`` or ``record``."""
        written = set(_written_action_literals())
        assert len(written) > 80
        assert written - actions.ACTIONS == set()


def _literals(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


def _written_action_literals() -> Iterator[str]:
    """Action names in the source: literal arguments of the two write
    functions, and literals assigned to a variable that is then passed."""
    for path in sorted(SRC.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("action", "audit_action") for t in node.targets
            ):
                yield from (lit for lit in _literals(node.value) if "." in lit)
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name == "write_audit":
                if len(node.args) >= 3:
                    yield from _literals(node.args[2])
                for keyword in node.keywords:
                    if keyword.arg == "action":
                        yield from _literals(keyword.value)
            elif name == "record" and isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id == "audit" and node.args:
                    yield from _literals(node.args[0])


class TestActors:
    def test_a_users_row(self) -> None:
        admin = SimpleNamespace(id="u-1", username="root", email="root@x.io", is_admin=True)
        member = SimpleNamespace(id="u-2", username=None, email="m@x.io", is_admin=False)

        assert audit.actor_of(admin) == Actor(user_id="u-1", label="root", kind="admin")
        # No username: the email is the label.
        assert audit.actor_of(member) == Actor(user_id="u-2", label="m@x.io", kind="user")

    def test_a_principal(self) -> None:
        from fim_one.core.assembly.principal import AuthMethod, provisional_principal

        principal = provisional_principal("u-1", auth=AuthMethod.API_KEY)
        actor = audit.actor_from_principal(principal, label="alice", credential_ref="key-9")

        assert actor == Actor(
            user_id="u-1", label="alice", kind="user", auth="api_key", credential_ref="key-9"
        )

    def test_the_system(self) -> None:
        actor = audit.system_actor("retention sweep")
        assert (actor.user_id, actor.label, actor.kind) == (None, "retention sweep", "system")


class TestSignInAttempts:
    async def test_with_the_callers_session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as db:
            await audit.record_login(
                user_id="u-1",
                username="alice",
                email="a@x.io",
                success=False,
                failure_reason="wrong_password",
                request=RequestMeta(ip="203.0.113.7", user_agent="curl/8", request_id=None),
                db=db,
            )
            await db.commit()

        async with factory() as s:
            (row,) = (await s.execute(select(LoginHistory))).scalars().all()
        assert (row.user_id, row.username, row.email) == ("u-1", "alice", "a@x.io")
        assert (row.ip_address, row.user_agent) == ("203.0.113.7", "curl/8")
        assert (row.success, row.failure_reason, row.method) == (False, "wrong_password", None)

    async def test_in_a_session_of_its_own(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await audit.record_login(
            user_id=None, username=None, email="x@x.io", success=False, method="oauth:github"
        )

        async with factory() as s:
            (row,) = (await s.execute(select(LoginHistory))).scalars().all()
        assert (row.email, row.success, row.ip_address) == ("x@x.io", False, None)
        assert row.method == "oauth:github"


class TestReviewTrail:
    async def test_an_entry_joins_the_callers_transaction(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with factory() as db:
            await audit.record_review(
                db,
                org_id="org-1",
                resource_type="agent",
                resource_id="a-1",
                resource_name="Helper",
                action="approved",
                actor=ALICE,
                note="ok",
            )
            await db.commit()

        async with factory() as s:
            (row,) = (await s.execute(select(ReviewLog))).scalars().all()
        assert (row.org_id, row.resource_type, row.resource_id) == ("org-1", "agent", "a-1")
        assert (row.action, row.actor_id, row.actor_username, row.note) == (
            "approved",
            "u-alice",
            "alice",
            "ok",
        )


class TestTheExistingWritersGoThroughIt:
    """``write_audit``, ``log_review_event``, ``_record_login``: same rows as before."""

    async def test_write_audit(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api.admin_utils import write_audit

        admin = User(id="u-root", email="root@x.io", username=None, is_admin=True)
        async with factory() as db:
            await write_audit(db, admin, "user.disable", "user", "u-2", "bob", "why")

        (row,) = await _audit_rows(factory)  # committed by write_audit itself
        assert (row.actor_id, row.actor_label) == ("u-root", "root@x.io")
        assert (row.action, row.target_type, row.target_id, row.target_label, row.detail) == (
            "user.disable",
            "user",
            "u-2",
            "bob",
            "why",
        )

    async def test_log_review_event(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api.reviews import log_review_event

        reviewer = User(id="u-rev", email="rev@x.io", username=None)
        async with factory() as db:
            await log_review_event(db, "org-1", "skill", "s-1", "Summarise", "rejected", reviewer)
            await log_review_event(db, "org-1", "skill", "s-1", "Summarise", "submitted")
            await db.commit()

        async with factory() as s:
            rows = (await s.execute(select(ReviewLog).order_by(ReviewLog.action))).scalars().all()
        # The review trail names the actor by username alone: none here.
        assert [(r.action, r.actor_id, r.actor_username) for r in rows] == [
            ("rejected", "u-rev", None),
            ("submitted", None, None),
        ]

    async def test_record_login(self, factory: async_sessionmaker[AsyncSession]) -> None:
        from fim_one.web.api.auth import _record_login

        request = SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.4"), headers={"User-Agent": "Firefox"}
        )
        user = User(id="u-1", email="a@x.io", username="alice")
        async with factory() as db:
            await _record_login(db, request, user, method="password", success=True)  # type: ignore[arg-type]
            await _record_login(  # type: ignore[arg-type]
                db,
                request,
                None,
                method="password",
                success=False,
                failure_reason="user_not_found",
                email="z@x.io",
            )

        async with factory() as s:
            rows = (await s.execute(select(LoginHistory).order_by(LoginHistory.email))).scalars()
            got = [
                (r.user_id, r.username, r.email, r.ip_address, r.user_agent, r.success)
                for r in rows
            ]
        assert got == [
            ("u-1", "alice", "a@x.io", "198.51.100.4", "Firefox", True),
            (None, None, "z@x.io", "198.51.100.4", "Firefox", False),
        ]
