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
        ("db://shop-db/query", "db://shop-db/query"),
        ("mcp://echo-test/whoami", "mcp://echo-test/whoami"),
        ("", ""),
        (None, None),
    ],
)
def test_the_stored_address_has_no_user_info_query_or_fragment(given, kept):
    assert _where_it_went(given) == kept
