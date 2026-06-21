"""Tests for scripts/translate.py translation helpers.

Focus: the untranslated-detection heuristic, whose false positives on
prose-free heading sections (version tags / dates) previously caused those
sections to be re-attempted on every commit forever, silently inflating each
commit by dozens of wasted LLM calls.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "translate.py"
_spec = importlib.util.spec_from_file_location("translate_script", _SCRIPT)
assert _spec is not None and _spec.loader is not None
translate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(translate)


class TestCheckUntranslated:
    def test_non_cjk_locale_never_flagged(self) -> None:
        # German/French don't use the CJK heuristic at all.
        assert translate._check_untranslated("Some English prose here", "de") is False
        assert translate._check_untranslated("Some English prose here", "fr") is False

    def test_english_prose_flagged_for_cjk(self) -> None:
        # Real English prose with no CJK is a genuine translation failure.
        assert translate._check_untranslated("- Fixed the HTTP pool eviction bug", "zh") is True
        assert translate._check_untranslated("Knowledge base assistant settings", "ja") is True

    def test_cjk_prose_not_flagged(self) -> None:
        assert translate._check_untranslated("修复了 HTTP 连接池回收问题", "zh") is False
        assert translate._check_untranslated("ナレッジベースの設定", "ja") is False

    @pytest.mark.parametrize("locale", ["zh", "ja", "ko"])
    def test_version_header_not_flagged(self, locale: str) -> None:
        # Pure version-tag / date headers have nothing to translate and stay
        # byte-identical across locales — they must NOT be flagged, otherwise
        # they re-attempt on every run forever (regression guard).
        assert translate._check_untranslated("## [v0.8.6] - 2026-05-08", locale) is False
        assert translate._check_untranslated("## [v0.8.7] - 2026-06-10\n", locale) is False

    @pytest.mark.parametrize("locale", ["zh", "ja", "ko"])
    def test_code_only_section_not_flagged(self, locale: str) -> None:
        assert translate._check_untranslated("```bash\nuv run pytest\n```", locale) is False

    def test_single_letter_tokens_not_flagged(self, ) -> None:
        # "v" alone (e.g. in a version tag) is not a translatable word.
        assert translate._check_untranslated("# [v1.0]", "zh") is False


class TestSectionReuseIsContentAddressed:
    """Reuse must match by section content (EN hash), not by position.

    Regression guard: inserting/deleting a heading must only (re)translate the
    genuinely new/changed sections — never the whole file. A positional-alignment
    scheme retranslated every section whenever the section count changed (e.g. a
    new "## [vX.Y]" changelog heading), which made every cut-release cost hundreds
    of LLM calls.
    """

    def test_insertion_only_translates_new_section(self, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Prior committed state: two sections, already translated.
        en_v1 = "# A\n\nalpha prose\n\n# B\n\nbeta prose\n"
        committed = tmp_path / "out.md"
        committed.write_text("# A\n\n阿尔法\n\n# B\n\n贝塔\n", encoding="utf-8")
        v1_sections = translate._split_sections(en_v1)
        translate._hashes_put_sections(
            "test/reuse", "zh", [translate._hash(s) for s in v1_sections]
        )

        # New EN: insert "# NEW" between A and B — section count changes 2 → 3.
        en_v2 = "# A\n\nalpha prose\n\n# NEW\n\ngamma prose\n\n# B\n\nbeta prose\n"

        calls: list[str] = []

        def fake_llm(config, system, content, temperature=0.3):  # type: ignore[no-untyped-def]
            calls.append(content)
            return "翻译 " + content  # contains CJK → not flagged untranslated

        monkeypatch.setattr(translate, "llm_chat", fake_llm)

        result = translate._translate_sections(
            tmp_path / "src.md", en_v2, "zh", {}, "sys",
            cache_key="test/reuse", target_path=committed,
        )

        # Only the inserted section is translated; A and B are reused verbatim.
        assert len(calls) == 1
        assert "gamma prose" in calls[0]
        assert "阿尔法" in result and "贝塔" in result
