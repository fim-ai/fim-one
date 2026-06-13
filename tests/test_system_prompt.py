"""Tests for the composable ReAct system-prompt sections.

These guard the single-source-of-truth invariant: the shared spine
(identity, FILE INTEGRITY, language, error-handling bullets) must appear
byte-identically in both prompt modes, the JSON ``str.format`` contract
must stay intact, and the ACCOUNTABILITY tone rule must be present in both.
"""

from __future__ import annotations

from fim_one.core.agent.system_prompt import (
    ACCOUNTABILITY,
    DIAGNOSE_WHY,
    EXIT_CODE_1,
    FILE_INTEGRITY,
    IDENTITY,
    JSON_MODE_SYSTEM_PROMPT,
    LANGUAGE,
    NATIVE_MODE_SYSTEM_PROMPT,
    SAME_ARGS,
    USE_TOOLS_ONLY,
)

_SHARED_SECTIONS = [
    IDENTITY,
    USE_TOOLS_ONLY,
    LANGUAGE,
    FILE_INTEGRITY,
    DIAGNOSE_WHY,
    SAME_ARGS,
    EXIT_CODE_1,
    ACCOUNTABILITY,
]


class TestSharedSpine:
    """Every shared section appears verbatim in BOTH prompt modes."""

    def test_shared_sections_present_in_both_modes(self) -> None:
        rendered_json = JSON_MODE_SYSTEM_PROMPT.format(tool_descriptions="x")
        for section in _SHARED_SECTIONS:
            assert section.content in rendered_json, f"JSON missing {section.name}"
            assert section.content in NATIVE_MODE_SYSTEM_PROMPT, (
                f"NATIVE missing {section.name}"
            )

    def test_accountability_is_last_shared_bullet(self) -> None:
        assert JSON_MODE_SYSTEM_PROMPT.rstrip().endswith("self-criticism.")
        assert NATIVE_MODE_SYSTEM_PROMPT.rstrip().endswith("self-criticism.")

    def test_section_names_are_unique(self) -> None:
        names = [s.name for s in _SHARED_SECTIONS]
        assert len(names) == len(set(names))


class TestJsonModeContract:
    """The JSON template keeps its ``str.format`` placeholder contract."""

    def test_tool_descriptions_placeholder_renders(self) -> None:
        rendered = JSON_MODE_SYSTEM_PROMPT.format(tool_descriptions="[TOOLS]")
        assert "[TOOLS]" in rendered

    def test_doubled_braces_collapse_to_valid_json_example(self) -> None:
        rendered = JSON_MODE_SYSTEM_PROMPT.format(tool_descriptions="x")
        # After .format(), the JSON example braces are single again.
        assert '"type": "tool_call"' in rendered
        assert '"type": "final_answer"' in rendered
        # No stray unrendered braces remain in the guidelines block.
        guidelines = rendered.split("Guidelines:", 1)[1]
        assert "{" not in guidelines and "}" not in guidelines

    def test_json_only_bullets_absent_from_native(self) -> None:
        assert "produce a final_answer immediately" in JSON_MODE_SYSTEM_PROMPT
        assert "Do NOT generate charts" in JSON_MODE_SYSTEM_PROMPT
        assert "produce a final_answer immediately" not in NATIVE_MODE_SYSTEM_PROMPT


class TestNativeMode:
    """The native template has no format placeholders or stray braces."""

    def test_no_format_placeholder(self) -> None:
        assert "{tool_descriptions}" not in NATIVE_MODE_SYSTEM_PROMPT

    def test_no_braces_at_all(self) -> None:
        assert "{" not in NATIVE_MODE_SYSTEM_PROMPT
        assert "}" not in NATIVE_MODE_SYSTEM_PROMPT

    def test_native_only_bullets_present(self) -> None:
        assert "Always think carefully before acting." in NATIVE_MODE_SYSTEM_PROMPT
        assert "STOP calling tools and respond" in NATIVE_MODE_SYSTEM_PROMPT
