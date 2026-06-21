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
