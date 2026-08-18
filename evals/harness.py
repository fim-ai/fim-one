"""Minimal harness for behavioral evals.

Builds a real ``ReActAgent`` (real LLM, real ``FileOpsTool`` on a per-case
sandbox, a *recording* image tool that never hits an image API) and exposes
trajectory-level accessors so cases assert on **what the agent did** —
tool names, file writes, sandbox contents — never on answer wording.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

# fim_one resolves as an *untyped* installed package when mypy checks
# evals/ alone (src layout, no py.typed) — hence the import ignores.  The
# evals.* mypy override in pyproject.toml keeps them valid in both regimes.
from fim_one.core.agent import ReActAgent  # type: ignore[import-untyped]
from fim_one.core.agent.types import AgentResult  # type: ignore[import-untyped]
from fim_one.core.model.openai_compatible import (  # type: ignore[import-untyped]
    OpenAICompatibleLLM,
)
from fim_one.core.tool import ToolRegistry  # type: ignore[import-untyped]
from fim_one.core.tool.builtin.file_ops import (  # type: ignore[import-untyped]
    FileOpsTool,
)
from fim_one.core.tool.builtin.generate_image import (  # type: ignore[import-untyped]
    GenerateImageTool,
)

# The generic "AI purple" gradient banned by HTML_STYLE_BASELINE.
BANNED_PURPLE_HEXES = ("#667eea", "#764ba2")

_FILE_WRITE_OPS = frozenset(
    {"write", "append", "write_json", "write_csv", "find_replace", "apply_patch"}
)


class RecordingImageTool(GenerateImageTool):
    """generate_image stand-in: always offered, never calls an API.

    The eval tests whether the model *chooses* to call the tool, so the
    tool must look fully available while the call itself is a no-op.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    async def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "*Prompt:* (stub)\n*Model:* eval-stub"


@dataclass
class EvalRun:
    """One agent run plus the sandbox it operated on."""

    result: AgentResult
    sandbox: Path
    image_tool: RecordingImageTool

    def tool_names(self) -> list[str]:
        """Names of every tool the agent called, in order."""
        return [
            step.action.tool_name
            for step in self.result.steps
            if step.action.type == "tool_call" and step.action.tool_name
        ]

    def file_writes(self, path_contains: str = "") -> list[dict[str, Any]]:
        """file_ops calls that mutate a file, optionally filtered by path."""
        writes: list[dict[str, Any]] = []
        for step in self.result.steps:
            action = step.action
            if action.type != "tool_call" or action.tool_name != "file_ops":
                continue
            args = action.tool_args or {}
            if args.get("operation") not in _FILE_WRITE_OPS:
                continue
            if path_contains and path_contains not in str(args.get("path", "")):
                continue
            writes.append(args)
        return writes

    def sandbox_file(self, name: str) -> str:
        """Content of a sandbox file ('' when absent)."""
        target = self.sandbox / name
        return target.read_text(encoding="utf-8") if target.exists() else ""

    def sandbox_html_files(self) -> list[Path]:
        return sorted(self.sandbox.rglob("*.html"))


def make_llm() -> OpenAICompatibleLLM:
    """Build the LLM under test from the same env keys the app uses."""
    return OpenAICompatibleLLM(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("LLM_MODEL", "gpt-4o"),
        default_temperature=0.0,
    )


async def run_case(
    query: str,
    sandbox: Path,
    *,
    max_iterations: int = 15,
    llm: Any = None,
) -> EvalRun:
    """Run one query through a real ReAct loop on a sandboxed toolset.

    *llm* defaults to the model under test. Passing a scripted stand-in
    exercises the harness itself without a real call — that is how
    ``tests/test_eval_harness.py`` pins the plumbing these cases rely on,
    so a red eval means the model regressed and not the harness.
    """
    sandbox.mkdir(parents=True, exist_ok=True)
    image_tool = RecordingImageTool()
    tools = ToolRegistry()
    # FileOpsTool widens run() to ``str | ToolResult`` (same ignore[override]
    # every rich builtin carries); production registers it via dynamic
    # discovery so the Tool-protocol mismatch never surfaces statically.
    tools.register(FileOpsTool(workspace_dir=sandbox))  # type: ignore[arg-type]
    tools.register(image_tool)
    agent = ReActAgent(
        llm=llm if llm is not None else make_llm(),
        tools=tools,
        max_iterations=max_iterations,
        enable_plan_tool=False,
    )
    result = await agent.run(query)
    return EvalRun(result=result, sandbox=sandbox, image_tool=image_tool)


async def eval_retry(attempt: Callable[[], Awaitable[None]]) -> None:
    """pass@k: run *attempt* up to EVAL_ATTEMPTS times, pass on first success.

    Model behavior is stochastic — a single failure is a signal, not a
    verdict.  Keep the default at 1 for cheap local runs; raise it
    (``EVAL_ATTEMPTS=3``) when judging whether a failure is a real
    regression.
    """
    tries = max(1, int(os.environ.get("EVAL_ATTEMPTS", "1")))
    for i in range(1, tries + 1):
        try:
            await attempt()
            return
        except AssertionError:
            if i == tries:
                raise
