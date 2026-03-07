"""Built-in image generation tool powered by Google Imagen."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fim_agent.core.tool.base import BaseTool

# Default directory when no per-conversation sandbox is provided.
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "tmp" / "default" / "exec"


class GenerateImageTool(BaseTool):
    """Generate an image from a text prompt using Google Imagen (via Gemini API).

    Requires IMAGE_GEN_API_KEY to be set in the environment.
    The generated image is saved to the shared workspace and automatically
    registered as an artifact via ``scan_new_files()``.
    """

    def __init__(
        self,
        output_dir: Path | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self._output_dir = output_dir or _DEFAULT_OUTPUT_DIR
        self._artifacts_dir = artifacts_dir

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def display_name(self) -> str:
        return "Generate Image"

    @property
    def category(self) -> str:
        return "media"

    @property
    def description(self) -> str:
        return (
            "Generate an image from a text description using Google Imagen. "
            "The image file is attached below automatically — do NOT mention "
            "any download link or URL in your reply. Just briefly describe "
            "what was generated. "
            "Supports aspect ratios: 1:1 (default), 16:9, 9:16, 4:3, 3:4."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed text description of the image to generate.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "description": "Image aspect ratio. Defaults to 1:1.",
                    "default": "1:1",
                },
            },
            "required": ["prompt"],
        }

    def availability(self) -> tuple[bool, str | None]:
        if not os.environ.get("IMAGE_GEN_API_KEY"):
            return (
                False,
                "Set IMAGE_GEN_API_KEY (Google AI Studio key) in your environment to enable image generation.",
            )
        return True, None

    async def run(self, *, prompt: str, aspect_ratio: str = "1:1") -> str:
        available, reason = self.availability()
        if not available:
            return f"Error: {reason}"

        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot files before generation for artifact detection.
        before: set[str] = set()
        if self._artifacts_dir:
            before = {f.name for f in self._output_dir.iterdir() if f.is_file()}

        from fim_agent.core.image_gen.google import GoogleImageGen

        gen = GoogleImageGen()
        try:
            result = await gen.generate(
                prompt, aspect_ratio=aspect_ratio, output_dir=str(self._output_dir)
            )
        except Exception as exc:
            return f"Image generation failed: {exc}"

        # Text summary for LLM — no image URL, the artifact chip handles display.
        text = f"*Prompt:* {result.prompt}\n*Model:* {result.model}"

        # Scan for new files after generation (same pattern as PythonExecTool).
        if self._artifacts_dir:
            from ..artifact_utils import scan_new_files
            from ..base import ToolResult

            artifacts = scan_new_files(self._output_dir, before, self._artifacts_dir)
            if artifacts:
                return ToolResult(content=text, artifacts=artifacts)

        return text
