"""Tests for the built-in ``TemplateRenderTool``.

Jinja2 is a hard dependency of this project, so these tests target the
Jinja2 rendering path (``StrictUndefined``, ``autoescape=False``).
"""

from __future__ import annotations

import json

import pytest

from fim_one.core.tool.base import ToolResult
from fim_one.core.tool.builtin.template_render import (
    _JINJA2_AVAILABLE,
    TemplateRenderTool,
)


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> TemplateRenderTool:
    # No artifacts_dir → HTML detection never promotes output to an
    # artifact; every success returns a markdown ToolResult.
    return TemplateRenderTool()


def _content(result: str | ToolResult) -> str:
    """Extract the textual content from either return shape."""
    if isinstance(result, ToolResult):
        content: str = result.content
        return content
    return result


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestTemplateRenderToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: TemplateRenderTool) -> None:
        assert tool.name == "template_render"

    def test_category(self, tool: TemplateRenderTool) -> None:
        assert tool.category == "general"

    def test_cacheable(self, tool: TemplateRenderTool) -> None:
        assert tool.cacheable is True

    def test_display_name(self, tool: TemplateRenderTool) -> None:
        assert tool.display_name == "Template Render"

    def test_parameters_schema(self, tool: TemplateRenderTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "template" in schema["properties"]
        assert "context" in schema["properties"]
        assert schema["required"] == ["template"]

    def test_jinja2_is_available(self) -> None:
        # The whole suite assumes the Jinja2 branch is exercised.
        assert _JINJA2_AVAILABLE is True


# ======================================================================
# Input validation
# ======================================================================


class TestInputValidation:
    """Validation before rendering."""

    async def test_missing_template(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(context='{"a": 1}')
        assert isinstance(result, str)
        assert "[Error]" in result
        assert "'template' is required" in result

    async def test_empty_template(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(template="", context="{}")
        assert isinstance(result, str)
        assert "'template' is required" in result

    async def test_invalid_context_json(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(template="{{ a }}", context="{not json}")
        assert isinstance(result, str)
        assert "[Error]" in result
        assert "not valid JSON" in result

    async def test_context_not_object(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(template="{{ a }}", context="[1, 2, 3]")
        assert isinstance(result, str)
        assert "[Error]" in result
        assert "must be a JSON object" in result

    async def test_context_defaults_to_empty_object(
        self, tool: TemplateRenderTool
    ) -> None:
        # No context at all → defaults to {} → static template renders fine.
        result = await tool.run(template="static text")
        assert _content(result) == "static text"

    async def test_empty_string_context_treated_as_empty(
        self, tool: TemplateRenderTool
    ) -> None:
        # ``"" or "{}"`` → "{}" in _run_sync, so empty string is valid.
        result = await tool.run(template="hi", context="")
        assert _content(result) == "hi"


# ======================================================================
# Variable substitution
# ======================================================================


class TestVariableSubstitution:
    """Basic ``{{ variable }}`` interpolation."""

    async def test_single_variable(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="Hello {{ name }}!", context='{"name": "Alice"}'
        )
        assert _content(result) == "Hello Alice!"

    async def test_multiple_variables(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ greeting }}, {{ name }}.",
            context='{"greeting": "Hi", "name": "Bob"}',
        )
        assert _content(result) == "Hi, Bob."

    async def test_numeric_and_bool(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ count }} / {{ flag }}",
            context='{"count": 7, "flag": true}',
        )
        assert _content(result) == "7 / True"

    async def test_nested_attribute_access(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ user.city }}",
            context='{"user": {"city": "Tokyo"}}',
        )
        assert _content(result) == "Tokyo"

    async def test_result_type_is_markdown(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(template="plain", context="{}")
        assert isinstance(result, ToolResult)
        assert result.content_type == "markdown"
        assert result.artifacts == []


# ======================================================================
# Control flow: conditionals, loops, filters
# ======================================================================


class TestControlFlow:
    """``{% if %}``, ``{% for %}`` and Jinja2 filters."""

    async def test_conditional_true(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% if admin %}YES{% else %}NO{% endif %}",
            context='{"admin": true}',
        )
        assert _content(result) == "YES"

    async def test_conditional_false(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% if admin %}YES{% else %}NO{% endif %}",
            context='{"admin": false}',
        )
        assert _content(result) == "NO"

    async def test_for_loop(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% for x in items %}{{ x }},{% endfor %}",
            context='{"items": [1, 2, 3]}',
        )
        assert _content(result) == "1,2,3,"

    async def test_for_loop_over_objects(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% for u in users %}{{ u.name }} {% endfor %}",
            context='{"users": [{"name": "A"}, {"name": "B"}]}',
        )
        assert _content(result) == "A B "

    async def test_upper_filter(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ text | upper }}", context='{"text": "hello"}'
        )
        assert _content(result) == "HELLO"

    async def test_default_filter(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ missing | default('fallback') }}",
            context="{}",
        )
        assert _content(result) == "fallback"

    async def test_length_filter(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ items | length }}", context='{"items": [1, 2, 3, 4]}'
        )
        assert _content(result) == "4"

    async def test_join_filter(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{{ parts | join('-') }}",
            context='{"parts": ["a", "b", "c"]}',
        )
        assert _content(result) == "a-b-c"


# ======================================================================
# StrictUndefined: missing variables raise errors
# ======================================================================


class TestStrictUndefined:
    """The tool configures ``StrictUndefined`` — referencing an unknown
    variable is an error rather than silent empty output."""

    async def test_missing_variable_errors(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(template="Hello {{ name }}", context="{}")
        assert isinstance(result, str)
        assert "[Error]" in result
        assert "Template error" in result

    async def test_error_lists_available_variables(
        self, tool: TemplateRenderTool
    ) -> None:
        result = await tool.run(
            template="{{ missing }}",
            context='{"alpha": 1, "beta": 2}',
        )
        assert isinstance(result, str)
        assert "[Error]" in result
        assert "Available context variables" in result
        assert "alpha" in result
        assert "beta" in result

    async def test_error_shows_none_when_no_context(
        self, tool: TemplateRenderTool
    ) -> None:
        result = await tool.run(template="{{ x }}", context="{}")
        assert isinstance(result, str)
        assert "(none)" in result

    async def test_missing_attribute_errors(
        self, tool: TemplateRenderTool
    ) -> None:
        result = await tool.run(
            template="{{ user.email }}",
            context='{"user": {"name": "A"}}',
        )
        assert isinstance(result, str)
        assert "[Error]" in result


# ======================================================================
# Malformed templates and safety
# ======================================================================


class TestMalformedTemplate:
    """Syntax errors and engine safety guards."""

    async def test_syntax_error(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% for x in %}", context='{"x": 1}'
        )
        assert isinstance(result, str)
        assert "[Error]" in result

    async def test_unclosed_block(self, tool: TemplateRenderTool) -> None:
        result = await tool.run(
            template="{% if a %}no end", context='{"a": true}'
        )
        assert isinstance(result, str)
        assert "[Error]" in result

    async def test_autoescape_disabled_html_passthrough(
        self, tool: TemplateRenderTool
    ) -> None:
        # autoescape=False → '<' and '>' are NOT entity-encoded.
        result = await tool.run(
            template="<b>{{ v }}</b>", context='{"v": "x"}'
        )
        content = _content(result)
        assert content == "<b>x</b>"
        assert "&lt;" not in content

    async def test_injected_markup_not_re_evaluated(
        self, tool: TemplateRenderTool
    ) -> None:
        # A context value that itself contains Jinja syntax must be treated
        # as literal data, never re-rendered (no SSTI via data).
        result = await tool.run(
            template="value: {{ v }}",
            context=json.dumps({"v": "{{ 7 * 7 }}"}),
        )
        content = _content(result)
        assert content == "value: {{ 7 * 7 }}"
        assert "49" not in content
