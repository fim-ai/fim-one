"""Security tests for connector template rendering — JSON injection prevention."""

import pytest
from fim_agent.core.tool.connector.adapter import ConnectorToolAdapter


class TestRenderTemplate:
    """Tests for ConnectorToolAdapter._render_template."""

    def test_normal_string_replacement(self):
        template = {"q": "{{term}}"}
        result = ConnectorToolAdapter._render_template(template, {"term": "hello"})
        assert result == {"q": "hello"}

    def test_inline_string_replacement(self):
        template = {"q": "search: {{term}}"}
        result = ConnectorToolAdapter._render_template(template, {"term": "hello"})
        assert result == {"q": "search: hello"}

    def test_injection_blocked(self):
        """Quotes in value should be escaped, preventing JSON injection."""
        template = {"q": "search: {{term}}"}
        result = ConnectorToolAdapter._render_template(
            template, {"term": 'x", "admin": true'}
        )
        # The injection should stay inside the "q" string, not create a new key
        assert "admin" not in result  # No injected key
        assert result["q"] == 'search: x", "admin": true'

    def test_quotes_in_value(self):
        template = {"msg": "{{text}}"}
        result = ConnectorToolAdapter._render_template(template, {"text": 'say "hi"'})
        assert result == {"msg": 'say "hi"'}

    def test_backslashes_in_value(self):
        template = {"path": "{{dir}}"}
        result = ConnectorToolAdapter._render_template(template, {"dir": "C:\\Users\\test"})
        assert result == {"path": "C:\\Users\\test"}

    def test_newlines_in_value(self):
        template = {"body": "prefix: {{content}}"}
        result = ConnectorToolAdapter._render_template(template, {"content": "line1\nline2"})
        assert result["body"] == "prefix: line1\nline2"

    def test_unicode_in_value(self):
        template = {"q": "{{term}}"}
        result = ConnectorToolAdapter._render_template(template, {"term": "你好世界"})
        assert result == {"q": "你好世界"}

    def test_number_replacement(self):
        """Non-string values should still work (full-placeholder replacement)."""
        template = {"count": "{{n}}"}
        result = ConnectorToolAdapter._render_template(template, {"n": 42})
        assert result == {"count": 42}

    def test_boolean_replacement(self):
        template = {"flag": "{{v}}"}
        result = ConnectorToolAdapter._render_template(template, {"v": True})
        assert result == {"flag": True}

    def test_multiple_placeholders(self):
        template = {"q": "from:{{user}} subject:{{topic}}"}
        result = ConnectorToolAdapter._render_template(
            template, {"user": "alice", "topic": "test"}
        )
        assert result == {"q": "from:alice subject:test"}

    def test_inline_injection_multiple_placeholders(self):
        """Even with multiple placeholders, injection should be blocked."""
        template = {"q": "{{a}} and {{b}}"}
        result = ConnectorToolAdapter._render_template(
            template, {"a": 'x"', "b": '"y'}
        )
        assert result["q"] == 'x" and "y'
