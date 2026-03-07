"""Built-in image generation tool powered by Google Imagen."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fim_agent.core.tool.base import BaseTool


class GenerateImageTool(BaseTool):
    """Generate an image from a text prompt using Google Imagen (via Gemini API).

    Requires IMAGE_GEN_API_KEY to be set in the environment.
    The generated image is saved to the uploads directory and the agent
    receives a markdown image link it can embed in its reply.
    """

    def __init__(
        self,
        output_dir: Path | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        # Default to uploads/generated when no conversation context is provided
        # (e.g. tool catalog listing). Per-conversation callers inject the
        # scoped path so images are isolated and cleaned up with the conversation.
        self._output_dir = output_dir or (Path("uploads") / "generated")
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
            "The tool returns a markdown image snippet `![Generated Image](url)`. "
            "You MUST copy this exact markdown snippet verbatim into your reply "
            "so the image renders inline — do NOT describe the image with text instead. "
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

        from fim_agent.core.image_gen.google import GoogleImageGen

        gen = GoogleImageGen()
        try:
            result = await gen.generate(
                prompt, aspect_ratio=aspect_ratio, output_dir=str(self._output_dir)
            )
        except Exception as exc:
            return f"Image generation failed: {exc}"

        # Register generated image as an artifact and use the artifact API URL
        # so the frontend can load the image with authentication.
        if self._artifacts_dir:
            from fim_agent.core.tool.base import Artifact, ToolResult

            img_path = self._output_dir / Path(result.url).name
            if img_path.exists():
                self._artifacts_dir.mkdir(parents=True, exist_ok=True)
                artifact_id = uuid.uuid4().hex[:12]
                stored = self._artifacts_dir / f"{artifact_id}_{img_path.name}"
                shutil.copy2(img_path, stored)
                try:
                    rel_path = str(stored.relative_to(self._artifacts_dir.parent.parent.parent))
                except ValueError:
                    rel_path = stored.name

                # Derive conversation ID from artifacts_dir path to build the
                # artifact API URL (instead of the unserved /uploads/... path).
                conv_id = self._artifacts_dir.parent.name
                artifact_url = f"/api/conversations/{conv_id}/artifacts/{artifact_id}"

                text = (
                    f"![Generated Image]({artifact_url})\n\n"
                    f"*Prompt:* {result.prompt}  \n"
                    f"*Model:* {result.model}"
                )
                artifact = Artifact(
                    name=img_path.name,
                    path=rel_path,
                    mime_type="image/png",
                    size=stored.stat().st_size,
                )
                return ToolResult(content=text, artifacts=[artifact])

        # Fallback when no artifacts_dir: use the direct upload URL.
        text = (
            f"![Generated Image]({result.url})\n\n"
            f"*Prompt:* {result.prompt}  \n"
            f"*Model:* {result.model}"
        )
        return text
