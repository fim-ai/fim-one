"""Regression guards: the web layer must wire AgentWorkspace end-to-end.

The agent layer has workspace-backed recovery paths (large-output offload,
budget-truncation rescue with ``workspace://`` pointers, pre-compaction
transcript snapshots) — but they only function when the web endpoints pass
``workspace=`` to :class:`ReActAgent`.  This was silently missing once
(2026-07 test sweep) and every recovery path degraded to permanent data
loss.  These source-level tests keep the wiring from regressing, and the
cleanup tests keep deleted conversations from orphaning workspace files.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "fim_one"


def _react_agent_calls(module_path: Path) -> list[ast.Call]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "ReActAgent":
                calls.append(node)
    return calls


class TestChatWorkspaceWiring:
    def test_every_react_agent_in_chat_gets_a_workspace(self) -> None:
        chat = SRC / "web" / "api" / "chat.py"
        calls = _react_agent_calls(chat)
        assert calls, "expected ReActAgent construction sites in chat.py"
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "workspace" in kwargs, (
                f"ReActAgent at chat.py:{call.lineno} does not pass "
                "workspace= — offload/truncation-rescue/transcript-snapshot "
                "would silently degrade to data loss."
            )


class TestConversationCleanupWiring:
    def test_conversation_delete_cleans_workspace_and_checkpoint(self) -> None:
        source = (SRC / "web" / "api" / "conversations.py").read_text(
            encoding="utf-8"
        )
        assert "_WORKSPACES_DIR" in source
        assert "_DAG_CHECKPOINTS_DIR" in source
        # Both delete paths (single + batch) remove the workspace dir.
        assert source.count("_WORKSPACES_DIR /") >= 2
        assert source.count("_DAG_CHECKPOINTS_DIR /") >= 2

    def test_user_purge_cleans_workspaces(self) -> None:
        source = (
            SRC / "web" / "services" / "user_deletion.py"
        ).read_text(encoding="utf-8")
        assert 'data" / "workspaces' in source.replace("'", '"')
