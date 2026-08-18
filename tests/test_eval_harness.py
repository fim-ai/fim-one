"""Contract tests for the behavioral-eval harness (`evals/harness.py`).

`evals/` judges the **model**: given the real system prompt and tool
descriptions, does the agent choose to edit the file instead of generating an
image, does it put the deliverable on disk. Every one of those cases costs
tokens and minutes, and each one reaches its assertion through the same
plumbing — the sandboxed `FileOpsTool`, the recording image tool, the
`EvalRun` accessors, the pass@k retry.

If that plumbing breaks, the eval goes red for a reason that has nothing to do
with the model, and finding out costs a paid run. So the plumbing is pinned
here, against a scripted model, for free: a red eval then means the model
regressed.

These are not behavioral evals and must not become them. Nothing here asserts
what a model *chooses* to do — the script decides that.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from evals.harness import (
    EvalRun,
    RecordingImageTool,
    eval_retry,
    run_case,
)
from fim_one.core.agent.types import Action, AgentResult, StepResult

from .fake_llm import NATIVE_TOOLS, FakeLLM, answer, tool_call


# ---------------------------------------------------------------------------
# EvalRun accessors
# ---------------------------------------------------------------------------


class TestEvalRunAccessors:
    """Trajectory readers the cases assert through."""

    @staticmethod
    def _run(tmp_path: Path, *steps: StepResult) -> EvalRun:
        return EvalRun(
            result=AgentResult(answer="done", steps=list(steps)),
            sandbox=tmp_path,
            image_tool=RecordingImageTool(),
        )

    @staticmethod
    def _call(tool_name: str, **args: object) -> StepResult:
        return StepResult(
            action=Action(
                type="tool_call",
                reasoning="",
                tool_name=tool_name,
                tool_args=dict(args),
            )
        )

    def test_tool_names_lists_calls_in_order(self, tmp_path: Path) -> None:
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
            self._call("generate_image", prompt="x"),
            StepResult(action=Action(type="final_answer", reasoning="", answer="ok")),
        )
        assert run.tool_names() == ["file_ops", "generate_image"]

    def test_file_writes_ignores_reads(self, tmp_path: Path) -> None:
        """A read of the deliverable must not count as producing it."""
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
            self._call("file_ops", operation="read", path="a/scorecard.html"),
        )
        assert run.file_writes() == [
            {"operation": "write", "path": "a/scorecard.html"}
        ]

    @pytest.mark.parametrize(
        "operation",
        ["write", "append", "write_json", "write_csv", "find_replace", "apply_patch"],
    )
    def test_every_mutating_operation_counts_as_a_write(
        self, tmp_path: Path, operation: str
    ) -> None:
        """The restyle case passes by editing, which is not always `write`."""
        run = self._run(
            tmp_path, self._call("file_ops", operation=operation, path="s.html")
        )
        assert run.file_writes(), f"{operation} not recognised as a file write"

    def test_file_writes_filters_by_path(self, tmp_path: Path) -> None:
        run = self._run(
            tmp_path,
            self._call("file_ops", operation="write", path="a/scorecard.html"),
        )
        assert run.file_writes(path_contains="scorecard.html")
        assert not run.file_writes(path_contains="other.html")

    def test_sandbox_file_returns_empty_string_when_absent(
        self, tmp_path: Path
    ) -> None:
        """Cases assert on this falsiness ("scorecard.html vanished")."""
        run = self._run(tmp_path)
        assert run.sandbox_file("missing.html") == ""

    def test_sandbox_html_files_finds_nested_output(self, tmp_path: Path) -> None:
        nested = tmp_path / "out"
        nested.mkdir()
        (nested / "scorecard.html").write_text("<html></html>", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in self._run(tmp_path).sandbox_html_files()] == [
            "scorecard.html"
        ]


# ---------------------------------------------------------------------------
# The recording image tool
# ---------------------------------------------------------------------------


class TestRecordingImageTool:
    """The eval asks whether the model *chooses* generate_image, so the tool
    must look available while never reaching an image API."""

    def test_reports_available_regardless_of_credentials(self) -> None:
        available, reason = RecordingImageTool().availability()
        assert available is True
        assert reason is None

    async def test_records_the_call_instead_of_generating(self) -> None:
        tool = RecordingImageTool()
        out = await tool.run(prompt="a purple gradient")

        assert tool.calls == [{"prompt": "a purple gradient"}]
        assert "eval-stub" in out


# ---------------------------------------------------------------------------
# run_case wiring
# ---------------------------------------------------------------------------


class TestRunCaseWiring:
    """One scripted turn through the real agent, tools, and sandbox."""

    async def test_a_scripted_write_lands_in_the_sandbox(
        self, tmp_path: Path
    ) -> None:
        sandbox = tmp_path / "deliver"
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call(
                    "file_ops",
                    {
                        "operation": "write",
                        "path": "scorecard.html",
                        "content": "<html><body>ok</body></html>",
                    },
                ),
                answer("Wrote scorecard.html."),
            ],
        )

        run = await run_case("build the scorecard", sandbox, llm=llm)

        assert run.tool_names() == ["file_ops"]
        assert run.file_writes(path_contains="scorecard.html")
        assert "ok" in run.sandbox_file("scorecard.html")
        assert [p.name for p in run.sandbox_html_files()] == ["scorecard.html"]

    async def test_the_sandbox_is_created_and_isolated(
        self, tmp_path: Path
    ) -> None:
        """Cases hand run_case a path that may not exist yet, and the file
        tool must be confined to it."""
        sandbox = tmp_path / "not" / "yet" / "there"
        llm = FakeLLM(abilities=NATIVE_TOOLS, responses=[answer("nothing to do")])

        run = await run_case("say hi", sandbox, llm=llm)

        assert sandbox.is_dir()
        assert run.sandbox == sandbox

    async def test_an_image_call_is_recorded_not_executed(
        self, tmp_path: Path
    ) -> None:
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call("generate_image", {"prompt": "a scorecard"}),
                answer("here you go"),
            ],
        )

        run = await run_case("make me a picture", tmp_path / "img", llm=llm)

        assert run.image_tool.calls == [{"prompt": "a scorecard"}]
        assert "generate_image" in run.tool_names()

    async def test_a_preseeded_file_is_visible_to_the_agent(
        self, tmp_path: Path
    ) -> None:
        """The restyle case depends on seeding turn 1's deliverable."""
        sandbox = tmp_path / "restyle"
        sandbox.mkdir()
        (sandbox / "scorecard.html").write_text("<html>old</html>", encoding="utf-8")
        llm = FakeLLM(
            abilities=NATIVE_TOOLS,
            responses=[
                tool_call(
                    "file_ops", {"operation": "read", "path": "scorecard.html"}
                ),
                answer("read it"),
            ],
        )

        run = await run_case("what is in there", sandbox, llm=llm)

        read_steps = [
            step
            for step in run.result.steps
            if step.action.tool_name == "file_ops" and not step.error
        ]
        assert read_steps, "the read never reached the tool"
        assert "old" in (read_steps[0].observation or ""), (
            "the seeded file was not readable from inside the sandbox"
        )


# ---------------------------------------------------------------------------
# pass@k retry
# ---------------------------------------------------------------------------


class TestEvalRetry:
    """Model output is stochastic, so a case may retry. The retry must not
    turn a real regression green, and must not hide a harness crash."""

    async def test_a_passing_attempt_runs_once(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            await eval_retry(attempt)

        assert calls == 1

    async def test_it_retries_up_to_the_budget_then_passes(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise AssertionError("flaky")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            await eval_retry(attempt)

        assert calls == 3

    async def test_it_raises_after_the_last_failed_attempt(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise AssertionError("always fails")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "2"}):
            with pytest.raises(AssertionError, match="always fails"):
                await eval_retry(attempt)

        assert calls == 2

    async def test_it_defaults_to_a_single_attempt(self) -> None:
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise AssertionError("boom")

        env = {k: v for k, v in os.environ.items() if k != "EVAL_ATTEMPTS"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(AssertionError):
                await eval_retry(attempt)

        assert calls == 1

    async def test_a_non_assertion_error_is_not_retried(self) -> None:
        """A harness bug must surface immediately, not burn the budget."""
        calls = 0

        async def attempt() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("harness broke")

        with patch.dict(os.environ, {"EVAL_ATTEMPTS": "3"}):
            with pytest.raises(RuntimeError, match="harness broke"):
                await eval_retry(attempt)

        assert calls == 1
