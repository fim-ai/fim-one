"""Tests for the built-in ``TextUtilsTool``."""

from __future__ import annotations

import base64
import hashlib
import uuid

import pytest

from fim_one.core.tool.builtin.text_utils import TextUtilsTool


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture()
def tool() -> TextUtilsTool:
    return TextUtilsTool()


# ======================================================================
# Tool protocol compliance
# ======================================================================


class TestTextUtilsToolProperties:
    """Verify tool protocol properties."""

    def test_name(self, tool: TextUtilsTool) -> None:
        assert tool.name == "text_utils"

    def test_category(self, tool: TextUtilsTool) -> None:
        assert tool.category == "general"

    def test_cacheable(self, tool: TextUtilsTool) -> None:
        assert tool.cacheable is True

    def test_display_name(self, tool: TextUtilsTool) -> None:
        assert tool.display_name == "Text Utilities"

    def test_parameters_schema(self, tool: TextUtilsTool) -> None:
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        for key in ("operation", "text", "pattern", "replacement", "max_length"):
            assert key in props
        assert schema["required"] == ["operation"]


# ======================================================================
# Encoding operations
# ======================================================================


class TestEncoding:
    """Tests for base64 / hex / URL encode and decode."""

    async def test_base64_encode(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="base64_encode", text="hello")
        assert result == base64.b64encode(b"hello").decode("ascii")

    async def test_base64_decode(self, tool: TextUtilsTool) -> None:
        encoded = base64.b64encode(b"hello").decode("ascii")
        result = await tool.run(operation="base64_decode", text=encoded)
        assert result == "hello"

    async def test_base64_roundtrip_unicode(self, tool: TextUtilsTool) -> None:
        encoded = await tool.run(operation="base64_encode", text="héllo 世界")
        decoded = await tool.run(operation="base64_decode", text=encoded)
        assert decoded == "héllo 世界"

    async def test_base64_decode_invalid(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="base64_decode", text="!!!not base64!!!")
        assert "[Error]" in result

    async def test_hex_encode(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="hex_encode", text="hi")
        assert result == "6869"

    async def test_hex_decode(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="hex_decode", text="6869")
        assert result == "hi"

    async def test_hex_decode_invalid(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="hex_decode", text="zzzz")
        assert "[Error]" in result

    async def test_url_encode(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="url_encode", text="a b/c?d=e")
        # safe="" means '/' is also escaped.
        assert result == "a%20b%2Fc%3Fd%3De"

    async def test_url_decode(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="url_decode", text="a%20b%2Fc")
        assert result == "a b/c"


# ======================================================================
# Hashing operations
# ======================================================================


class TestHashing:
    """Tests for md5 / sha1 / sha256."""

    async def test_md5(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="md5", text="hello")
        assert result == hashlib.md5(b"hello").hexdigest()

    async def test_sha1(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="sha1", text="hello")
        assert result == hashlib.sha1(b"hello").hexdigest()

    async def test_sha256(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="sha256", text="hello")
        assert result == hashlib.sha256(b"hello").hexdigest()

    async def test_md5_empty_text(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="md5", text="")
        assert result == hashlib.md5(b"").hexdigest()


# ======================================================================
# UUID generation
# ======================================================================


class TestUuid:
    """Tests for UUID v4 generation."""

    async def test_uuid_is_valid_v4(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="uuid")
        parsed = uuid.UUID(result)
        assert parsed.version == 4

    async def test_uuid_no_text_required(self, tool: TextUtilsTool) -> None:
        # 'uuid' needs no text input.
        result = await tool.run(operation="uuid")
        assert uuid.UUID(result)

    async def test_uuid_values_differ(self, tool: TextUtilsTool) -> None:
        first = await tool.run(operation="uuid")
        second = await tool.run(operation="uuid")
        assert first != second


# ======================================================================
# Regex operations
# ======================================================================


class TestRegex:
    """Tests for regex_match and regex_replace."""

    async def test_regex_match_found(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="regex_match", text="a1 b2 c3", pattern=r"\d"
        )
        assert result == "1\n2\n3"

    async def test_regex_match_groups(self, tool: TextUtilsTool) -> None:
        # A single capture group -> findall returns the group strings.
        result = await tool.run(
            operation="regex_match", text="key=value", pattern=r"(\w+)=(\w+)"
        )
        # findall returns a list of tuples; str(tuple) is rendered per match.
        assert "('key', 'value')" in result

    async def test_regex_match_none(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="regex_match", text="abc", pattern=r"\d"
        )
        assert result == "No matches found."

    async def test_regex_match_missing_pattern(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="regex_match", text="abc")
        assert "[Error]" in result
        assert "pattern" in result

    async def test_regex_match_invalid_pattern(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="regex_match", text="abc", pattern=r"([unterminated"
        )
        assert "[Error]" in result

    async def test_regex_replace(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="regex_replace",
            text="hello world",
            pattern=r"o",
            replacement="0",
        )
        assert result == "hell0 w0rld"

    async def test_regex_replace_missing_pattern(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="regex_replace", text="abc", replacement="x"
        )
        assert "[Error]" in result
        assert "pattern" in result


# ======================================================================
# Counting operations
# ======================================================================


class TestCounting:
    """Tests for word_count and char_count."""

    async def test_word_count(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="word_count", text="one two three")
        assert result == "3"

    async def test_word_count_collapses_whitespace(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="word_count", text="  one   two  "
        )
        assert result == "2"

    async def test_word_count_empty(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="word_count", text="")
        assert result == "0"

    async def test_char_count(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="char_count", text="hello")
        assert result == "5"

    async def test_char_count_counts_spaces(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="char_count", text="a b")
        assert result == "3"


# ======================================================================
# Truncation
# ======================================================================


class TestTruncate:
    """Tests for the truncate operation."""

    async def test_truncate_shorter_than_max(self, tool: TextUtilsTool) -> None:
        # No ellipsis when text fits.
        result = await tool.run(operation="truncate", text="hi", max_length=10)
        assert result == "hi"

    async def test_truncate_longer_than_max(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="truncate", text="hello world", max_length=5
        )
        assert result == "hello…"

    async def test_truncate_exact_length(self, tool: TextUtilsTool) -> None:
        # len == max_length -> no ellipsis appended.
        result = await tool.run(operation="truncate", text="hello", max_length=5)
        assert result == "hello"

    async def test_truncate_missing_max_length(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="truncate", text="hello")
        assert "[Error]" in result
        assert "max_length" in result


# ======================================================================
# Case and whitespace
# ======================================================================


class TestCaseAndWhitespace:
    """Tests for to_upper / to_lower / title_case / strip / slugify."""

    async def test_to_upper(self, tool: TextUtilsTool) -> None:
        assert await tool.run(operation="to_upper", text="Hello") == "HELLO"

    async def test_to_lower(self, tool: TextUtilsTool) -> None:
        assert await tool.run(operation="to_lower", text="Hello") == "hello"

    async def test_title_case(self, tool: TextUtilsTool) -> None:
        assert (
            await tool.run(operation="title_case", text="hello world")
            == "Hello World"
        )

    async def test_strip(self, tool: TextUtilsTool) -> None:
        assert await tool.run(operation="strip", text="  hi  ") == "hi"

    async def test_slugify_basic(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="slugify", text="Hello, World!"
        )
        assert result == "hello-world"

    async def test_slugify_collapses_separators(self, tool: TextUtilsTool) -> None:
        result = await tool.run(
            operation="slugify", text="  Foo__Bar -- Baz  "
        )
        assert result == "foo-bar-baz"

    async def test_slugify_strips_leading_trailing_dashes(
        self, tool: TextUtilsTool
    ) -> None:
        result = await tool.run(operation="slugify", text="--hello--")
        assert result == "hello"


# ======================================================================
# Error handling
# ======================================================================


class TestErrorHandling:
    """Tests for missing / unknown operations."""

    async def test_no_operation(self, tool: TextUtilsTool) -> None:
        result = await tool.run(text="hello")
        assert "[Error]" in result
        assert "No operation" in result

    async def test_empty_operation(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="   ", text="hello")
        assert "[Error]" in result
        assert "No operation" in result

    async def test_unknown_operation(self, tool: TextUtilsTool) -> None:
        result = await tool.run(operation="reverse", text="hello")
        assert "[Error]" in result
        assert "Unknown operation" in result
        assert "reverse" in result
