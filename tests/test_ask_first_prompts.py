"""Prompt-contract tests for ask-first goal handling (Planner/DAG path).

An ask-first goal ("先用选择题问清楚我再出方案") used to fail under DAG mode:
the planner emitted steps depending on user answers that never arrive, and
the analyzer honestly judged the goal unachieved, causing replan loops and
repeated questionnaires.  The fix is prompt-level (no pause/resume in DAG):

- Planner: ask-first goals with no pause-capable tool plan exactly one
  step delivering the questionnaire itself.
- Analyzer: a complete questionnaire IS achievement, but only when the
  goal itself explicitly requested asking first.
- Router: an explicit ask-first phrasing routes to react.

These tests pin the prompt text so the rules do not silently regress.
"""

from __future__ import annotations

from fim_one.core.planner.analyzer import _ANALYSIS_PROMPT
from fim_one.core.planner.planner import _PLANNING_PROMPT
from fim_one.core.planner.router import _CLASSIFICATION_PROMPT


class TestAskFirstPlannerRule:
    def test_rule_present(self) -> None:
        assert "ASK-FIRST GOALS" in _PLANNING_PROMPT

    def test_rule_demands_single_step(self) -> None:
        assert "EXACTLY ONE step" in _PLANNING_PROMPT

    def test_rule_forbids_answer_dependent_steps(self) -> None:
        assert "do NOT plan steps that depend on those" in _PLANNING_PROMPT


class TestAskFirstAnalyzerRule:
    def test_rule_present(self) -> None:
        assert "ASK-FIRST GOALS" in _ANALYSIS_PROMPT

    def test_rule_is_scoped_to_explicit_ask_first_goals(self) -> None:
        # The guard clause that keeps genuine failures from being
        # whitewashed: results that merely end in a question don't count.
        assert "ONLY when the goal" in _ANALYSIS_PROMPT
        assert "is NOT achievement" in _ANALYSIS_PROMPT


class TestAskFirstRouterExample:
    def test_example_present(self) -> None:
        assert "问清楚我的需求再出方案" in _CLASSIFICATION_PROMPT
        assert "-> react" in _CLASSIFICATION_PROMPT.split("问清楚我的需求再出方案")[1][:200]
