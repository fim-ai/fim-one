"""How many rounds a plan (DAG) turn runs when the goal is never reached.

``DAG_MAX_REPLAN_ROUNDS`` counts rounds in total: the first plan plus N-1
autonomous replans. The turn is driven through the real ``/api/dag`` endpoint
with a scripted model whose analyzer verdict is always "not achieved".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from fim_one.web import create_app

from . import test_chat_turn_assembly as harness
from .test_chat_turn_assembly import (  # noqa: F401  (pytest fixtures)
    World,
    db_env,
    loops,
    world,
)

pytestmark = pytest.mark.timeout(120)


@pytest.fixture()
def app() -> FastAPI:
    return create_app()


@pytest.fixture()
def never_achieved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The analyzer says the goal was missed and can still be reached."""
    monkeypatch.setitem(harness._EVERY_ANSWER, "achieved", False)
    monkeypatch.setitem(harness._EVERY_ANSWER, "confidence", 0.9)


def _rounds(body: str) -> tuple[list[int], int]:
    """Planning rounds started, and autonomous replans announced."""
    planning: list[int] = []
    replans = 0
    for name, data in harness._payloads(body):
        if name != "phase" or not isinstance(data, dict):
            continue
        if data.get("name") == "planning" and data.get("status") == "start":
            planning.append(data["round"])
        elif data.get("name") == "replanning" and data.get("status") == "start":
            replans += 1
    return planning, replans


class TestRoundsWhenTheGoalIsNeverReached:
    @pytest.mark.parametrize("configured", [1, 2, 3])
    async def test_n_rounds_in_total_means_the_first_plan_plus_n_minus_one_replans(
        self,
        app: FastAPI,
        world: World,  # noqa: F811
        loops: list[dict[str, Any]],  # noqa: F811
        never_achieved: None,
        monkeypatch: pytest.MonkeyPatch,
        configured: int,
    ) -> None:
        monkeypatch.setenv("DAG_MAX_REPLAN_ROUNDS", str(configured))

        body = await harness._turn(app, "/api/dag", world, world.agent)

        names = [name for name, _ in harness._events(body)]
        assert "done" in names and "error" not in names, body[-2000:]
        planning, replans = _rounds(body)
        assert planning == list(range(1, configured + 1))
        assert replans == configured - 1

    async def test_the_default_is_three_rounds(
        self,
        app: FastAPI,
        world: World,  # noqa: F811
        loops: list[dict[str, Any]],  # noqa: F811
        never_achieved: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DAG_MAX_REPLAN_ROUNDS", raising=False)

        body = await harness._turn(app, "/api/dag", world, world.agent)

        planning, replans = _rounds(body)
        assert planning == [1, 2, 3]
        assert replans == 2

    async def test_a_goal_reached_in_the_first_round_is_not_replanned(
        self,
        app: FastAPI,
        world: World,  # noqa: F811
        loops: list[dict[str, Any]],  # noqa: F811
    ) -> None:
        body = await harness._turn(app, "/api/dag", world, world.agent)

        planning, replans = _rounds(body)
        assert planning == [1]
        assert replans == 0
