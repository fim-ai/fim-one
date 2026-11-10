"""A user's plan changing without an administrator leaves a record.

The payment processor reporting a purchase or a switch, and a cancelled
subscription reaching the end of its period, changed ``users.plan_id``
with nothing but a log line. Each now leaves an audit event: the system as
actor, the user as target, the plan before and after.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from fim_one.db.models import BillingPlan, Subscription, SystemSetting, User
from tests.audit.harness import World, world  # noqa: F401


@pytest.fixture(autouse=True)
def _stripe_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_audit")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_audit")
    from fim_one.web.config import settings as cfg
    from fim_one.web.services import stripe_client

    cfg.reset()
    stripe_client.reset_for_testing()
    yield
    cfg.reset()
    stripe_client.reset_for_testing()


def _ts(moment: datetime) -> int:
    return int(moment.timestamp())


async def _billing(world: World) -> tuple[User, BillingPlan, BillingPlan]:
    """Billing on, a Free and a Pro plan, and a user on Free."""
    free = BillingPlan(slug="free", name="Free", monthly_token_quota=100_000)
    pro = BillingPlan(
        slug="pro", name="Pro", stripe_price_id="price_pro", monthly_token_quota=5_000_000
    )
    await world.add(free, pro, SystemSetting(key="access_model", value="freemium"))
    await world.add(SystemSetting(key="default_plan_id", value=str(free.id)))
    alice = await world.user("alice", plan_id=free.id)
    return alice, free, pro


async def _deliver(world: World, event: dict[str, Any], subscription: dict[str, Any]) -> None:
    import stripe

    with (
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("stripe.Subscription.retrieve", return_value=subscription),
    ):
        await world.call(
            "POST",
            "/api/webhooks/stripe",
            headers={"stripe-signature": "valid"},
            content=json.dumps(event).encode(),
        )


def _subscription(price_id: str, **fields: Any) -> dict[str, Any]:
    end = datetime.now(UTC) + timedelta(days=30)
    return {
        "id": "sub_1",
        "status": "active",
        "current_period_start": _ts(end - timedelta(days=30)),
        "current_period_end": _ts(end),
        "cancel_at_period_end": False,
        "canceled_at": None,
        "items": {"data": [{"price": {"id": price_id}}]},
        **fields,
    }


class TestThePaymentProcessor:
    async def test_plan_change_audited(self, world: World) -> None:
        alice, free, pro = await _billing(world)
        event = {
            "id": "evt_checkout_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": alice.id,
                    "customer": "cus_alice",
                    "subscription": "sub_1",
                }
            },
        }

        await _deliver(world, event, _subscription("price_pro"))

        # The processor's servers called, not the user: the system is the actor.
        row = await world.one(
            "billing.plan_change",
            actor=None,
            target=("user", alice.id),
            changes={"plan_id": {"from": free.id, "to": pro.id}},
        )
        assert (row.actor_kind, row.actor_id, row.actor_label) == (
            "system",
            None,
            "stripe webhook",
        )
        assert (row.target_label, row.detail) == ("alice", "checkout completed; plan=pro")
        assert (await world.get(User, alice.id)).plan_id == pro.id

    async def test_the_same_event_delivered_twice_is_one_record(self, world: World) -> None:
        alice, _free, _pro = await _billing(world)
        event = {
            "id": "evt_checkout_1",
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": alice.id, "subscription": "sub_1"}},
        }

        await _deliver(world, event, _subscription("price_pro"))
        await _deliver(world, event, _subscription("price_pro"))

        assert len(await world.events("billing.plan_change")) == 1

    async def test_a_switch_of_plan_on_an_existing_subscription(self, world: World) -> None:
        alice, free, pro = await _billing(world)
        await world.add(
            Subscription(
                user_id=alice.id,
                plan_id=free.id,
                stripe_subscription_id="sub_1",
                stripe_price_id="price_old",
                status="active",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
                updated_at=datetime.now(UTC),
            )
        )
        event = {
            "id": "evt_updated_1",
            "type": "customer.subscription.updated",
            "data": {"object": _subscription("price_pro")},
        }

        await _deliver(world, event, _subscription("price_pro"))

        row = await world.one(
            "billing.plan_change",
            actor=None,
            target=("user", alice.id),
            changes={"plan_id": {"from": free.id, "to": pro.id}},
        )
        assert row.detail == "subscription updated; plan=pro"

    async def test_an_event_that_leaves_the_plan_as_it_is_records_nothing(
        self, world: World
    ) -> None:
        alice, _free, pro = await _billing(world)
        async with world.factory() as db:
            user = await db.get(User, alice.id)
            user.plan_id = pro.id
            await db.commit()
        event = {
            "id": "evt_checkout_2",
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": alice.id, "subscription": "sub_1"}},
        }

        await _deliver(world, event, _subscription("price_pro"))

        assert await world.events("billing.plan_change") == []


class TestThePeriodEndSweep:
    async def test_a_cancelled_subscription_past_its_period_puts_the_user_back_on_free(
        self, world: World
    ) -> None:
        from fim_one.web.services.subscription_lifecycle import (
            downgrade_expired_canceled_subscriptions,
        )

        alice, free, pro = await _billing(world)
        async with world.factory() as db:
            user = await db.get(User, alice.id)
            user.plan_id = pro.id
            db.add(
                Subscription(
                    user_id=alice.id,
                    plan_id=pro.id,
                    stripe_subscription_id="sub_1",
                    stripe_price_id="price_pro",
                    status="canceled",
                    current_period_start=datetime.now(UTC) - timedelta(days=40),
                    current_period_end=datetime.now(UTC) - timedelta(days=1),
                    updated_at=datetime.now(UTC),
                )
            )
            await db.commit()

        async with world.factory() as db:
            assert await downgrade_expired_canceled_subscriptions(db) == 1
        async with world.factory() as db:
            assert await downgrade_expired_canceled_subscriptions(db) == 0  # nothing left

        # A background job: no address.
        row = await world.one(
            "billing.plan_change",
            actor=None,
            target=("user", alice.id),
            changes={"plan_id": {"from": pro.id, "to": free.id}},
            ip=None,
        )
        assert (row.actor_kind, row.actor_label) == ("system", "subscription lifecycle")
        assert row.detail == (
            "canceled subscription reached its period end; access model freemium"
        )
        assert (await world.get(User, alice.id)).plan_id == free.id
