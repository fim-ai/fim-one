"""Tests for ``fim_one.core.planner.citation_verifier``.

Covers:
  * ``should_verify_citations`` — domain_hint short-circuit + keyword scan
    (legal / financial / medical hits and the no-hit miss path).
  * ``extract_citations`` — Chinese statute refs (法条), Chinese case
    numbers (案号), US/intl-style citations, dedup, and the empty path.
  * ``verify_citations`` — LLM-backed verdict via the ``FakeLLM`` stub:
    normal pass, normal fail-with-issues, no-citation early return,
    graceful degradation when the LLM raises, and input truncation
    at ``_CITATION_VERIFY_TRUNCATION`` chars.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fim_one.core.model import ChatMessage, LLMResult
from fim_one.core.planner import citation_verifier
from fim_one.core.planner.citation_verifier import (
    CitationVerifyResult,
    _CITATION_VERIFY_TRUNCATION,
    extract_citations,
    should_verify_citations,
    verify_citations,
)

from .conftest import FakeLLM


# ======================================================================
# Helpers
# ======================================================================


def _json_llm(payload: dict[str, Any]) -> FakeLLM:
    """A FakeLLM whose plain-text content is a JSON blob.

    Default abilities have ``tool_call`` / ``json_mode`` disabled, so
    ``structured_llm_call`` lands on the plain-text level and parses the
    JSON straight out of ``content``.
    """
    return FakeLLM(
        responses=[
            LLMResult(
                message=ChatMessage(
                    role="assistant",
                    content=json.dumps(payload),
                ),
            ),
        ],
    )


class _RaisingLLM(FakeLLM):
    """A FakeLLM whose ``chat`` always raises, to exercise degradation."""

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResult:
        raise RuntimeError("simulated LLM outage")


class _CapturingLLM(FakeLLM):
    """Records the messages passed to ``chat`` for prompt inspection."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content=json.dumps(
                            {"passed": True, "issues": [], "suggestions": ""}
                        ),
                    ),
                ),
            ],
        )
        self.seen_messages: list[ChatMessage] = []

    async def chat(
        self, messages: list[ChatMessage], **kwargs: Any
    ) -> LLMResult:
        self.seen_messages = messages
        return await super().chat(messages, **kwargs)


# ======================================================================
# should_verify_citations
# ======================================================================


class TestShouldVerifyCitations:
    """Predicate deciding whether citation verification runs."""

    def test_domain_hint_short_circuits_to_true(self) -> None:
        # With a domain_hint set, the keyword scan is skipped entirely —
        # even mundane text triggers verification.
        assert (
            should_verify_citations(
                "summarise the weather", "it is sunny", domain_hint="legal"
            )
            is True
        )

    def test_empty_domain_hint_string_still_short_circuits(self) -> None:
        # Only ``None`` falls back to keyword scanning; an empty string is
        # still "set".
        assert (
            should_verify_citations("hello", "world", domain_hint="") is True
        )

    def test_legal_keyword_hit_chinese(self) -> None:
        assert (
            should_verify_citations("请分析商标法相关问题", "结论如下") is True
        )

    def test_legal_keyword_hit_english(self) -> None:
        assert (
            should_verify_citations(
                "Analyze the trademark dispute", "outcome here"
            )
            is True
        )

    def test_financial_keyword_hit(self) -> None:
        assert (
            should_verify_citations("证券合规审计", "report") is True
        )

    def test_medical_keyword_hit(self) -> None:
        assert (
            should_verify_citations("clinical diagnosis review", "ok") is True
        )

    def test_keyword_found_in_result_text(self) -> None:
        # The scan combines task + result, so a hit in the result counts.
        assert (
            should_verify_citations("generic question", "依据民法典第X条")
            is True
        )

    def test_no_keyword_miss(self) -> None:
        assert (
            should_verify_citations(
                "what is the capital of France", "Paris"
            )
            is False
        )

    def test_no_keyword_miss_with_none_hint(self) -> None:
        assert (
            should_verify_citations("plan a birthday party", "balloons", domain_hint=None)
            is False
        )


# ======================================================================
# extract_citations
# ======================================================================


class TestExtractCitations:
    """Pattern extraction across CN statute / CN case / intl-law forms."""

    def test_chinese_statute_with_article(self) -> None:
        cites = extract_citations("依据《商标法》第五十七条认定侵权")
        assert "《商标法》第五十七条" in cites

    def test_chinese_statute_with_article_and_clause(self) -> None:
        cites = extract_citations("见《反不正当竞争法》第6条第2款")
        assert "《反不正当竞争法》第6条第2款" in cites

    def test_chinese_statute_bare_title(self) -> None:
        # A 《...》 title with no 第..条 suffix still matches.
        cites = extract_citations("参见《广告法》的规定")
        assert "《广告法》" in cites

    def test_chinese_case_number(self) -> None:
        cites = extract_citations("（2025）京73民终1234号判决")
        assert any("号" in c and "2025" in c for c in cites)

    def test_chinese_case_number_ascii_parens(self) -> None:
        cites = extract_citations("see (2024)沪0115民初567号")
        assert any("567号" in c for c in cites)

    def test_intl_section_citation(self) -> None:
        cites = extract_citations("under Section 43(a) of the Lanham Act")
        assert "Section 43(a)" in cites

    def test_intl_article_citation(self) -> None:
        cites = extract_citations("pursuant to Article 13")
        assert "Article 13" in cites

    def test_section_sign_citation(self) -> None:
        cites = extract_citations("violates § 1125")
        assert any("1125" in c for c in cites)

    def test_mixed_citations_all_captured(self) -> None:
        text = (
            "《商标法》第五十七条 and Section 43(a); "
            "（2025）京73民终1234号"
        )
        cites = extract_citations(text)
        assert "《商标法》第五十七条" in cites
        assert "Section 43(a)" in cites
        assert any("1234号" in c for c in cites)

    def test_deduplicates_repeated_citation(self) -> None:
        text = "《商标法》第五十七条 ... 再次引用《商标法》第五十七条"
        cites = extract_citations(text)
        assert cites.count("《商标法》第五十七条") == 1

    def test_no_citations_returns_empty(self) -> None:
        assert extract_citations("just some plain prose with no refs") == []


# ======================================================================
# verify_citations
# ======================================================================


class TestVerifyCitations:
    """LLM-backed verification of extracted citations."""

    async def test_no_citations_early_return(self) -> None:
        # Plain text → no citations → early return without touching the LLM.
        llm = _RaisingLLM()
        result = await verify_citations("plain prose, no legal refs", llm)
        assert isinstance(result, CitationVerifyResult)
        assert result.passed is True
        assert result.citations_found == 0
        assert result.issues == []
        # LLM must not have been consulted.
        assert llm.call_count == 0

    async def test_normal_pass(self) -> None:
        llm = _json_llm(
            {"passed": True, "issues": [], "suggestions": ""}
        )
        result = await verify_citations(
            "依据《商标法》第五十七条认定侵权成立", llm
        )
        assert result.passed is True
        assert result.citations_found == 1
        assert result.issues == []

    async def test_normal_fail_with_issues(self) -> None:
        llm = _json_llm(
            {
                "passed": False,
                "issues": ["第5条 描述与实际不符", "案号疑似虚构"],
                "suggestions": "核对法条编号与案号",
            }
        )
        result = await verify_citations(
            "《反不正当竞争法》第5条 ... （2025）京73民终1234号", llm
        )
        assert result.passed is False
        assert result.citations_found == 2
        assert result.issues == ["第5条 描述与实际不符", "案号疑似虚构"]
        assert result.suggestions == "核对法条编号与案号"

    async def test_llm_raises_degrades_to_passed(self) -> None:
        # The structured-call layer swallows LLM failures and returns the
        # default_value (passed=True), so verify_citations must not crash
        # and must report a non-zero citations_found.
        llm = _RaisingLLM()
        result = await verify_citations(
            "依据《商标法》第五十七条认定侵权", llm
        )
        assert result.passed is True
        assert result.citations_found == 1
        # Default path yields no issues.
        assert result.issues == []

    async def test_malformed_llm_payload_defaults_to_passed(self) -> None:
        # Non-JSON content can't be parsed at any level → default_value used.
        llm = FakeLLM(
            responses=[
                LLMResult(
                    message=ChatMessage(
                        role="assistant",
                        content="totally not json, sorry",
                    ),
                ),
            ],
        )
        result = await verify_citations(
            "依据《商标法》第五十七条认定侵权", llm
        )
        assert result.passed is True
        assert result.citations_found == 1

    async def test_truncation_default_is_6000(self) -> None:
        # Guard the documented cap so the truncation test below is meaningful.
        assert _CITATION_VERIFY_TRUNCATION == 6000

    async def test_input_truncated_to_cap(self) -> None:
        # A real citation up front guarantees citations_found > 0 so the
        # LLM path executes; a long filler tail must be cut at the cap.
        # A distinct sentinel placed *past* the cap proves the tail is cut
        # (plain "x" filler would self-match against the truncated head).
        prefix = "《商标法》第五十七条 "
        head_filler = "h" * _CITATION_VERIFY_TRUNCATION
        sentinel = "ZZZ_BEYOND_THE_CAP_ZZZ"
        long_text = prefix + head_filler + sentinel
        assert len(long_text) > _CITATION_VERIFY_TRUNCATION

        llm = _CapturingLLM()
        result = await verify_citations(long_text, llm)
        assert result.passed is True

        prompt = "\n".join(
            m.content
            for m in llm.seen_messages
            if isinstance(m.content, str)
        )
        # The truncated head (the citation + start of filler) is present.
        assert prefix in prompt
        # Anything past the cap (the sentinel) is dropped from the excerpt.
        assert sentinel not in prompt
