"""Security tests for MCP transport policies."""

import os

import pytest
from fim_one.core.security.mcp import (
    get_allowed_stdio_commands,
    is_stdio_allowed,
    validate_stdio_command,
)


class TestIsStdioAllowed:
    def test_default_is_false(self, monkeypatch):
        monkeypatch.delenv("ALLOW_STDIO_MCP", raising=False)
        assert is_stdio_allowed() is False

    def test_true_when_set_true(self, monkeypatch):
        monkeypatch.setenv("ALLOW_STDIO_MCP", "true")
        assert is_stdio_allowed() is True

    def test_true_when_set_1(self, monkeypatch):
        monkeypatch.setenv("ALLOW_STDIO_MCP", "1")
        assert is_stdio_allowed() is True

    def test_true_when_set_yes(self, monkeypatch):
        monkeypatch.setenv("ALLOW_STDIO_MCP", "yes")
        assert is_stdio_allowed() is True

    def test_false_when_set_false(self, monkeypatch):
        monkeypatch.setenv("ALLOW_STDIO_MCP", "false")
        assert is_stdio_allowed() is False

    def test_false_when_set_empty(self, monkeypatch):
        monkeypatch.setenv("ALLOW_STDIO_MCP", "")
        assert is_stdio_allowed() is False


class TestValidateStdioCommand:
    def test_npx_allowed(self):
        validate_stdio_command("npx")  # Should not raise

    def test_full_path_allowed(self):
        validate_stdio_command("/usr/bin/npx")  # Should not raise

    def test_uvx_allowed(self):
        validate_stdio_command("uvx")

    def test_python_allowed(self):
        validate_stdio_command("python3")

    def test_bash_blocked(self):
        with pytest.raises(ValueError, match="bash"):
            validate_stdio_command("bash")

    def test_sh_blocked(self):
        with pytest.raises(ValueError, match="sh"):
            validate_stdio_command("sh")

    def test_curl_blocked(self):
        with pytest.raises(ValueError, match="curl"):
            validate_stdio_command("curl")

    def test_empty_blocked(self):
        with pytest.raises(ValueError, match="empty"):
            validate_stdio_command("")

    def test_whitespace_only_blocked(self):
        with pytest.raises(ValueError, match="empty"):
            validate_stdio_command("   ")

    def test_custom_allowlist(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_STDIO_COMMANDS", "custom-tool,another")
        validate_stdio_command("custom-tool")  # Should not raise
        with pytest.raises(ValueError):
            validate_stdio_command("npx")  # No longer in list


class TestGetAllowedStdioCommands:
    def test_default_list(self, monkeypatch):
        monkeypatch.delenv("ALLOWED_STDIO_COMMANDS", raising=False)
        allowed = get_allowed_stdio_commands()
        assert "npx" in allowed
        assert "uvx" in allowed
        assert "node" in allowed
        assert "python" in allowed
        assert "python3" in allowed

    def test_custom_list(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_STDIO_COMMANDS", "foo,bar")
        allowed = get_allowed_stdio_commands()
        assert allowed == {"foo", "bar"}


class TestValidateMcpUrl:
    """SSRF validation for SSE / Streamable-HTTP MCP server URLs (PR #14)."""

    def test_blocks_imds_for_sse(self):
        from fim_one.web.api.mcp_servers import _validate_mcp_url
        from fim_one.web.exceptions import AppError

        with pytest.raises(AppError) as exc:
            _validate_mcp_url("sse", "http://169.254.169.254/latest/meta-data/")
        assert exc.value.status_code == 400
        assert exc.value.error_code == "mcp_url_blocked"

    def test_blocks_imds_for_streamable_http(self):
        from fim_one.web.api.mcp_servers import _validate_mcp_url
        from fim_one.web.exceptions import AppError

        with pytest.raises(AppError):
            _validate_mcp_url("streamable_http", "http://127.0.0.1:8000/mcp")

    def test_skips_stdio_transport(self):
        from fim_one.web.api.mcp_servers import _validate_mcp_url

        # stdio servers have no URL; validation must not run.
        _validate_mcp_url("stdio", None)

    def test_skips_when_url_missing(self):
        from fim_one.web.api.mcp_servers import _validate_mcp_url

        _validate_mcp_url("sse", None)
