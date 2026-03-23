"""OpenAI-compatible image generation provider.

Works with any proxy that implements the ``POST /v1/images/generations``
endpoint (the same interface as DALL-E), e.g. UniAPI's ``nano-banana-2``.
"""

from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .base import BaseImageGen, ImageResult

_DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIImageGen(BaseImageGen):
    """Generate images via the OpenAI Images API (``/v1/images/generations``).

    Compatible with any relay/proxy that exposes the same endpoint, such as
    UniAPI (model ``gemini-nano-banana-2``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("IMAGE_GEN_API_KEY", "")
        self._model = model or os.environ.get("IMAGE_GEN_MODEL", "dall-e-3")
        self._base_url = (
            base_url or os.environ.get("IMAGE_GEN_BASE_URL", _DEFAULT_BASE)
        ).rstrip("/")

    async def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str = "1:1",
        output_dir: str,
    ) -> ImageResult:
        """Call the OpenAI-compatible images/generations endpoint."""
        url = f"{self._base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Map aspect ratio to size (OpenAI Images API uses WxH).
        size = _aspect_to_size(aspect_ratio)

        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        if size:
            payload["size"] = size

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data", [])
        if not items:
            raise ValueError("OpenAI Images API returned no data")

        image_b64 = items[0].get("b64_json", "")
        if not image_b64:
            raise ValueError("OpenAI Images API response contained no image data")

        image_bytes = base64.b64decode(image_b64)

        # Default to png; the API doesn't always specify mime type.
        ext = ".png"
        slug = re.sub(r"[^\w]+", "_", prompt[:40]).strip("_").lower()
        filename = f"{int(time.time())}_{slug}{ext}"

        out_path = Path(output_dir) / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_bytes)

        url_path = Path(output_dir).as_posix()
        return ImageResult(
            file_path=str(out_path),
            url=f"/{url_path}/{filename}",
            prompt=prompt,
            model=self._model,
        )


def _aspect_to_size(aspect_ratio: str) -> str | None:
    """Best-effort mapping from aspect ratio to OpenAI ``size`` param."""
    mapping = {
        "1:1": "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3": "1024x768",
        "3:4": "768x1024",
    }
    return mapping.get(aspect_ratio)
