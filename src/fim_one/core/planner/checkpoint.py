"""File-based DAG step checkpoints for crash-resume.

The DAG endpoint saves the plan (with per-step status and results) after
planning and after every step completion.  If the turn dies mid-execution
— process crash, deploy, dropped connection that aborts the request — the
user's retry of the same goal finds the checkpoint and carries the already
completed steps into the new plan instead of redoing them.

File-based by design (same pattern as ``AgentWorkspace``): one JSON file
per conversation under ``data/dag_checkpoints/``, no database migration.
A checkpoint only matches when the goal hash is identical, so a different
follow-up question never resumes stale steps.  Design: dev/incremental-dag.md.
"""

from __future__ import annotations

__fim_license__ = "FIM-SAL-1.1"
__fim_origin__ = "https://github.com/fim-ai/fim-one"

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from .types import ExecutionPlan, PlanStep, StepOutput

logger = logging.getLogger(__name__)

# Evidence is capped per step in the checkpoint file to keep it small;
# the summary is stored in full.
_CHECKPOINT_EVIDENCE_CHARS = int(os.getenv("DAG_CHECKPOINT_EVIDENCE_CHARS", "4000"))

# Checkpoints older than this are ignored on load (stale crash leftovers).
_CHECKPOINT_MAX_AGE_SECONDS = int(os.getenv("DAG_CHECKPOINT_MAX_AGE_HOURS", "24")) * 3600


def _goal_hash(goal: str) -> str:
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]


class DAGCheckpoint:
    """Save/load/clear per-conversation DAG execution checkpoints."""

    def __init__(self, base_dir: str = "data/dag_checkpoints") -> None:
        self._dir = Path(base_dir)

    def _path(self, conversation_id: str) -> Path:
        safe = "".join(c for c in conversation_id if c.isalnum() or c in "._-")
        return self._dir / f"{safe}.json"

    def save(
        self,
        conversation_id: str,
        goal: str,
        plan: ExecutionPlan,
        round_num: int,
    ) -> None:
        """Persist the plan's current step states.

        Failures are logged and swallowed — checkpointing must never break
        the DAG turn.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            steps = []
            for s in plan.steps:
                record: dict[str, object] = {
                    "id": s.id,
                    "task": s.task,
                    "dependencies": list(s.dependencies),
                    "tool_hint": s.tool_hint,
                    "model_hint": s.model_hint,
                    "status": s.status,
                }
                if s.result is not None:
                    record["summary"] = s.result.summary
                    if s.result.evidence:
                        record["evidence"] = s.result.evidence[:_CHECKPOINT_EVIDENCE_CHARS]
                steps.append(record)
            payload = {
                "goal_hash": _goal_hash(goal),
                "round": round_num,
                "updated_at": time.time(),
                "steps": steps,
            }
            self._path(conversation_id).write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to save DAG checkpoint for %s", conversation_id)

    def load_completed_steps(
        self,
        conversation_id: str,
        goal: str,
    ) -> list[PlanStep]:
        """Return the completed steps from a matching, fresh checkpoint.

        Empty list when there is no checkpoint, the goal differs, the file
        is stale, or nothing completed — callers can always iterate the
        result without branching.
        """
        path = self._path(conversation_id)
        try:
            if not path.exists():
                return []
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("goal_hash") != _goal_hash(goal):
                return []
            if time.time() - float(payload.get("updated_at", 0)) > _CHECKPOINT_MAX_AGE_SECONDS:
                logger.info("DAG checkpoint for %s is stale — ignoring", conversation_id)
                return []
            steps: list[PlanStep] = []
            for record in payload.get("steps", []):
                if record.get("status") != "completed":
                    continue
                step = PlanStep(
                    id=str(record["id"]),
                    task=str(record["task"]),
                    dependencies=list(record.get("dependencies", [])),
                    tool_hint=record.get("tool_hint"),
                    model_hint=record.get("model_hint"),
                )
                step.status = "completed"
                step.result = StepOutput(
                    summary=str(record.get("summary", "")),
                    evidence=str(record.get("evidence", "")) or None,
                )
                steps.append(step)
            return steps
        except Exception:
            logger.exception("Failed to load DAG checkpoint for %s", conversation_id)
            return []

    def clear(self, conversation_id: str) -> None:
        """Remove the checkpoint after a successfully completed turn."""
        try:
            self._path(conversation_id).unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to clear DAG checkpoint for %s", conversation_id)
