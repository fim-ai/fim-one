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
from fim_one.core.workflow.nodes import MCPExecutor, _interpolated_parameters
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


async def _fill(template: dict[str, Any], values: dict[str, Any], schema: Any = SCHEMA) -> Any:
    store = VariableStore()
    await store.set_many(values)
    return await _interpolated_parameters(store, template, schema)


class TestAFilledInReferenceTakesTheDeclaredType:
    async def test_each_declared_type(self) -> None:
        template = {
            "count": "{{start_1.n}}",
            "ratio": "{{start_1.r}}",
            "strict": "{{start_1.s}}",
            "filter": "{{start_1.f}}",
            "tags": "{{start_1.t}}",
            "label": "{{start_1.n}}",
            "maybe": "{{start_1.n}}",
        }
        values = {
            "start_1.n": "3",
            "start_1.r": " 0.5 ",
            "start_1.s": "true",
            "start_1.f": '{"status": "open"}',
            "start_1.t": '["a", "b"]',
        }
        assert await _fill(template, values) == {
            "count": 3,
            "ratio": 0.5,
            "strict": True,
            "filter": {"status": "open"},
            "tags": ["a", "b"],
            "label": "3",
            "maybe": 3,
        }

    async def test_a_variable_that_already_has_the_type(self) -> None:
        template = {"count": "{{s.n}}", "strict": "{{s.b}}", "filter": "{{s.o}}", "tags": "{{s.l}}"}
        values = {"s.n": 3, "s.b": False, "s.o": {"status": "open"}, "s.l": [1, 2]}
        assert await _fill(template, values) == {
            "count": 3,
            "strict": False,
            "filter": {"status": "open"},
            "tags": [1, 2],
        }

    async def test_text_that_is_not_of_the_type_is_passed_as_it_is(self) -> None:
        template = {key: "{{x." + key + "}}" for key in ("count", "ratio", "strict", "filter", "tags")}
        filled = {
            "count": "three",
            "ratio": "nan",
            "strict": "yes",
            "filter": "[1]",
            "tags": "not json",
        }
        assert await _fill(template, {f"x.{key}": text for key, text in filled.items()}) == filled

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("3", 3), ("3.0", 3), (" 3.00 ", 3), ("-7", -7), ("+7", 7), ("3e2", 300), ("0", 0)],
    )
    async def test_a_whole_number_fits_an_integer_however_it_is_written(
        self, text: str, expected: int
    ) -> None:
        fitted = await _fill({"count": "{{x.y}}"}, {"x.y": text})
        assert fitted == {"count": expected}
        assert type(fitted["count"]) is int

    @pytest.mark.parametrize(
        "text",
        ["3.5", "1_000", "٣", "３", "0x10", "1e999999", "1e40", "Infinity", "nan", "3 4", "1,000"],
    )
    async def test_text_that_is_not_a_plain_whole_number_stays_text_for_an_integer(
        self, text: str
    ) -> None:
        assert await _fill({"count": "{{x.y}}"}, {"x.y": text}) == {"count": text}

    @pytest.mark.parametrize("text", ["1_000.5", "١٫٥", "inf", "-inf", "nan", "1e999", "0x1p3", "."])
    async def test_text_that_is_not_a_plain_number_stays_text_for_a_number(
        self, text: str
    ) -> None:
        assert await _fill({"ratio": "{{x.y}}"}, {"x.y": text}) == {"ratio": text}

    @pytest.mark.parametrize(("text", "expected"), [("1.5", 1.5), (".5", 0.5), ("1e3", 1000.0)])
    async def test_a_plain_number_fits_a_number(self, text: str, expected: float) -> None:
        assert await _fill({"ratio": "{{x.y}}"}, {"x.y": text}) == {"ratio": expected}

    async def test_an_argument_that_may_be_text_stays_text(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": ["integer", "string"]},
                "flag": {"type": ["string", "boolean", "null"]},
                "body": {"type": ["object", "string"]},
            },
        }
        template = {"id": "{{x.id}}", "flag": "{{x.flag}}", "body": "{{x.body}}"}
        values = {"x.id": "007", "x.flag": "true", "x.body": '{"a": 1}'}
        assert await _fill(template, values, schema) == {
            "id": "007",
            "flag": "true",
            "body": '{"a": 1}',
        }

    async def test_a_value_written_literally_is_never_touched(self) -> None:
        literal = {"count": "3", "label": "x", "filter": '{"a": 1}'}
        assert await _fill(literal, {}) == literal

    async def test_an_argument_the_schema_does_not_name_is_passed_as_text(self) -> None:
        assert await _fill({"other": "{{x.y}}"}, {"x.y": "3"}) == {"other": "3"}
        assert await _fill({"count": "{{x.y}}"}, {"x.y": "3"}, {}) == {"count": "3"}
        assert await _fill({"count": "{{x.y}}"}, {"x.y": "3"}, None) == {"count": "3"}


class TestAValueCannotAddToTheStructureAroundIt:
    """An object or list argument is parsed first and filled in afterwards."""

    INJECTION = 'x", "admin": true, "note": "'

    async def test_a_value_inside_a_json_string_stays_that_string(self) -> None:
        filled = await _fill(
            {"filter": '{"status": "{{start_1.status}}"}'}, {"start_1.status": self.INJECTION}
        )
        assert filled == {"filter": {"status": self.INJECTION}}

    async def test_a_value_outside_a_string_is_one_json_value(self) -> None:
        template = {"filter": '{"status": {{s.status}}, "limit": {{s.n}}, "ids": {{s.ids}}}'}
        values = {"s.status": '"open", "admin": true', "s.n": 5, "s.ids": [1, 2]}
        assert await _fill(template, values) == {
            "filter": {"status": '"open", "admin": true', "limit": 5, "ids": [1, 2]}
        }

    async def test_a_list_is_filled_in_item_by_item(self) -> None:
        filled = await _fill(
            {"tags": '["fixed", "{{s.tag}}", {{s.tag}}]'}, {"s.tag": 'a", "b'}
        )
        assert filled == {"tags": ["fixed", 'a", "b', 'a", "b']}

    async def test_a_filled_in_value_is_not_read_as_a_template(self) -> None:
        values = {"s.q": "{{env.SECRET}}", "env.SECRET": "hunter2", "s.o": {"k": "{{env.SECRET}}"}}
        filled = await _fill({"filter": '{"q": "{{s.q}}", "o": {{s.o}}}', "label": "{{s.q}}"}, values)
        assert filled == {
            "filter": {"q": "{{env.SECRET}}", "o": {"k": "{{env.SECRET}}"}},
            "label": "{{env.SECRET}}",
        }

    async def test_a_template_that_is_not_json_is_passed_as_text(self) -> None:
        filled = await _fill({"filter": "{{s.a}}: {{s.b}}"}, {"s.a": '{"k"', "s.b": "1}"})
        assert filled == {"filter": '{"k": 1}'}

    async def test_a_variable_nothing_set_stays_visible(self) -> None:
        assert await _fill({"filter": '{"q": {{s.missing}}}'}, {}) == {
            "filter": {"q": "{{s.missing}}"}
        }
        assert await _fill({"filter": "{{s.missing}}"}, {}) == {"filter": "{{s.missing}}"}


class TestReferencesAreFilledInAtAnyDepth:
    """Arguments entered as JSON hold objects and lists, not only text."""

    NESTED_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "name": {"type": "string"},
                    "ranges": {"type": "array", "items": {"type": "number"}},
                },
            }
        },
    }

    async def test_text_inside_an_object_and_a_list_is_filled_in(self) -> None:
        template = {
            "filter": {"status": "{{s.status}}", "owners": ["{{s.owner}}", "fixed"], "n": 1},
            "tags": [{"name": "{{s.owner}}"}],
        }
        assert await _fill(template, {"s.status": "open", "s.owner": "ann"}) == {
            "filter": {"status": "open", "owners": ["ann", "fixed"], "n": 1},
            "tags": [{"name": "ann"}],
        }

    async def test_a_nested_value_takes_the_type_declared_at_its_place(self) -> None:
        template = {"filter": {"limit": "{{s.n}}", "name": "{{s.n}}", "ranges": ["{{s.r}}", 2]}}
        assert await _fill(template, {"s.n": "3", "s.r": "0.5"}, self.NESTED_SCHEMA) == {
            "filter": {"limit": 3, "name": "3", "ranges": [0.5, 2]}
        }

    async def test_with_no_schema_every_text_is_filled_in_as_text(self) -> None:
        template = {"body": {"id": "{{s.n}}", "items": ["{{s.n}}"]}, "plain": 7}
        assert await _fill(template, {"s.n": 3}, None) == {
            "body": {"id": "3", "items": ["3"]},
            "plain": 7,
        }


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
    await store.set("start_1.status", 'open", "admin": "yes')
    node = WorkflowNodeDef(
        id="m1",
        type=NodeType.MCP,
        data={
            "server_id": scene.ids["mcp_http"],
            "tool_name": "search",
            "parameters": {
                "count": "{{start_1.n}}",
                "label": "top {{start_1.n}}",
                "ratio": 0.25,
                "filter": '{"status": "{{start_1.status}}"}',
                "tags": ["{{start_1.status}}"],
            },
        },
    )
    context = ExecutionContext(run_id="r1", user_id=scene.actor, workflow_id=scene.ids["workflow"])
    with (
        m.outside_world(factory),
        patch("fim_one.core.assembly.toolset._connect_mcp_server", _connect),
    ):
        result = await MCPExecutor().execute(node, store, context)

    assert result.status == NodeStatus.COMPLETED, result.error
    assert received == [
        {
            "count": 3,
            "label": "top 3",
            "ratio": 0.25,
            # The value is one string in the object, not more of its keys.
            "filter": {"status": 'open", "admin": "yes'},
            "tags": ['open", "admin": "yes'],
        }
    ]
