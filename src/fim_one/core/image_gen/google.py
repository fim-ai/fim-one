"""Google Gemini image generation provider."""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
from pathlib import Path

import httpx

from .base import BaseImageGen, ImageResult

# Gemini API base URL
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"



class GoogleImageGen(BaseImageGen):
    """Generate images with Gemini native image generation via the Gemini REST API.

    Auth: API key passed via ``x-goog-api-key`` header (Google AI Studio key).
    Endpoint: ``POST /v1beta/models/{model}:generateContent``
    Docs: https://ai.google.dev/gemini-api/docs/image-generation
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("IMAGE_GEN_API_KEY", "")
        self._model = model or os.environ.get(
            "IMAGE_GEN_MODEL", "gemini-3.1-flash-image-preview"
        )
        self._base_url = (
            base_url or os.environ.get("IMAGE_GEN_BASE_URL", _GEMINI_BASE)
        ).rstrip("/")

    async def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        output_dir: str,
    ) -> ImageResult:
        """Call the Gemini generateContent API and save the result as an image file."""
        url = f"{self._base_url}/models/{self._model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        # Response: candidates[0].content.parts[] — find the IMAGE part
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini API returned no candidates")

        image_b64: str | None = None
        mime_type: str = "image/png"
        for part in candidates[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData")
            if inline:
                image_b64 = inline.get("data", "")
                mime_type = inline.get("mimeType", "image/png")
                break

        if not image_b64:
            raise ValueError("Gemini API response contained no image data")

        image_bytes = base64.b64decode(image_b64)

        # Derive file extension from mime type (image/png → .png, image/jpeg → .jpg)
        ext = mimetypes.guess_extension(mime_type) or ".png"
        if ext == ".jpe":
            ext = ".jpg"

        slug = re.sub(r"[^\w]+", "_", prompt[:40]).strip("_").lower()
        filename = f"{int(time.time())}_{slug}{ext}"

        out_path = Path(output_dir) / filename
        # The output directory is the conversation's workspace, which
        # sandboxed code can write to: never through a symlink left there.
        from fim_one.core.security import safe_fs

        _root, _parts = safe_fs.anchor_of(Path(output_dir))
        safe_fs.write_bytes(_root, (*_parts, filename), image_bytes)

        # Build a server-relative URL from the output_dir so it works regardless
        # of whether images go to uploads/generated or uploads/conversations/{id}.
        url_path = Path(output_dir).as_posix()
        return ImageResult(
            file_path=str(out_path),
            url=f"/{url_path}/{filename}",
            prompt=prompt,
            model=self._model,
        )
