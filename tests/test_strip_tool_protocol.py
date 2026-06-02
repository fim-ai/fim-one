"""Tests for ``strip_tool_protocol`` — removing model-emitted tool-call
pseudo-protocol that would otherwise leak verbatim into user-facing answers.
"""

from __future__ import annotations

import pytest

from fim_one.core.utils import strip_tool_protocol


class TestStripToolProtocol:
    def test_no_protocol_returns_unchanged(self) -> None:
        text = "Here is a normal answer with **markdown** and `code`."
        assert strip_tool_protocol(text) == text

    def test_empty_string(self) -> None:
        assert strip_tool_protocol("") == ""

    def test_removes_single_tool_call_block(self) -> None:
        text = 'Before <tool_call>{"name": "python_exec", "arguments": {}}</tool_call> after'
        assert strip_tool_protocol(text) == "Before  after".strip()

    def test_removes_tool_response_with_base64(self) -> None:
        blob = "JVBERi0xLjQKJeLjz9MKMSAwIG9iago8PA==" * 50
        text = f"<tool_response>{{\"content\": \"{blob}\"}}</tool_response>"
        assert strip_tool_protocol(text) == ""
        assert "JVBER" not in strip_tool_protocol(text)

    def test_real_bug_mixed_blob_then_summary(self) -> None:
        # The actual failure mode: a fabricated tool transcript followed by a
        # clean summary. Only the summary must survive.
        text = (
            '<tool_call>{"name": "python_exec", "arguments": {"code": "x=1"}}</tool_call>\n'
            "<tool_response>PDF generated: /tmp/report.pdf</tool_response>\n\n"
            "已为您生成完整的 PDF 报告，文件保存在 /tmp/report.pdf。"
        )
        cleaned = strip_tool_protocol(text)
        assert cleaned == "已为您生成完整的 PDF 报告，文件保存在 /tmp/report.pdf。"
        assert "<tool_call>" not in cleaned
        assert "<tool_response>" not in cleaned

    def test_multiple_blocks(self) -> None:
        text = (
            "<tool_call>a</tool_call>middle<tool_response>b</tool_response>end"
        )
        assert strip_tool_protocol(text) == "middleend"

    def test_entirely_protocol_returns_empty(self) -> None:
        text = '<tool_call>{"name": "x", "arguments": {}}</tool_call>'
        assert strip_tool_protocol(text) == ""

    def test_unclosed_trailing_tag_stripped_to_eos(self) -> None:
        # A dangling <tool_call> with no closing tag must not leak its JSON.
        text = 'Answer text.\n<tool_call>{"name": "python_exec", "arguments":'
        assert strip_tool_protocol(text) == "Answer text."

    def test_dangling_closing_tag_removed(self) -> None:
        text = "Answer</tool_response> tail"
        assert strip_tool_protocol(text) == "Answer tail"

    def test_case_insensitive(self) -> None:
        text = "x<TOOL_CALL>noise</TOOL_CALL>y"
        assert strip_tool_protocol(text) == "xy"

    def test_function_call_tag(self) -> None:
        text = "pre<function_call>{}</function_call>post"
        assert strip_tool_protocol(text) == "prepost"

    def test_tool_result_and_outputs_variants(self) -> None:
        text = "a<tool_result>r</tool_result>b<tool_outputs>o</tool_outputs>c"
        assert strip_tool_protocol(text) == "abc"

    def test_collapses_excess_blank_lines(self) -> None:
        text = "para one\n\n<tool_call>x</tool_call>\n\n\n\npara two"
        cleaned = strip_tool_protocol(text)
        assert "\n\n\n" not in cleaned
        assert "para one" in cleaned and "para two" in cleaned

    def test_legit_text_mentioning_tags_in_prose_without_pairs(self) -> None:
        # Bare mentions without an opening tag pattern are left alone.
        text = "The tool_call concept is explained here."
        assert strip_tool_protocol(text) == text
