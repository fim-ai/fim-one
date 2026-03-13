"""Read Skill tool — loads full skill content on demand."""

from __future__ import annotations

from typing import Any

from fim_one.core.tool.base import BaseTool


class ReadSkillTool(BaseTool):
    """Load the full content of a named skill.

    Call this before executing any procedure described as a skill.
    Returns the complete SOP and optional executable script.
    """

    def __init__(
        self, skill_ids: list[str], user_id: str | None = None
    ) -> None:
        self._skill_ids = skill_ids
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def display_name(self) -> str:
        return "Read Skill"

    @property
    def description(self) -> str:
        return (
            "Load the full content of a named skill. "
            "Call this before executing any procedure described as a skill. "
            "Returns the complete SOP and optional executable script."
        )

    @property
    def category(self) -> str:
        return "skills"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load.",
                },
            },
            "required": ["name"],
        }

    async def run(self, **kwargs: Any) -> str:
        name = kwargs.get("name", "").strip()
        if not name:
            return "[Error] name is required"

        from fim_one.db import create_session
        from fim_one.web.models.skill import Skill
        from sqlalchemy import select

        try:
            async with create_session() as session:
                result = await session.execute(
                    select(Skill).where(
                        Skill.id.in_(self._skill_ids),
                        Skill.name == name,
                        Skill.is_active == True,  # noqa: E712
                    )
                )
                skill = result.scalar_one_or_none()
                if not skill:
                    return f"[Error] skill not found: {name}"
                output = skill.content
                if skill.script and skill.script_type:
                    output += f"\n\n--- Script ({skill.script_type}) ---\n{skill.script}"
                return output
        except Exception as e:
            return f"[Error] failed to load skill: {e}"
