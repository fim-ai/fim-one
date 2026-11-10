"""A decision on an approval request leaves a record that outlives the request.

Who approved or rejected a tool call or a workflow approval step was kept
only on the request row, and that row goes when its channel or its
organization is deleted (and, for a workflow approval, was closed with its
run). The decision is now also an audit event, written in the transaction
of the decision, from every surface that decides. It names the tool or the
step, never the arguments of the call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete

from fim_one.core.assembly.confirmation import ConfirmationService
from fim_one.core.assembly.errors import AssemblyRefusal
from fim_one.core.assembly.principal import AuthMethod, resolve_principal
from fim_one.core.channels.expiry import ConfirmationRequestExpirer
from fim_one.db.models.channel import Channel, ConfirmationRequest
from fim_one.db.models.organization import Organization
from tests.audit.harness import World, world  # noqa: F401

ARGUMENT = "customer-C-20240892-CONFIDENTIAL"


def _request(initiator_id: str, **fields: Any) -> ConfirmationRequest:
    values: dict[str, Any] = {
        "id": "cnf-1",
        "kind": "confirmation",
        "mode": "inline",
        "user_id": initiator_id,
        "agent_id": "agent-1",
        "conversation_id": "conv-1",
        "payload": {"tool_name": "delete_customer", "tool_args": {"customer_id": ARGUMENT}},
        "eligible_scope": "initiator",
        "eligible_user_id": initiator_id,
        "title": f"Delete {ARGUMENT}?",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(fields)
    return ConfirmationRequest(**values)


class TestDecidingInThePortal:
    @pytest.mark.parametrize(
        ("decision", "action"), [("approve", "confirmation.approve"), ("reject", "confirmation.reject")]
    )
    async def test_a_tool_call(self, world: World, decision: str, action: str) -> None:
        alice = await world.user("alice")
        await world.add(_request(alice.id))

        await world.call(
            "POST",
            "/api/confirmations/cnf-1/respond",
            by=alice,
            json={"decision": decision, "reason": f"because of {ARGUMENT}"},
        )

        row = await world.one(action, actor=alice, target=("confirmation", "cnf-1"))
        # Which tool, which kind of request, where it was decided.
        assert (row.target_label, row.detail) == ("delete_customer", "kind=confirmation; via=portal")
        assert (row.conversation_id, row.agent_id, row.workflow_run_id) == (
            "conv-1",
            "agent-1",
            None,
        )
        assert row.auth_method == "session"
        # Never the arguments, the request's title or the approver's note.
        world.assert_absent(row, ARGUMENT)

    async def test_a_workflow_approval_step_belongs_to_the_organization(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        await world.add(
            _request(
                alice.id,
                kind="workflow_approval",
                org_id=acme.id,
                agent_id=None,
                conversation_id=None,
                workflow_run_id="run-7",
                node_id="human_1",
                payload={"message": f"Pay {ARGUMENT}?"},
                eligible_scope="assignee",
                eligible_user_id=bob.id,
            )
        )

        await world.call(
            "POST", "/api/confirmations/cnf-1/respond", by=bob, json={"decision": "approve"}
        )

        row = await world.one(
            "confirmation.approve", actor=bob, org=acme, target=("confirmation", "cnf-1")
        )
        assert (row.target_label, row.detail) == (
            "human_1",
            "kind=workflow_approval; via=portal",
        )
        assert row.workflow_run_id == "run-7"
        world.assert_absent(row, ARGUMENT)

    async def test_someone_who_may_not_decide_changes_and_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        mallory = await world.user("mallory")
        await world.add(_request(alice.id))

        await world.call(
            "POST",
            "/api/confirmations/cnf-1/respond",
            by=mallory,
            json={"decision": "approve"},
            expect=403,
        )

        assert await world.events("confirmation.approve") == []
        assert (await world.get(ConfirmationRequest, "cnf-1")).status == "pending"

    async def test_a_second_decision_is_refused_and_there_is_one_record(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await world.add(_request(alice.id))

        await world.call(
            "POST", "/api/confirmations/cnf-1/respond", by=alice, json={"decision": "approve"}
        )
        await world.call(
            "POST",
            "/api/confirmations/cnf-1/respond",
            by=alice,
            json={"decision": "reject"},
            expect=409,
        )

        assert [row.action for row in await world.events()] == ["confirmation.approve"]

    async def test_an_answer_to_the_agents_question_is_not_a_decision(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(
            _request(alice.id, kind="user_question", payload={"questions": [{"q": "Which?"}]})
        )

        await world.call(
            "POST",
            "/api/confirmations/cnf-1/respond",
            by=alice,
            json={"decision": "approve"},
            expect=404,
        )

        assert await world.events() == []


class TestDecidingThroughAChannel:
    async def test_a_click_on_a_channels_card_is_recorded_as_such(self, world: World) -> None:
        """The channel callback hands the service the linked account as a
        principal authenticated by the channel; this drives the service the
        way the callback does."""
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        await world.add(
            Channel(id="chan-1", name="approvals", type="feishu", org_id=acme.id, config={}, created_by=alice.id)
        )
        await world.add(
            _request(alice.id, org_id=acme.id, channel_id="chan-1", mode="channel")
        )

        async with world.factory() as db:
            actor = await resolve_principal(db, user_id=alice.id, auth=AuthMethod.CHANNEL)
            await ConfirmationService(world.factory).decide(
                db,
                confirmation_id="cnf-1",
                actor=actor,
                decision="reject",
                via_channel_id="chan-1",
                external_actor="ou_feishu_open_id_of_alice",
            )

        # Not an HTTP request of the user's: no address of theirs to record.
        row = await world.one(
            "confirmation.reject", actor=alice, org=acme, target=("confirmation", "cnf-1"), ip=None
        )
        assert (row.detail, row.auth_method) == ("kind=confirmation; via=channel", "channel")
        # The outside system's id of the person stays on the request row.
        world.assert_absent(row, "ou_feishu_open_id_of_alice", ARGUMENT)

    async def test_a_decision_refused_because_the_request_went_elsewhere_records_nothing(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await world.add(_request(alice.id))

        async with world.factory() as db:
            actor = await resolve_principal(db, user_id=alice.id, auth=AuthMethod.CHANNEL)
            with pytest.raises(AssemblyRefusal):
                await ConfirmationService(world.factory).decide(
                    db,
                    confirmation_id="cnf-1",
                    actor=actor,
                    decision="approve",
                    via_channel_id="some-other-channel",
                )

        assert await world.events() == []


class TestTheRecordOutlivesTheRequest:
    async def test_deleting_the_organization_takes_the_request_and_leaves_the_record(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        await world.add(_request(alice.id, org_id=acme.id))
        await world.call(
            "POST", "/api/confirmations/cnf-1/respond", by=alice, json={"decision": "approve"}
        )

        async with world.factory() as db:
            await db.execute(delete(ConfirmationRequest))  # as the cascade does
            await db.execute(delete(Organization).where(Organization.id == acme.id))
            await db.commit()

        assert await world.get(ConfirmationRequest, "cnf-1") is None
        row = await world.one(
            "confirmation.approve", actor=alice, org=acme, target=("confirmation", "cnf-1")
        )
        assert row.target_label == "delete_customer"


class TestNobodyDecidedInTime:
    async def test_the_sweep_records_each_request_it_closes(self, world: World) -> None:
        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        past = datetime.now(UTC) - timedelta(minutes=1)
        await world.add(
            _request(alice.id, id="cnf-late", org_id=acme.id, expires_at=past),
            _request(alice.id, id="cnf-open"),
            _request(alice.id, id="cnf-question", kind="user_question", expires_at=past),
        )

        async with world.factory() as db:
            assert await ConfirmationRequestExpirer().sweep(db) == 2
        async with world.factory() as db:
            assert await ConfirmationRequestExpirer().sweep(db) == 0  # nothing left to close

        # The system is the actor; an unanswered question is not a decision.
        row = await world.one(
            "confirmation.expire",
            actor=None,
            org=acme,
            target=("confirmation", "cnf-late"),
            ip=None,
        )
        assert (row.actor_kind, row.actor_label) == ("system", "confirmation expiry")
        assert (row.target_label, row.detail) == ("delete_customer", "kind=confirmation; via=timeout")
        world.assert_absent(row, ARGUMENT)
        assert (await world.get(ConfirmationRequest, "cnf-open")).status == "pending"

    async def test_a_late_click_closes_the_request_and_records_that_it_expired(
        self, world: World
    ) -> None:
        alice = await world.user("alice")
        await world.add(_request(alice.id, expires_at=datetime.now(UTC) - timedelta(seconds=5)))

        await world.call(
            "POST",
            "/api/confirmations/cnf-1/respond",
            by=alice,
            json={"decision": "approve"},
            expect=409,
        )

        # Not approved by Alice: expired, by the system.
        assert [(r.action, r.actor_kind) for r in await world.events()] == [
            ("confirmation.expire", "system")
        ]
        assert (await world.get(ConfirmationRequest, "cnf-1")).status == "expired"

    async def test_a_wait_that_runs_out_records_it(self, world: World) -> None:
        alice = await world.user("alice")
        await world.add(_request(alice.id))

        await ConfirmationService(world.factory)._mark_expired("cnf-1")
        await ConfirmationService(world.factory)._mark_expired("cnf-1")  # already closed

        row = await world.one(
            "confirmation.expire", actor=None, target=("confirmation", "cnf-1"), ip=None
        )
        assert row.actor_kind == "system"


class TestAnApprovalPlaygroundCard:
    """An organization member can send a test card with any tool name on it
    and answer it. That read in the audit log like a real approval."""

    TOOL = "wire_transfer_to_account_CONFIDENTIAL"

    async def _channel(self, world: World) -> tuple[Any, Any, Any]:
        alice = await world.user("alice")
        bob = await world.user("bob")
        acme = await world.org("Acme", alice, member=bob)
        await world.add(
            Channel(
                id="chan-1", name="approvals", type="feishu", org_id=acme.id, config={},
                created_by=alice.id,
            )
        )
        return alice, bob, acme

    async def test_sending_one_is_recorded_as_a_test_of_the_channel(self, world: World) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from fim_one.core.channels import ApprovalCarrier

        _alice, bob, acme = await self._channel(world)
        carrier = MagicMock(spec=ApprovalCarrier)
        sent = _request(bob.id, id="cnf-test", payload={"tool_name": self.TOOL, "test_mode": True})

        with (
            patch("fim_one.web.api.channels.build_channel", return_value=carrier),
            patch(
                "fim_one.web.api.channels.ConfirmationService.request",
                AsyncMock(return_value=sent),
            ),
        ):
            await world.call(
                "POST",
                "/api/channels/chan-1/test-approval",
                by=bob,
                json={"tool_name": self.TOOL, "tool_args": {"amount": ARGUMENT}},
            )

        row = await world.one(
            "channel.test_approval", actor=bob, org=acme, target=("channel", "chan-1")
        )
        assert (row.target_label, row.detail) == ("approvals", "test card sent")
        world.assert_absent(row, self.TOOL, ARGUMENT)

    @pytest.mark.parametrize(("decision", "result"), [("approve", "approved"), ("reject", "rejected")])
    async def test_answering_one_is_not_an_approval(
        self, world: World, decision: str, result: str
    ) -> None:
        _alice, bob, acme = await self._channel(world)
        await world.add(
            _request(
                bob.id,
                org_id=acme.id,
                channel_id="chan-1",
                agent_id=None,
                payload={"tool_name": self.TOOL, "tool_args": {"a": ARGUMENT}, "test_mode": True},
            )
        )

        await world.call(
            "POST", "/api/confirmations/cnf-1/respond", by=bob, json={"decision": decision}
        )

        assert [r.action for r in await world.events()] == ["channel.test_approval"]
        row = await world.one(
            "channel.test_approval", actor=bob, org=acme, target=("channel", "chan-1")
        )
        assert row.detail == f"test card {result}; via=portal"
        world.assert_absent(row, self.TOOL, ARGUMENT)

    async def test_one_nobody_answers_is_not_an_expired_approval(self, world: World) -> None:
        _alice, bob, acme = await self._channel(world)
        await world.add(
            _request(
                bob.id,
                org_id=acme.id,
                channel_id="chan-1",
                payload={"tool_name": self.TOOL, "test_mode": True},
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )

        async with world.factory() as db:
            await ConfirmationRequestExpirer().sweep(db)

        (row,) = await world.events()
        assert (row.action, row.outcome, row.detail) == (
            "channel.test_approval",
            "failed",
            "test card expired; via=timeout",
        )


class TestSomeoneWhoMayNotDecide:
    """A forbidden decision and a card click from someone with no usable
    account were refused and left nothing."""

    async def test_a_forbidden_decision_in_the_portal_is_recorded_once(self, world: World) -> None:
        alice = await world.user("alice")
        mallory = await world.user("mallory")
        acme = await world.org("Acme", alice)
        await world.add(_request(alice.id, org_id=acme.id))

        for _ in range(3):
            await world.call(
                "POST",
                "/api/confirmations/cnf-1/respond",
                by=mallory,
                json={"decision": "approve"},
                expect=403,
            )

        row = await world.one(
            "confirmation.decision_denied",
            actor=mallory,
            org=acme,
            target=("confirmation", "cnf-1"),
            outcome="denied",
        )
        assert row.detail == "kind=confirmation; via=portal"
        world.assert_absent(row, ARGUMENT)

    async def test_a_card_click_from_an_account_that_is_not_linked(self, world: World) -> None:
        from fim_one.web.api.channels import _decide_from_click

        alice = await world.user("alice")
        acme = await world.org("Acme", alice)
        channel = Channel(
            id="chan-1", name="approvals", type="feishu", org_id=acme.id, config={},
            created_by=alice.id,
        )
        await world.add(channel)
        await world.add(_request(alice.id, org_id=acme.id, channel_id="chan-1", mode="channel"))

        async with world.factory() as db:
            await _decide_from_click(
                db,
                channel=channel,
                confirmation_id="cnf-1",
                decision="approve",
                open_id="ou_stranger_open_id",
            )

        (row,) = await world.events("confirmation.decision_denied")
        assert (row.actor_kind, row.actor_id, row.org_id, row.outcome) == (
            "external",
            None,
            acme.id,
            "denied",
        )
        assert (row.target_id, row.detail) == (
            "cnf-1",
            "card click; account is not linked; via=channel",
        )
        world.assert_absent(row, "ou_stranger_open_id")
        assert (await world.get(ConfirmationRequest, "cnf-1")).status == "pending"
