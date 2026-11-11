"""A call record keeps where the call went, not what authenticated it."""

from __future__ import annotations

import pytest

from fim_one.core.assembly.calls import _where_it_went


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("https://api.example.com/v1/items/42", "https://api.example.com/v1/items/42"),
        ("https://api.example.com/v1/items?api_key=sk-live-abc", "https://api.example.com/v1/items"),
        ("https://user:pass@api.example.com:8443/v1", "https://api.example.com:8443/v1"),
        ("https://api.example.com/v1#token=abc", "https://api.example.com/v1"),
        # A credential in the path.
        (
            "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage",
            "https://api.telegram.org/***/sendMessage",
        ),
        ("https://h.example/app;jsessionid=SECRET/items", "https://h.example/app;***/items"),
        ("https://h.example/a%3Fapi_key=SECRET", "https://h.example/***"),
        # No scheme.
        ("u:pw@host.example/a", "host.example/a"),
        ("host.example/a?k=SECRET", "host.example/a"),
        # A UUID segment cannot be told from a token, and is masked.
        (
            "https://api.example.com/v1/items/3f2a9c1e-1111-2222-3333-444455556666",
            "https://api.example.com/v1/items/***",
        ),
        # Synthetic addresses are names, kept readable.
        ("db://shop-db/query", "db://shop-db/query"),
        ("mcp://3f2a9c1e-1111-2222-3333-444455556666/search",
         "mcp://3f2a9c1e-1111-2222-3333-444455556666/search"),
        ("mcp://echo-test/whoami", "mcp://echo-test/whoami"),
        ("", ""),
        (None, None),
    ],
)
def test_the_stored_address_has_no_user_info_query_or_fragment(given, kept):
    assert _where_it_went(given) == kept
