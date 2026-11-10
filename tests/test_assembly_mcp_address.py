"""Whether an MCP server's address carries a secret (``core/assembly/mcp_address.py``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fim_one.core.assembly.mcp_address import (
    SECRET_NAME_WORDS,
    address_carries_secret,
    launch_args_carry_secret,
    strip_url_secrets,
    url_carries_secret,
)


class TestUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://host.example/mcp?token=abc",
            "https://host.example/mcp?v=2",  # any query string at all
            "https://user:pass@host.example/mcp",
            "https://user@host.example/mcp",
            "https://:pass@host.example/mcp",
            "https://host.example/mcp#access_token=abc",
            "https://host.example:8443/sse?api_key=abc#x",
            "http://[::1]:99999/mcp",  # cannot be parsed
            "host.example/mcp?token=abc",  # no scheme, still a query string
            "  https://host.example/mcp?token=abc  ",
        ],
    )
    def test_userinfo_a_query_string_or_a_fragment_counts(self, url: str) -> None:
        assert url_carries_secret(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            None,
            "",
            "   ",
            "https://host.example/mcp",
            "https://host.example:8443/mcp/sse",
            "http://[::1]:8080/mcp",
            "https://host.example/mcp?",  # an empty query string holds nothing
            # Known miss: a secret written as a path segment is not recognised.
            "https://host.example/mcp/3f9c2b7a1d5e4f60/sse",
        ],
    )
    def test_a_plain_address_does_not(self, url: str | None) -> None:
        assert url_carries_secret(url) is False


class TestLaunchArguments:
    @pytest.mark.parametrize(
        "args",
        [
            # a flag named after a secret, with a value
            ["--api-key", "abc"],
            ["--api-key=abc"],
            ["-y", "pkg", "--token", "abc"],
            ["--access_token=abc"],
            ["--client-secret", "abc"],
            ["--password", "hunter2"],
            ["--db-passwd=x"],
            ["--pwd", "x"],
            ["--auth", "abc"],
            ["--credentials", "/run/creds.json"],
            ["--bearer", "abc"],
            ["--API-KEY", "abc"],
            # the single-dash spelling of a whole word
            ["-token", "abc"],
            ["-password=abc"],
            # an assignment to such a name, and a header as it is written
            ["API_KEY=abc"],
            ["GITHUB_TOKEN=abc"],
            ["X-Api-Key: abc"],
            ["Authorization: abc"],
            # a URL that carries one, alone or inside an argument
            ["https://user:pass@host.example/mcp"],
            ["https://host.example/mcp?token=abc"],
            ["--endpoint=https://host.example/mcp?key=abc"],
            ["postgresql://app:pw@db.internal/app"],
            # a known token shape, wherever it stands
            ["sk-0123456789abcdef"],
            ["--model-key-for", "x", "ghp_0123456789abcdefghij"],
            ["gho_0123456789abcdef"],
            ["github_pat_0123456789abcdef"],
            ["xoxb-0123-4567-abcdef"],
            ["xoxp-0123-4567-abcdef"],
            ["AKIAIOSFODNN7EXAMPLE"],
            ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln"],
            ["Bearer abc"],
            ["Bearer", "abc"],
            ["--header", "Authorization: Bearer abc"],
            ["-k=sk-0123456789abcdef"],
            ["--opt", "value:sk-0123456789abcdef"],
            # err towards flagging: the name contains a word
            ["--keyboard-layout", "us"],
            ["--author", "someone"],
        ],
    )
    def test_a_secret_shape_counts(self, args: list[str]) -> None:
        assert launch_args_carry_secret(args) is True

    @pytest.mark.parametrize(
        "args",
        [
            None,
            [],
            ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            ["--port", "8080", "--verbose"],
            ["--transport=stdio"],
            ["run", "server.py"],
            ["https://host.example/mcp"],  # a plain URL
            ["--no-auth"],  # a flag with no value holds nothing
            ["--token"],
            ["--token", "--verbose"],
            ["--token="],
            ["task-list"],  # not ``sk-``
            ["--sk-mode", "fast"],
            ["sk-short"],  # too short for a key
            ["AKIA"],
            ["MODE=fast"],
            ["C:\\tools\\server.exe"],
            # Known misses: nothing in the shape says these are secrets.
            ["-k", "abc123"],
            ["--id", "9f8e7d6c5b4a"],
            ["9f8e7d6c5b4a39281706f5e4d3c2b1a0"],
        ],
    )
    def test_ordinary_arguments_do_not(self, args: list[str] | None) -> None:
        assert launch_args_carry_secret(args) is False

    @pytest.mark.parametrize("word", SECRET_NAME_WORDS)
    def test_every_listed_word_names_a_secret(self, word: str) -> None:
        assert launch_args_carry_secret([f"--x-{word}", "v"]) is True
        assert launch_args_carry_secret([f"X_{word.upper()}=v"]) is True

    def test_arguments_that_are_not_text_are_read_as_text(self) -> None:
        assert launch_args_carry_secret(["--port", 8080]) is False
        assert launch_args_carry_secret(["--token", 12345]) is True


def _server(transport: str | None, **fields: object) -> SimpleNamespace:
    return SimpleNamespace(transport=transport, **fields)


class TestAddress:
    def test_a_stdio_server_is_judged_by_its_arguments(self) -> None:
        flagged = _server("stdio", args=["--api-key", "abc"], url=None)
        plain = _server("stdio", args=["-y", "pkg"], url="https://stale.example/?token=abc")
        assert address_carries_secret(flagged) is True
        # The URL is not what a stdio server is opened with.
        assert address_carries_secret(plain) is False

    @pytest.mark.parametrize("transport", ["sse", "streamable_http"])
    def test_an_http_server_is_judged_by_its_url(self, transport: str) -> None:
        flagged = _server(transport, url="https://host.example/mcp?token=abc", args=None)
        plain = _server(transport, url="https://host.example/mcp", args=["--api-key", "abc"])
        assert address_carries_secret(flagged) is True
        assert address_carries_secret(plain) is False

    def test_an_unknown_transport_is_judged_by_both(self) -> None:
        assert address_carries_secret(_server("websocket", url="wss://h/?t=1", args=None)) is True
        assert address_carries_secret(_server(None, url=None, args=["--token", "x"])) is True
        assert address_carries_secret(_server(None, url="https://h/mcp", args=[])) is False

    def test_a_row_without_the_fields_carries_nothing(self) -> None:
        assert address_carries_secret(SimpleNamespace(transport="stdio")) is False


class TestStripUrlSecrets:
    def test_what_the_rule_flags_is_what_is_cut(self) -> None:
        url = "https://user:pass@host.example:8443/mcp?token=abc#frag"
        stripped = strip_url_secrets(f"failed for url '{url}' twice")
        assert stripped == "failed for url 'https://host.example:8443/mcp' twice"
        assert url_carries_secret("https://host.example:8443/mcp") is False

    @pytest.mark.parametrize("left", ["", "'", '"', "(", "[", "<", "url='"])
    @pytest.mark.parametrize(
        "secret",
        ["p)ss'w", 'a"b', "x]y", "q>r<s", "a)b'c", "end)", "end'", "]}>", "plain", "a,b;c"],
    )
    def test_a_secret_with_quotes_or_brackets_in_it_is_cut_whole(
        self, left: str, secret: str
    ) -> None:
        """The match used to stop at ``) ' " ] < >`` and leave the rest standing."""
        right = {"'": "'", '"': '"', "(": ")", "[": "]", "<": ">", "url='": "'"}.get(left, "")
        marked = secret.replace("plain", "PLAIN") + "SECRETMARK"
        for url in (
            f"https://user:{marked}@host.example/mcp",
            f"https://host.example/mcp?token={marked}",
            f"https://host.example/mcp#{marked}",
            f"https://{marked}:{marked}@host.example/mcp?a={marked}&b={marked}#{marked}",
        ):
            stripped = strip_url_secrets(f"failed for {left}{url}{right} after 3 tries")
            assert stripped is not None
            assert "SECRETMARK" not in stripped
            assert stripped.startswith(f"failed for {left}https://host.example/mcp")
            assert stripped.endswith(" after 3 tries")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("(see https://host.example/mcp).", "(see https://host.example/mcp)."),
            ("at https://host.example/mcp, then", "at https://host.example/mcp, then"),
            ("URL('https://h.example/mcp?t=abc')", "URL('https://h.example/mcp')"),
            ("http://[::1]:8080/mcp?x=1", "http://[::1]:8080/mcp"),
            ("https://h.example/p?next=https://u:p@h2.example/x", "https://h.example/p"),
            ("https://user:p/ss@h.example/mcp", "https://h.example/mcp"),
            ("//user:pw@h.example/mcp", "//h.example/mcp"),
            ("h.example/mcp?token=abc failed", "h.example/mcp failed"),
            ("why? because a=b", "why? because a=b"),
            ("http://127.0.0.1:99/a@b", "http://127.0.0.1:99/a@b"),
        ],
    )
    def test_what_is_kept_and_what_is_cut(self, text: str, expected: str) -> None:
        assert strip_url_secrets(text) == expected
