"""An MCP step's arguments reach the tool in the type its schema declares.

The step editor now offers a field for each argument of a tool. A field may
hold a ``{{variable}}``; filling one in yields text, so a number taken from
an earlier step reached a tool declaring ``integer`` as ``"3"`` and was
rejected by servers that validate their input.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.mcp.adapter import MCPToolAdapter
from fim_one.core.workflow.nodes import MCPExecutor, _fit_tool_schema
from fim_one.core.workflow.types import ExecutionContext, NodeStatus, NodeType, WorkflowNodeDef
from fim_one.core.workflow.variable_store import VariableStore
from fim_one.db.base import Base

from .assembly import matrix as m

pytestmark = pytest.mark.timeout(60)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "strict": {"type": "boolean"},
        "filter": {"type": "object"},
        "tags": {"type": "array"},
        "label": {"type": "string"},
        "maybe": {"type": ["integer", "null"]},
    },
    "required": ["count"],
}


class TestFitToolSchema:
    def test_a_filled_in_reference_takes_the_declared_type(self) -> None:
        template = {
            "count": "{{start_1.n}}",
            "ratio": "{{start_1.r}}",
            "strict": "{{start_1.s}}",
            "filter": "{{start_1.f}}",
            "tags": "{{start_1.t}}",
            "label": "{{start_1.n}}",
            "maybe": "{{start_1.n}}",
        }
        filled = {
            "count": "3",
            "ratio": " 0.5 ",
            "strict": "true",
            "filter": '{"status": "open"}',
            "tags": '["a", "b"]',
            "label": "3",
            "maybe": "3",
        }
        assert _fit_tool_schema(filled, template, SCHEMA) == {
            "count": 3,
            "ratio": 0.5,
            "strict": True,
            "filter": {"status": "open"},
            "tags": ["a", "b"],
            "label": "3",
            "maybe": 3,
        }

    def test_text_that_is_not_of_the_type_is_passed_as_it_is(self) -> None:
        template = {key: "{{x.y}}" for key in ("count", "ratio", "strict", "filter", "tags")}
        filled = {
            "count": "three",
            "ratio": "nan",
            "strict": "yes",
            "filter": "[1]",
            "tags": "not json",
        }
        assert _fit_tool_schema(filled, template, SCHEMA) == filled

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("3", 3), ("3.0", 3), (" 3.00 ", 3), ("-7", -7), ("+7", 7), ("3e2", 300), ("0", 0)],
    )
    def test_a_whole_number_fits_an_integer_however_it_is_written(
        self, text: str, expected: int
    ) -> None:
        fitted = _fit_tool_schema({"count": text}, {"count": "{{x.y}}"}, SCHEMA)
        assert fitted == {"count": expected}
        assert type(fitted["count"]) is int

    @pytest.mark.parametrize(
        "text",
        ["3.5", "1_000", "٣", "３", "0x10", "1e999999", "1e40", "Infinity", "nan", "", "3 4", "1,000"],
    )
    def test_text_that_is_not_a_plain_whole_number_stays_text_for_an_integer(
        self, text: str
    ) -> None:
        assert _fit_tool_schema({"count": text}, {"count": "{{x.y}}"}, SCHEMA) == {"count": text}

    @pytest.mark.parametrize("text", ["1_000.5", "١٫٥", "inf", "-inf", "nan", "1e999", "0x1p3", "."])
    def test_text_that_is_not_a_plain_number_stays_text_for_a_number(self, text: str) -> None:
        assert _fit_tool_schema({"ratio": text}, {"ratio": "{{x.y}}"}, SCHEMA) == {"ratio": text}

    @pytest.mark.parametrize(("text", "expected"), [("1.5", 1.5), (".5", 0.5), ("1e3", 1000.0)])
    def test_a_plain_number_fits_a_number(self, text: str, expected: float) -> None:
        assert _fit_tool_schema({"ratio": text}, {"ratio": "{{x.y}}"}, SCHEMA) == {
            "ratio": expected
        }

    def test_an_argument_that_may_be_text_stays_text(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": ["integer", "string"]},
                "flag": {"type": ["string", "boolean", "null"]},
            },
        }
        filled = {"id": "007", "flag": "true"}
        template = {"id": "{{x.id}}", "flag": "{{x.flag}}"}
        assert _fit_tool_schema(filled, template, schema) == filled

    def test_a_value_written_literally_is_never_touched(self) -> None:
        literal = {"count": "3", "label": "x"}
        assert _fit_tool_schema(literal, literal, SCHEMA) == literal

    def test_an_argument_the_schema_does_not_name_is_passed_as_it_is(self) -> None:
        assert _fit_tool_schema({"other": "3"}, {"other": "{{x.y}}"}, SCHEMA) == {"other": "3"}
        assert _fit_tool_schema({"count": "3"}, {"count": "{{x.y}}"}, {}) == {"count": "3"}
        assert _fit_tool_schema({"count": "3"}, {"count": "{{x.y}}"}, None) == {"count": "3"}


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mcpargs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_the_step_calls_the_tool_with_typed_arguments(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    scene = await m.seed_scene(
        factory, m.SceneKey(role="owner", allow_fallback=True, is_active=True, has_personal=False)
    )
    received: list[dict[str, Any]] = []

    async def _call(_tool: str, arguments: dict[str, Any]) -> Any:
        received.append(arguments)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], isError=False)

    async def _connect(_client: Any, server: Any, _credential: Any) -> list[Any]:
        return [
            MCPToolAdapter(
                server_name=server.name,
                tool_def={"name": "search", "description": "", "inputSchema": SCHEMA},
                call_fn=_call,
            )
        ]

    store = VariableStore()
    await store.set("start_1.n", 3)
    node = WorkflowNodeDef(
        id="m1",
        type=NodeType.MCP,
        data={
            "server_id": scene.ids["mcp_http"],
            "tool_name": "search",
            "parameters": {"count": "{{start_1.n}}", "label": "top {{start_1.n}}", "ratio": 0.25},
        },
    )
    context = ExecutionContext(run_id="r1", user_id=scene.actor, workflow_id=scene.ids["workflow"])
    with (
        m.outside_world(factory),
        patch("fim_one.core.assembly.toolset._connect_mcp_server", _connect),
    ):
        result = await MCPExecutor().execute(node, store, context)

    assert result.status == NodeStatus.COMPLETED, result.error
    assert received == [{"count": 3, "label": "top 3", "ratio": 0.25}]
