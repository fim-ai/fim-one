"""Tests for the built-in ``JsonTransformTool``."""

from __future__ import annotations

import json

import pytest

from fim_one.core.tool.builtin.json_transform import JsonTransformTool


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> JsonTransformTool:
    return JsonTransformTool()


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestJsonTransformToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: JsonTransformTool) -> None:
        assert tool.name == "json_transform"

    def test_category(self, tool: JsonTransformTool) -> None:
        assert tool.category == "general"

    def test_cacheable(self, tool: JsonTransformTool) -> None:
        assert tool.cacheable is True

    def test_display_name(self, tool: JsonTransformTool) -> None:
        assert tool.display_name == "JSON Transform"

    def test_description_mentions_operations(self, tool: JsonTransformTool) -> None:
        desc = tool.description
        for op in ("path_get", "merge", "pick", "omit", "flatten", "to_csv", "keys"):
            assert op in desc

    def test_parameters_schema(self, tool: JsonTransformTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        for key in ("operation", "data", "path", "extra", "keys", "sep"):
            assert key in props
        assert schema["required"] == ["operation", "data"]
        assert set(props["operation"]["enum"]) == {
            "path_get",
            "merge",
            "pick",
            "omit",
            "flatten",
            "to_csv",
            "keys",
        }


# ======================================================================
# Top-level validation
# ======================================================================


class TestTopLevelValidation:
    """Validation that runs before any operation dispatch."""

    async def test_missing_operation(self, tool: JsonTransformTool) -> None:
        result = await tool.run(data="{}")
        assert "[Error]" in result
        assert "No operation specified" in result

    async def test_blank_operation(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="   ", data="{}")
        assert "[Error]" in result
        assert "No operation specified" in result

    async def test_unknown_operation(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="explode", data="{}")
        assert "[Error]" in result
        assert "Unknown operation: explode" in result

    async def test_invalid_json_data(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="keys", data="{not json}")
        assert "[Error]" in result
        assert "Invalid JSON in 'data'" in result

    async def test_empty_data_is_invalid_json(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="keys", data="")
        assert "[Error]" in result
        assert "Invalid JSON in 'data'" in result


# ======================================================================
# path_get
# ======================================================================


class TestPathGet:
    """Tests for the ``path_get`` operation."""

    async def test_simple_key(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='{"name": "Alice"}', path="name"
        )
        assert result == "Alice"

    async def test_nested_path(self, tool: JsonTransformTool) -> None:
        data = '{"user": {"address": {"city": "Berlin"}}}'
        result = await tool.run(
            operation="path_get", data=data, path="user.address.city"
        )
        assert result == "Berlin"

    async def test_array_index(self, tool: JsonTransformTool) -> None:
        data = '{"items": [{"name": "first"}, {"name": "second"}]}'
        result = await tool.run(
            operation="path_get", data=data, path="items[0].name"
        )
        assert result == "first"

    async def test_array_index_second(self, tool: JsonTransformTool) -> None:
        data = '{"items": [{"name": "first"}, {"name": "second"}]}'
        result = await tool.run(
            operation="path_get", data=data, path="items[1].name"
        )
        assert result == "second"

    async def test_top_level_array_index(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='[10, 20, 30]', path="[2]"
        )
        assert result == "30"

    async def test_dict_result_is_pretty_json(self, tool: JsonTransformTool) -> None:
        data = '{"user": {"name": "Bob", "age": 30}}'
        result = await tool.run(operation="path_get", data=data, path="user")
        parsed = json.loads(result)
        assert parsed == {"name": "Bob", "age": 30}
        # indent=2 → multi-line output
        assert "\n" in result

    async def test_list_result_is_json(self, tool: JsonTransformTool) -> None:
        data = '{"tags": ["a", "b"]}'
        result = await tool.run(operation="path_get", data=data, path="tags")
        assert json.loads(result) == ["a", "b"]

    async def test_numeric_value_stringified(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='{"count": 42}', path="count"
        )
        assert result == "42"

    async def test_missing_path_param(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="path_get", data='{"a": 1}')
        assert "[Error]" in result
        assert "'path' is required" in result

    async def test_path_not_found(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='{"a": 1}', path="b.c"
        )
        assert "[Error]" in result
        assert "Path not found: b.c" in result

    async def test_array_index_out_of_range(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='{"items": [1]}', path="items[5]"
        )
        assert "[Error]" in result
        assert "Path not found" in result

    async def test_index_into_non_list(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="path_get", data='{"a": {"b": 1}}', path="a[0]"
        )
        assert "[Error]" in result
        assert "Path not found" in result

    async def test_null_value_returns_string_none(
        self, tool: JsonTransformTool
    ) -> None:
        # JSON null becomes Python None; it is not _MISSING, so str(None).
        result = await tool.run(
            operation="path_get", data='{"a": null}', path="a"
        )
        assert result == "None"


# ======================================================================
# merge
# ======================================================================


class TestMerge:
    """Tests for the ``merge`` operation (deep merge of two objects)."""

    async def test_shallow_merge(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="merge", data='{"a": 1}', extra='{"b": 2}'
        )
        assert json.loads(result) == {"a": 1, "b": 2}

    async def test_override_value(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="merge", data='{"a": 1}', extra='{"a": 99}'
        )
        assert json.loads(result) == {"a": 99}

    async def test_deep_merge_nested(self, tool: JsonTransformTool) -> None:
        data = '{"cfg": {"x": 1, "y": 2}}'
        extra = '{"cfg": {"y": 20, "z": 30}}'
        result = await tool.run(operation="merge", data=data, extra=extra)
        assert json.loads(result) == {"cfg": {"x": 1, "y": 20, "z": 30}}

    async def test_dict_replaces_scalar(self, tool: JsonTransformTool) -> None:
        # When override is a dict but base value is scalar, override wins.
        result = await tool.run(
            operation="merge", data='{"a": 1}', extra='{"a": {"nested": true}}'
        )
        assert json.loads(result) == {"a": {"nested": True}}

    async def test_missing_extra(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="merge", data='{"a": 1}')
        assert "[Error]" in result
        assert "'extra' is required" in result

    async def test_invalid_extra_json(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="merge", data='{"a": 1}', extra="{bad}"
        )
        assert "[Error]" in result
        assert "Invalid JSON in 'extra'" in result

    async def test_data_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="merge", data='[1, 2]', extra='{"a": 1}'
        )
        assert "[Error]" in result
        assert "must be JSON objects" in result

    async def test_extra_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(
            operation="merge", data='{"a": 1}', extra='[1, 2]'
        )
        assert "[Error]" in result
        assert "must be JSON objects" in result


# ======================================================================
# pick / omit
# ======================================================================


class TestPick:
    """Tests for the ``pick`` operation."""

    async def test_pick_keys(self, tool: JsonTransformTool) -> None:
        data = '{"a": 1, "b": 2, "c": 3}'
        result = await tool.run(operation="pick", data=data, keys="a,c")
        assert json.loads(result) == {"a": 1, "c": 3}

    async def test_pick_whitespace_in_keys(self, tool: JsonTransformTool) -> None:
        data = '{"a": 1, "b": 2, "c": 3}'
        result = await tool.run(operation="pick", data=data, keys=" a , c ")
        assert json.loads(result) == {"a": 1, "c": 3}

    async def test_pick_nonexistent_key_silently_skipped(
        self, tool: JsonTransformTool
    ) -> None:
        data = '{"a": 1}'
        result = await tool.run(operation="pick", data=data, keys="a,zzz")
        assert json.loads(result) == {"a": 1}

    async def test_pick_missing_keys_param(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="pick", data='{"a": 1}')
        assert "[Error]" in result
        assert "'keys' is required" in result

    async def test_pick_data_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="pick", data='[1, 2]', keys="a")
        assert "[Error]" in result
        assert "must be a JSON object" in result


class TestOmit:
    """Tests for the ``omit`` operation."""

    async def test_omit_keys(self, tool: JsonTransformTool) -> None:
        data = '{"a": 1, "b": 2, "c": 3}'
        result = await tool.run(operation="omit", data=data, keys="b")
        assert json.loads(result) == {"a": 1, "c": 3}

    async def test_omit_multiple(self, tool: JsonTransformTool) -> None:
        data = '{"a": 1, "b": 2, "c": 3}'
        result = await tool.run(operation="omit", data=data, keys="a, c")
        assert json.loads(result) == {"b": 2}

    async def test_omit_missing_keys_param(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="omit", data='{"a": 1}')
        assert "[Error]" in result
        assert "'keys' is required" in result

    async def test_omit_data_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="omit", data='"string"', keys="a")
        assert "[Error]" in result
        assert "must be a JSON object" in result


# ======================================================================
# flatten
# ======================================================================


class TestFlatten:
    """Tests for the ``flatten`` operation."""

    async def test_flatten_nested(self, tool: JsonTransformTool) -> None:
        data = '{"a": {"b": {"c": 1}}, "d": 2}'
        result = await tool.run(operation="flatten", data=data)
        assert json.loads(result) == {"a.b.c": 1, "d": 2}

    async def test_flatten_custom_separator(self, tool: JsonTransformTool) -> None:
        data = '{"a": {"b": 1}}'
        result = await tool.run(operation="flatten", data=data, sep="/")
        assert json.loads(result) == {"a/b": 1}

    async def test_flatten_lists_kept_as_values(
        self, tool: JsonTransformTool
    ) -> None:
        data = '{"a": {"items": [1, 2, 3]}}'
        result = await tool.run(operation="flatten", data=data)
        assert json.loads(result) == {"a.items": [1, 2, 3]}

    async def test_flatten_already_flat(self, tool: JsonTransformTool) -> None:
        data = '{"a": 1, "b": 2}'
        result = await tool.run(operation="flatten", data=data)
        assert json.loads(result) == {"a": 1, "b": 2}

    async def test_flatten_data_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="flatten", data='[1, 2]')
        assert "[Error]" in result
        assert "must be a JSON object" in result


# ======================================================================
# to_csv
# ======================================================================


class TestToCsv:
    """Tests for the ``to_csv`` Markdown-table operation."""

    async def test_basic_table(self, tool: JsonTransformTool) -> None:
        data = '[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]'
        result = await tool.run(operation="to_csv", data=data)
        lines = result.splitlines()
        # header row, separator row, two data rows
        assert len(lines) == 4
        assert "name" in lines[0]
        assert "age" in lines[0]
        assert lines[1].startswith("|-")
        assert "Alice" in result
        assert "Bob" in result

    async def test_union_of_keys(self, tool: JsonTransformTool) -> None:
        # Second object introduces a new column; missing values blank.
        data = '[{"a": 1}, {"a": 2, "b": 3}]'
        result = await tool.run(operation="to_csv", data=data)
        assert "a" in result
        assert "b" in result

    async def test_empty_array(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="to_csv", data="[]")
        assert result == "(empty array)"

    async def test_data_not_array(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="to_csv", data='{"a": 1}')
        assert "[Error]" in result
        assert "must be a JSON array" in result


# ======================================================================
# keys
# ======================================================================


class TestKeys:
    """Tests for the ``keys`` operation."""

    async def test_lists_top_level_keys(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="keys", data='{"a": 1, "b": 2}')
        assert result.splitlines() == ["a", "b"]

    async def test_empty_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="keys", data="{}")
        assert result == ""

    async def test_data_not_object(self, tool: JsonTransformTool) -> None:
        result = await tool.run(operation="keys", data='[1, 2]')
        assert "[Error]" in result
        assert "must be a JSON object" in result
