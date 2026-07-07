"""Unit tests for the FeishuChannel adapter."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.channels import FeishuChannel, build_channel
from fim_one.core.channels.feishu import build_confirmation_card
from fim_one.core.channels.registry import CHANNEL_TYPES, get_channel_class


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_feishu_type_registered(self) -> None:
        assert "feishu" in CHANNEL_TYPES
        assert get_channel_class("feishu") is FeishuChannel

    def test_unknown_type_returns_none(self) -> None:
        assert get_channel_class("telegram") is None
        assert build_channel("telegram", {}) is None

    def test_build_channel_returns_instance(self) -> None:
        channel = build_channel("feishu", {"app_id": "x", "app_secret": "y"})
        assert isinstance(channel, FeishuChannel)


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def _make_async_response(json_body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    resp.status_code = 200
    return resp


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_text_success(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_x"}
        )

        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok_abc"}
        )
        send_resp = _make_async_response({"code": 0, "msg": "success"})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[token_resp, send_resp])
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_async_client.return_value.__aexit__.return_value = None
            result = await channel.send_text("oc_x", "hello")

        assert result.ok is True
        assert result.raw.get("code") == 0

        # Two calls: token fetch + send.
        assert mock_client.post.await_count == 2
        second_call = mock_client.post.await_args_list[1]
        body = second_call.kwargs["json"]
        assert body["receive_id"] == "oc_x"
        assert body["msg_type"] == "text"
        assert json.loads(body["content"]) == {"text": "hello"}
        assert (
            second_call.kwargs["headers"]["Authorization"] == "Bearer tok_abc"
        )

    @pytest.mark.asyncio
    async def test_send_interactive_card(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_default"}
        )
        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        send_resp = _make_async_response({"code": 0})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[token_resp, send_resp])

        card = {"header": {"title": {"content": "Gate"}}, "elements": []}
        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_async_client.return_value.__aexit__.return_value = None
            result = await channel.send_interactive_card("oc_y", card)

        assert result.ok is True
        send_call = mock_client.post.await_args_list[1]
        body = send_call.kwargs["json"]
        assert body["msg_type"] == "interactive"
        assert json.loads(body["content"]) == card

    @pytest.mark.asyncio
    async def test_send_uses_default_chat_id(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_default"}
        )
        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        send_resp = _make_async_response({"code": 0})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[token_resp, send_resp])

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_async_client.return_value.__aexit__.return_value = None
            result = await channel.send_message(
                {"msg_type": "text", "content": "hi"}
            )

        assert result.ok is True
        body = mock_client.post.await_args_list[1].kwargs["json"]
        assert body["receive_id"] == "oc_default"

    @pytest.mark.asyncio
    async def test_send_api_error(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_x"}
        )
        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        send_resp = _make_async_response({"code": 230001, "msg": "bad chat"})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=[token_resp, send_resp])

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_async_client.return_value.__aexit__.return_value = None
            result = await channel.send_text("oc_x", "hi")

        assert result.ok is False
        assert "230001" in (result.error or "") or "bad chat" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_rejects_missing_chat_id(self) -> None:
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "s"})
        result = await channel.send_message(
            {"msg_type": "text", "content": "hi"}
        )
        assert result.ok is False
        assert "chat_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_send_rejects_invalid_content(self) -> None:
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_x"}
        )
        result = await channel.send_message(
            {"chat_id": "oc_x", "msg_type": "text", "content": 42}
        )
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_token_cache_reused_within_ttl(self) -> None:
        """Second send should NOT re-fetch the token."""
        channel = FeishuChannel(
            {"app_id": "cli_x", "app_secret": "s", "chat_id": "oc_x"}
        )
        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok_cached"}
        )
        send_resp = _make_async_response({"code": 0})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            side_effect=[token_resp, send_resp, send_resp]
        )

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = mock_client
            mock_async_client.return_value.__aexit__.return_value = None
            await channel.send_text("oc_x", "hi")
            await channel.send_text("oc_x", "hi again")

        # Three calls expected: token (1) + send (2).
        assert mock_client.post.await_count == 3


# ---------------------------------------------------------------------------
# list_chats
# ---------------------------------------------------------------------------


class TestListChats:
    @pytest.mark.asyncio
    async def test_success_returns_items(self) -> None:
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "s"})

        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        list_resp = _make_async_response(
            {
                "code": 0,
                "data": {
                    "items": [
                        {"chat_id": "oc_1", "name": "A"},
                        {"chat_id": "oc_2", "name": "B", "external": True},
                    ],
                    "has_more": False,
                },
            }
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=list_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value = mock_client
            items = await channel.list_chats()

        assert len(items) == 2
        assert items[0]["chat_id"] == "oc_1"
        assert items[1]["external"] is True
        # GET endpoint + bearer header.
        get_call = mock_client.get.await_args
        assert get_call.args[0].endswith("/open-apis/im/v1/chats")
        assert get_call.kwargs["params"]["page_size"] == 100
        assert get_call.kwargs["headers"]["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_api_error_raises_runtime(self) -> None:
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "s"})

        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        err_resp = _make_async_response(
            {"code": 99991663, "msg": "invalid app_access_token"}
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=err_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value = mock_client
            with pytest.raises(RuntimeError, match="invalid app_access_token"):
                await channel.list_chats()

    @pytest.mark.asyncio
    async def test_bad_credentials_raises_runtime(self) -> None:
        """Token endpoint fails -> list_chats surfaces the failure."""
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "wrong"})

        token_resp = _make_async_response(
            {"code": 10003, "msg": "app ticket is invalid"}
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value = mock_client
            with pytest.raises(
                RuntimeError, match="tenant_access_token"
            ):
                await channel.list_chats()

    @pytest.mark.asyncio
    async def test_empty_payload_returns_empty_list(self) -> None:
        channel = FeishuChannel({"app_id": "cli_x", "app_secret": "s"})

        token_resp = _make_async_response(
            {"code": 0, "tenant_access_token": "tok"}
        )
        empty_resp = _make_async_response({"code": 0, "data": {}})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=empty_resp)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value = mock_client
            items = await channel.list_chats()
        assert items == []


# ---------------------------------------------------------------------------
# handle_callback
# ---------------------------------------------------------------------------


class TestHandleCallback:
    @pytest.mark.asyncio
    async def test_url_verification_echoes_challenge(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        body = {"type": "url_verification", "challenge": "abc123"}
        result = await channel.handle_callback(body, {})

        assert result["response"] == {"challenge": "abc123"}
        assert result["event"]["kind"] == "url_verification"

    @pytest.mark.asyncio
    async def test_legacy_approve_action(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        body = {
            "action": {
                "value": {
                    "confirmation_id": "conf-1",
                    "decision": "approve",
                }
            },
            "open_id": "ou_user",
        }
        result = await channel.handle_callback(body, {})
        assert result["event"]["kind"] == "card_action"
        assert result["event"]["action"] == "approve"
        assert result["event"]["confirmation_id"] == "conf-1"
        assert result["event"]["open_id"] == "ou_user"

    @pytest.mark.asyncio
    async def test_legacy_reject_action(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        body = {
            "action": {
                "value": {
                    "confirmation_id": "conf-2",
                    "decision": "reject",
                }
            },
            "open_id": "ou_other",
        }
        result = await channel.handle_callback(body, {})
        assert result["event"]["action"] == "reject"
        assert result["event"]["confirmation_id"] == "conf-2"

    @pytest.mark.asyncio
    async def test_new_schema_action(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        body = {
            "schema": "2.0",
            "event": {
                "action": {
                    "value": {
                        "confirmation_id": "conf-3",
                        "decision": "approve",
                    }
                },
                "operator": {"open_id": "ou_z"},
            },
        }
        result = await channel.handle_callback(body, {})
        assert result["event"]["action"] == "approve"
        assert result["event"]["confirmation_id"] == "conf-3"
        assert result["event"]["open_id"] == "ou_z"

    @pytest.mark.asyncio
    async def test_unknown_event_kind(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        result = await channel.handle_callback({"foo": "bar"}, {})
        assert result["event"]["kind"] == "unknown"


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


class TestVerifySignature:
    @pytest.mark.asyncio
    async def test_no_key_fails_closed(self) -> None:
        channel = FeishuChannel({"app_id": "x", "app_secret": "y"})
        # No encrypt_key configured -> verification fails closed; an
        # unsigned endpoint would accept forged card-action approvals.
        assert await channel.verify_signature(b"anything", {}) is False

    @pytest.mark.asyncio
    async def test_valid_signature(self) -> None:
        channel = FeishuChannel(
            {"app_id": "x", "app_secret": "y", "encrypt_key": "secret"}
        )
        body = b'{"ping":"pong"}'
        ts = "1700000000"
        nonce = "abc"
        m = hashlib.sha256()
        m.update(ts.encode())
        m.update(nonce.encode())
        m.update(b"secret")
        m.update(body)
        sig = m.hexdigest()
        headers = {
            "X-Lark-Request-Timestamp": ts,
            "X-Lark-Request-Nonce": nonce,
            "X-Lark-Signature": sig,
        }
        assert await channel.verify_signature(body, headers) is True

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self) -> None:
        channel = FeishuChannel(
            {"app_id": "x", "app_secret": "y", "encrypt_key": "secret"}
        )
        headers = {
            "X-Lark-Request-Timestamp": "1700000000",
            "X-Lark-Request-Nonce": "abc",
            "X-Lark-Signature": "0" * 64,
        }
        assert (
            await channel.verify_signature(b'{"x":1}', headers) is False
        )

    @pytest.mark.asyncio
    async def test_missing_headers_rejected(self) -> None:
        channel = FeishuChannel(
            {"app_id": "x", "app_secret": "y", "encrypt_key": "secret"}
        )
        assert await channel.verify_signature(b"x", {}) is False


# ---------------------------------------------------------------------------
# decrypt_callback
# ---------------------------------------------------------------------------


def _feishu_encrypt(encrypt_key: str, payload: dict[str, Any]) -> str:
    """Encrypt a payload the way Feishu does: base64(iv + AES-256-CBC),
    key = sha256(encrypt_key), PKCS7 padding."""
    import base64
    import json as _json

    from cryptography.hazmat.primitives.ciphers import (
        Cipher,
        algorithms,
        modes,
    )

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"\x01" * 16
    plaintext = _json.dumps(payload).encode("utf-8")
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("ascii")


class TestDecryptCallback:
    def test_plaintext_passes_through(self) -> None:
        channel = FeishuChannel({"encrypt_key": "secret"})
        body = {"type": "url_verification", "challenge": "xyz"}
        assert channel.decrypt_callback(body) == body

    def test_encrypted_envelope_is_decrypted(self) -> None:
        channel = FeishuChannel({"encrypt_key": "secret"})
        inner = {"type": "url_verification", "challenge": "xyz"}
        body = {"encrypt": _feishu_encrypt("secret", inner)}
        assert channel.decrypt_callback(body) == inner

    def test_wrong_key_yields_empty_dict(self) -> None:
        channel = FeishuChannel({"encrypt_key": "other-key"})
        body = {"encrypt": _feishu_encrypt("secret", {"a": 1})}
        assert channel.decrypt_callback(body) == {}

    def test_envelope_without_key_yields_empty_dict(self) -> None:
        channel = FeishuChannel({"app_id": "x"})
        body = {"encrypt": _feishu_encrypt("secret", {"a": 1})}
        assert channel.decrypt_callback(body) == {}


# ---------------------------------------------------------------------------
# build_confirmation_card
# ---------------------------------------------------------------------------


class TestBuildConfirmationCard:
    def _buttons(self, card: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect all button elements from a v2.0 card's body tree."""
        buttons: list[dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("tag") == "button":
                    buttons.append(node)
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(card.get("body"))
        return buttons

    def test_card_is_v2_schema(self) -> None:
        card = build_confirmation_card(
            confirmation_id="conf-42",
            title="Approval needed",
            summary="Pay 500 to vendor X",
            tool_name="oa__purchase_pay",
            tool_args_preview='{"amount": 500}',
        )
        assert card["schema"] == "2.0"
        assert isinstance(card.get("body"), dict)
        assert "elements" in card["body"]

    def test_card_has_both_callback_buttons(self) -> None:
        card = build_confirmation_card(
            confirmation_id="conf-42",
            title="Approval needed",
            summary="Pay 500 to vendor X",
            tool_name="oa__purchase_pay",
            tool_args_preview='{"amount": 500}',
        )
        buttons = self._buttons(card)
        assert len(buttons) == 2
        decisions = set()
        for btn in buttons:
            behaviors = btn.get("behaviors")
            assert isinstance(behaviors, list) and behaviors
            # Every button must drive the card.action.trigger event via
            # a callback behavior with our confirmation_id in the value.
            cb = next(
                (b for b in behaviors if b.get("type") == "callback"),
                None,
            )
            assert cb is not None
            val = cb.get("value", {})
            assert val["confirmation_id"] == "conf-42"
            decisions.add(val["decision"])
        assert decisions == {"approve", "reject"}

    def test_card_title_truncates(self) -> None:
        long_title = "x" * 300
        card = build_confirmation_card(
            confirmation_id="c",
            title=long_title,
            summary="",
            tool_name="t",
            tool_args_preview="",
        )
        assert len(card["header"]["title"]["content"]) <= 100
