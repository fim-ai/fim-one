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


class TestPlaceholderIntegrity:
    """Cover the JSX/CODE_BLOCK shield-restore contract.

    Restore is positional: it swaps `<!--JSX_6-->` for `comments[6]` wherever
    that token landed. If the model moved the token, restore lands the right
    content on the wrong line and still emits valid MDX — nothing downstream
    catches it. `_check_placeholders` is the only guard against that, and it
    only works on the raw model output, before restore.
    """

    def test_faithful_output_passes(self) -> None:
        text = "- a <!--JSX_0-->\n- b <!--JSX_1-->\n"
        assert translate._check_placeholders(text, "JSX", 2) is None

    def test_no_placeholders_expected_is_noop(self) -> None:
        assert translate._check_placeholders("plain prose", "JSX", 0) is None

    def test_dropped_placeholder_is_caught(self) -> None:
        # Silent in the old code: the comment was simply lost.
        issue = translate._check_placeholders("- a <!--JSX_0-->\n- b\n", "JSX", 2)
        assert issue is not None and "dropped [1]" in issue

    def test_reordered_placeholders_are_caught(self) -> None:
        # THE silent-corruption case: same multiset, wrong lines. Valid MDX,
        # so MDX validation never sees it — this check is the only line of
        # defence.
        issue = translate._check_placeholders(
            "- a <!--JSX_1-->\n- b <!--JSX_0-->\n", "JSX", 2
        )
        assert issue is not None and "reordered" in issue

    def test_duplicated_placeholder_is_caught(self) -> None:
        issue = translate._check_placeholders(
            "- a <!--JSX_0-->\n- b <!--JSX_0-->\n", "JSX", 2
        )
        assert issue is not None
        assert "duplicated [0]" in issue and "dropped [1]" in issue

    def test_invented_placeholder_is_caught(self) -> None:
        issue = translate._check_placeholders(
            "- a <!--JSX_0-->\n- b <!--JSX_7-->\n", "JSX", 2
        )
        assert issue is not None
        assert "invented [7]" in issue and "dropped [1]" in issue

    def test_whitespace_variants_are_tolerated(self) -> None:
        # The model likes to pad the comment; that's cosmetic, not corruption.
        text = "- a <!-- JSX_0 -->\n- b <!--JSX_1 -->\n"
        assert translate._check_placeholders(text, "JSX", 2) is None

    def test_kinds_are_scored_independently(self) -> None:
        text = "<!--CODE_BLOCK_0-->\n- a <!--JSX_0-->\n"
        assert translate._check_placeholders(text, "JSX", 1) is None
        assert translate._check_placeholders(text, "CODE_BLOCK", 1) is None


class TestShieldRestoreRoundTrip:
    def test_jsx_round_trip_preserves_pointers(self) -> None:
        src = (
            "- [ ] Hook System extras {/* dev: dev/hook-system.md */}\n"
            "- [ ] Agent Workspace {/* dev: dev/agent-workspace.md */}\n"
        )
        shielded, comments = translate._shield_jsx_comments(src)
        assert "{/*" not in shielded
        assert len(comments) == 2
        assert translate._restore_jsx_comments(shielded, comments) == src

    def test_restore_honours_whitespace_variants(self) -> None:
        _, comments = translate._shield_jsx_comments("a {/* dev: x.md */}\n")
        out = translate._restore_jsx_comments("a <!-- JSX_0 -->\n", comments)
        assert out == "a {/* dev: x.md */}\n"

    def test_restore_leaves_out_of_range_token_alone(self) -> None:
        # Better a visible bare token (MDX validation trips) than an
        # IndexError crashing the whole translation run.
        _, comments = translate._shield_jsx_comments("a {/* dev: x.md */}\n")
        out = translate._restore_jsx_comments("a <!--JSX_9-->\n", comments)
        assert out == "a <!--JSX_9-->\n"

    def test_code_block_round_trip(self) -> None:
        src = "before\n```bash\n# Configure\nls\n```\nafter\n"
        shielded, blocks = translate._shield_code_blocks(src)
        assert "```" not in shielded
        assert len(blocks) == 1
        assert translate._restore_code_blocks(shielded, blocks) == src

    def test_restore_does_not_cross_families(self) -> None:
        # A JSX restore must not consume a CODE_BLOCK token, or the two
        # restores would fight over the same text.
        _, comments = translate._shield_jsx_comments("a {/* dev: x.md */}\n")
        out = translate._restore_jsx_comments("<!--CODE_BLOCK_0-->\n", comments)
        assert out == "<!--CODE_BLOCK_0-->\n"


class TestFixMdEmphasisClosers:
    """CommonMark cannot close `**` between punctuation and a letter.

    CJK translations drop the space English puts after a bold lead sentence
    (`**句子。**其余`), so Mintlify prints the asterisks. The fixer puts
    that space back and must not touch code, already-valid markup, or
    letter-letter closers that already parse.
    """

    def test_cjk_period_then_letter_gets_a_space(self) -> None:
        src = "- **AI助手面板使用与聊天相同的输入框。**知识库、智能体现在都使用胶囊形输入框。\n"
        out = translate._fix_md_emphasis_closers(src)
        assert out == "- **AI助手面板使用与聊天相同的输入框。** 知识库、智能体现在都使用胶囊形输入框。\n"

    def test_already_spaced_is_unchanged(self) -> None:
        src = "- **图像生成可使用 OpenAI 的模型。** 请求不再发送 `response_format`。\n"
        assert translate._fix_md_emphasis_closers(src) == src

    def test_letter_letter_closer_is_unchanged(self) -> None:
        # `**foo**bar` is already right-flanking in CommonMark.
        src = "这是**替代产品**它们要求迁移。\n"
        assert translate._fix_md_emphasis_closers(src) == src

    def test_colon_label_then_cjk(self) -> None:
        src = "**何时选择哪一种：**如果你发现自己在编写分步说明\n"
        out = translate._fix_md_emphasis_closers(src)
        assert out == "**何时选择哪一种：** 如果你发现自己在编写分步说明\n"

    def test_closing_paren_then_korean_particle(self) -> None:
        src = "드롭다운에서 **KingbaseES (PG compatible)**를 선택합니다.\n"
        out = translate._fix_md_emphasis_closers(src)
        assert out == "드롭다운에서 **KingbaseES (PG compatible)** 를 선택합니다.\n"

    def test_bold_wrapping_inline_code_then_particle(self) -> None:
        src = "**`smart_truncate`**는 휴리스틱 폴백입니다.\n"
        out = translate._fix_md_emphasis_closers(src)
        assert out == "**`smart_truncate`** 는 휴리스틱 폴백입니다.\n"

    def test_pair_inside_inline_code_is_untouched(self) -> None:
        src = "use `**x。**y` as a literal.\n"
        assert translate._fix_md_emphasis_closers(src) == src

    def test_fenced_code_is_untouched(self) -> None:
        src = "prose\n```\n**foo。**bar\n```\nafter\n"
        assert translate._fix_md_emphasis_closers(src) == src

    def test_jsx_comment_is_untouched(self) -> None:
        src = "- item {/* **foo。**bar */}\n"
        assert translate._fix_md_emphasis_closers(src) == src

    def test_italic_cjk_quote_then_particle(self) -> None:
        src = "機能が*「モデルをより賢くする方法」*を解決する場合\n"
        out = translate._fix_md_emphasis_closers(src)
        assert out == "機能が*「モデルをより賢くする方法」* を解決する場合\n"

    def test_idempotent(self) -> None:
        src = "- **句子。**其余内容。\n"
        once = translate._fix_md_emphasis_closers(src)
        twice = translate._fix_md_emphasis_closers(once)
        assert once == twice
        assert once == "- **句子。** 其余内容。\n"

    def test_followed_by_punctuation_is_unchanged(self) -> None:
        src = "- **订阅会随该资源一并移除**，不再保留。\n"
        assert translate._fix_md_emphasis_closers(src) == src


class TestModelAcceptsTemperature:
    def test_reasoning_models_drop_temperature(self) -> None:
        for name in ("gpt-5.6-luna", "openai/gpt-5.4-mini", "o3", "o4-mini", "openai/o1-preview"):
            assert translate._model_accepts_temperature(name) is False, name

    def test_other_models_keep_temperature(self) -> None:
        for name in ("gpt-4o", "claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001", "gemini-2.5-pro", "orca-2"):
            assert translate._model_accepts_temperature(name) is True, name

    def test_request_kwargs_omit_temperature_for_gpt5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        def fake_completion(**kwargs: object) -> _Resp:
            captured.update(kwargs)
            return _Resp()

        monkeypatch.setattr(translate.litellm, "completion", fake_completion)
        cfg = {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna", "api_key": "sk-test"}
        translate._llm_chat_inner(cfg, "sys", "user", temperature=0.3)
        assert "temperature" not in captured
        assert captured["model"] == "openai/gpt-5.6-luna"

        captured.clear()
        cfg["model"] = "gpt-4o-mini"
        translate._llm_chat_inner(cfg, "sys", "user", temperature=0.3)
        assert captured["temperature"] == 0.3
