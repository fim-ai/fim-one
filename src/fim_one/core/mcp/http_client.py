"""HTTP client for MCP transports that pins DNS on every request.

The address check that runs before ``connect`` is one lookup. The connection,
and every later request on it, goes through this client, which resolves again
and connects to the address it checked. Proxy variables are not used: a proxy
would resolve the name itself.
"""

from __future__ import annotations

import asyncio
from typing import Any


def ssrf_safe_httpx2_client(
    headers: dict[str, str] | None = None,
    timeout: Any = None,
    auth: Any = None,
) -> Any:
    """An httpx2 client whose transport pins each request to a checked address."""
    import httpx2

    from fim_one.core.security.ssrf import apply_dns_pin

    class _Pinned(httpx2.AsyncHTTPTransport):
        async def handle_async_request(self, request: Any) -> Any:
            await asyncio.to_thread(apply_dns_pin, request)
            return await super().handle_async_request(request)

    kwargs: dict[str, Any] = {"transport": _Pinned(), "trust_env": False}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return httpx2.AsyncClient(**kwargs)
