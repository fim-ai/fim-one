"""Tests for the OpenAI Images backend request shaping.

gpt-image-* and dall-e / relay models take different request bodies: the
former rejects ``response_format`` and only knows three sizes.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.image_gen.openai import OpenAIImageGen, _aspect_to_size


class TestAspectToSize:
    def test_gpt_image_sizes_snap_to_supported(self) -> None:
        assert _aspect_to_size("1:1", gpt_image=True) == "1024x1024"
        assert _aspect_to_size("16:9", gpt_image=True) == "1536x1024"
        assert _aspect_to_size("9:16", gpt_image=True) == "1024x1536"
        assert _aspect_to_size("4:3", gpt_image=True) == "1536x1024"
        assert _aspect_to_size("3:4", gpt_image=True) == "1024x1536"

    def test_dalle_sizes_unchanged(self) -> None:
        assert _aspect_to_size("16:9") == "1792x1024"
        assert _aspect_to_size("4:3") == "1024x768"

    def test_unknown_ratio(self) -> None:
        assert _aspect_to_size("21:9") is None
        assert _aspect_to_size("21:9", gpt_image=True) is None


def _client_returning(payload: dict[str, Any]) -> tuple[MagicMock, AsyncMock]:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    post = AsyncMock(return_value=resp)
    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client, post


@pytest.mark.asyncio
async def test_gpt_image_request_has_no_response_format(tmp_path: Any) -> None:
    payload = {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}
    client, post = _client_returning(payload)
    gen = OpenAIImageGen(api_key="sk-test", model="gpt-image-1", base_url="https://api.openai.com/v1")
    with patch("fim_one.core.image_gen.openai.httpx.AsyncClient", return_value=client):
        result = await gen.generate("a cat", aspect_ratio="16:9", output_dir=str(tmp_path))
    body = post.call_args.kwargs["json"]
    assert "response_format" not in body
    assert body["size"] == "1536x1024"
    assert body["model"] == "gpt-image-1"
    assert result.model == "gpt-image-1"


@pytest.mark.asyncio
async def test_dalle_request_keeps_response_format(tmp_path: Any) -> None:
    payload = {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}
    client, post = _client_returning(payload)
    gen = OpenAIImageGen(api_key="sk-test", model="dall-e-3", base_url="https://api.openai.com/v1")
    with patch("fim_one.core.image_gen.openai.httpx.AsyncClient", return_value=client):
        await gen.generate("a cat", aspect_ratio="16:9", output_dir=str(tmp_path))
    body = post.call_args.kwargs["json"]
    assert body["response_format"] == "b64_json"
    assert body["size"] == "1792x1024"
