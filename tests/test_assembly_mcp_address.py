"""Whether an MCP server's address carries a secret (``core/assembly/mcp_address.py``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fim_one.core.assembly.mcp_address import (
    SECRET_NAME_WORDS,
    address_carries_secret,
    address_secret_reasons,
    launch_arg_reasons,
    launch_args_carry_secret,
    strip_url_secrets,
    url_carries_secret,
    url_secret_reasons,
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
            # Known miss: a short or worded secret in the path is not recognised.
            "https://host.example/mcp/hunter2/sse",
        ],
    )
    def test_a_plain_address_does_not(self, url: str | None) -> None:
        assert url_carries_secret(url) is False


T32 = "0123456789abcdef0123456789abcdef"


class TestUrlPath:
    """Hosted MCP endpoints write the per-user secret into the path."""

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            (f"https://mcp.zapier.com/api/mcp/s/{T32}/mcp", "url_path_segment"),
            (f"https://mcp.pipedream.net/v2/{T32}", "url_path_segment"),
            (f"https://host.example/{T32}/sse", "url_path_segment"),
            (f"https://mcp.make.com/api/v1/u/{T32[:16]}/sse", "url_path_segment"),  # 16 hex
            (
                "https://mcp.composio.dev/composio/server/3f9c1c2e-1d2a-4b6f-9a77-0d6f2b1c9e11/mcp",
                "url_path_segment",
            ),
            ("https://n8n.example/mcp/aB3dE5gH7jK9mN1pQ3sT5/sse", "url_path_segment"),
            ("https://host.example/mcp/ab12cd34-ef56gh78-ij90kl12", "url_path_segment"),
            ("https://actions.zapier.com/mcp/sk-ak-abcdefgh12345/sse", "url_path_segment"),
            (f"https://host.example/mcp/%30123456789abcdef{T32[:8]}", "url_path_segment"),
            ("https://host.example/mcp;token=abc", "url_path_parameter"),
            ("https://host.example/mcp;v=1/sse", "url_path_parameter"),
            ("https://host.example/mcp%3Ftoken=abc", "url_path_parameter"),
        ],
    )
    def test_a_token_shaped_segment_or_a_path_parameter_counts(self, url: str, reason: str) -> None:
        assert url_secret_reasons(url) == [reason]
        assert url_carries_secret(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            # The documented fixed addresses of well-known remote servers.
            "https://api.githubcopilot.com/mcp/",
            "https://api.githubcopilot.com/mcp/x/repos/readonly",
            "https://mcp.linear.app/sse",
            "https://mcp.linear.app/mcp",
            "https://mcp.notion.com/mcp",
            "https://mcp.notion.com/sse",
            "https://mcp.sentry.dev/mcp",
            "https://mcp.sentry.dev/sse",
            "https://mcp.atlassian.com/v1/sse",
            "https://mcp.context7.com/mcp",
            "https://docs.mcp.cloudflare.com/sse",
            "https://bindings.mcp.cloudflare.com/sse",
            "https://observability.mcp.cloudflare.com/sse",
            "https://mcp.asana.com/sse",
            "https://huggingface.co/mcp",
            "https://hf.co/mcp",
            "https://mcp.stripe.com",
            "https://mcp.paypal.com/sse",
            "https://mcp.deepwiki.com/sse",
            "https://mcp.intercom.com/sse",
            "https://mcp.vercel.com",
            "https://mcp.figma.com/mcp",
            "https://mcp.canva.com/mcp",
            "https://mcp.supabase.com/mcp",
            "https://mcp.neon.tech/sse",
            "https://gitmcp.io/modelcontextprotocol/typescript-sdk",
            "https://learn.microsoft.com/api/mcp",
            "http://localhost:3000/mcp",
            "http://127.0.0.1:8931/sse",
            # Names, not tokens.
            "https://host.example/streamable-http-transport-v2/mcp",
            "https://host.example/api/v2/workspaces/acme-corp/mcp",
            "https://host.example/mcp;charset",
            "https://host.example/0123456789abcde/sse",  # 15 hex
        ],
    )
    def test_fixed_addresses_and_names_do_not(self, url: str) -> None:
        assert url_secret_reasons(url) == []

    def test_the_reasons_name_each_part_once(self) -> None:
        url = f"https://u:p@host.example/{T32}/{T32};a=b?x=1#f"
        assert url_secret_reasons(url) == [
            "url_user_info",
            "url_query",
            "url_fragment",
            "url_path_segment",
            "url_path_parameter",
        ]
        assert url_secret_reasons("http://[::1/mcp") == ["url_unparseable"]


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
            ["Bearer abc123def456"],
            ["Bearer ${TOKEN}"],
            ["--header", "Authorization: Bearer abc"],
            ["--header", "Authorization:${AUTH_HEADER}"],
            ["pk.eyJ1IjoiYSJ9.abcdef"],
            ["--apiKey", "abc"],
            ["--APIKey=abc"],
            ["--apikey", "abc"],
            ["GITHUB_PERSONAL_ACCESS_TOKEN=abc"],
            ["-k=sk-0123456789abcdef"],
            ["--opt", "value:sk-0123456789abcdef"],
        ],
    )
    def test_a_secret_shape_counts(self, args: list[str]) -> None:
        assert launch_args_carry_secret(args) is True

    @pytest.mark.parametrize(
        "args",
        [
            # more token prefixes
            ["glpat-abcdefghij1234567890"],
            ["AIzaSyA-abcdefghijklmnopqrstuvwxyz12345"],
            ["xoxa-2-1234567890-abcdef"],
            ["xapp-1-A0123456789-abcdef"],
            ["ntn_1234567890abcdefghijkl"],
            ["secret_abcdefghijklmnop1234"],
            ["hf_abcdefghijklmnopqrstuv"],
            ["npm_abcdefghijklmnopqrstuv"],
            ["tvly-abcdefghijklmnop"],
            ["fc-0123456789abcdef"],
            ["sk_live_abcdefgh1234"],
            ["sk_test_abcdefgh1234"],
            ["ghs_abcdefgh1234"],
            ["ghu_abcdefgh1234"],
            ["pat-na1-0123456789abcdef"],
            # more names
            ["--pat", "abc"],
            ["--dsn", "abc"],
            ["--connection", "abc"],
            ["--conn-str", "abc"],
            ["--connstr=abc"],
            ["--cookie", "abc"],
            ["SESSION=abc"],
            ["--connectionString", "abc"],
            # Docker's environment, whatever the name
            ["run", "-i", "--rm", "-e", "NOTION_API=abc", "img"],
            ["run", "--env", "FOO=bar", "img"],
            ["run", "--env=FOO=bar", "img"],
            # connection strings and DSNs
            ["--db", "Server=db;User Id=sa;Password=Hunter2;"],
            ["Data Source=db;Uid=sa;Pwd=x"],
            ["--source", "user:pass@tcp(db:3306)/app"],
            # an opaque value, whatever it follows
            ["-k", T32],
            ["some-mcp", T32],
            ["--id", "3f9c1c2e-1d2a-4b6f-9a77-0d6f2b1c9e11"],
            ["X-Zapier-Sig: " + T32],
            ["--opt=" + T32],
        ],
    )
    def test_the_shapes_added_for_stdio_servers_count(self, args: list[str]) -> None:
        assert launch_args_carry_secret(args) is True

    @pytest.mark.parametrize(
        ("args", "reasons"),
        [
            (["run", "-e", "FOO=bar", "img"], ["arg_env"]),
            (["--db", "Server=db;Password=Hunter2;"], ["arg_connection_string"]),
            (["-k", T32], ["arg_token"]),
            (["--api-key", "abc"], ["arg_name"]),
            (["API_KEY=abc"], ["arg_name"]),
            (["sk-0123456789abcdef"], ["arg_token"]),
            (["--api-key", "sk-0123456789abcdef"], ["arg_name", "arg_token"]),
            (["mcp-remote", "https://host.example/mcp?v=2"], ["url_query"]),
            (["mcp-remote", f"https://mcp.zapier.com/api/mcp/s/{T32}/mcp"], ["url_path_segment"]),
            (["postgresql://app:pw@db.internal/app"], ["url_user_info"]),
            (["--token", "a", "--secret", "b"], ["arg_name"]),
            (["-y", "pkg"], []),
        ],
    )
    def test_the_reasons_name_the_part_not_the_value(
        self, args: list[str], reasons: list[str]
    ) -> None:
        assert launch_arg_reasons(args) == reasons

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
            # A secret word inside another word is not the word.
            ["--keyboard-layout", "us"],
            ["--tokenizer", "gpt2"],
            ["--author", "someone"],
            ["--authority", "host"],
            ["--hotkey=F1"],
            ["--monkey", "1"],
            ["KEYBOARD=us"],
            ["turnkey: x"],
            # Plurals say how many or which, not what the secret is.
            ["--max-tokens", "100"],
            ["--allowed-keys", "a,b"],
            # A name that negates, or that describes where or how.
            ["--no-auth", "true"],
            ["--key-file", "/etc/ssl/key.pem"],
            ["--auth-type", "basic"],
            ["--token-url", "https://login.example/oauth/token"],
            # A switch for a value.
            ["--auth", "none"],
            ["--auth=false"],
            # The word alone, or in a sentence.
            ["--mode", "bearer"],
            ["--description", "bearer of news"],
            # A file path, not a token.
            ["/data/sk-archive-2024"],
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
            ["hunter2"],
            # Docker passing a variable through, or reading a file of them.
            ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/x/y"],
            ["run", "--env-file", "/etc/x.env", "img"],
            # Long, but a name.
            ["@modelcontextprotocol/server-sequential-thinking"],
            ["mcp-server-for-something-very-long-v2"],
            ["--connection-timeout", "30"],
            ["secret_santa"],
            ["pat-terns"],
            ["postgresql://db:5432/app"],
            ["git@github.com:org/repo.git"],
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

    def test_the_reasons_are_those_of_the_part_the_transport_uses(self) -> None:
        stdio = _server("stdio", args=["--api-key", "abc"], url="https://h.example/?t=1")
        http = _server("sse", args=["--api-key", "abc"], url="https://h.example/?t=1")
        other = _server(None, args=["--api-key", "abc"], url="https://h.example/?t=1")
        assert address_secret_reasons(stdio) == ["arg_name"]
        assert address_secret_reasons(http) == ["url_query"]
        assert address_secret_reasons(other) == ["url_query", "arg_name"]

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

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                f"POST https://mcp.zapier.com/api/mcp/s/{T32}/mcp failed",
                "POST https://mcp.zapier.com/api/mcp/s/***/mcp failed",
            ),
            (f"https://host.example/{T32}/sse?x=1", "https://host.example/***/sse"),
            (
                "https://open.feishu.cn/open-apis/bot/v2/hook/3f9c1c2e-1d2a-4b6f-9a77-0d6f2b1c9e11",
                "https://open.feishu.cn/open-apis/bot/v2/hook/***",
            ),
            (
                "https://host.example/mcp/sk-ak-abcdefgh12345/sse",
                "https://host.example/mcp/***/sse",
            ),
            ("https://host.example/mcp;token=abc/sse", "https://host.example/mcp;***/sse"),
            (f"https://host.example/{T32};a=b", "https://host.example/***"),
            ("https://host.example/mcp%3Ftoken=abc", "https://host.example/***"),
            (
                "https://api.githubcopilot.com/mcp/x/repos",
                "https://api.githubcopilot.com/mcp/x/repos",
            ),
        ],
    )
    def test_a_token_shaped_path_segment_is_masked(self, text: str, expected: str) -> None:
        assert strip_url_secrets(text) == expected

    def test_whatever_the_rule_flags_in_a_url_is_gone_from_its_stripped_form(self) -> None:
        for url in (
            f"https://u:p@host.example/{T32}/mcp;k=v?x=1#f",
            f"https://mcp.pipedream.net/v2/{T32}",
            "https://host.example/mcp/aB3dE5gH7jK9mN1pQ3sT5/sse",
        ):
            assert url_carries_secret(url) is True
            assert url_carries_secret(strip_url_secrets(url)) is False
