"""OAuth provider helpers -- GitHub, Google, Discord, and Feishu."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OAuthProvider:
    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    user_info_url: str
    scopes: list[str]


@dataclass
class OAuthUserInfo:
    provider: str
    id: str
    username: str
    email: str | None
    display_name: str | None


class OAuthEmailRequiredError(Exception):
    """Raised when the OAuth provider does not return an email address."""


_PROVIDERS: dict[str, dict] = {
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "user_info_url": "https://api.github.com/user",
        "scopes": ["read:user", "user:email"],
        "env_prefix": "GITHUB",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_info_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": ["openid", "email", "profile"],
        "env_prefix": "GOOGLE",
    },
    "discord": {
        "authorize_url": "https://discord.com/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        "user_info_url": "https://discord.com/api/users/@me",
        "scopes": ["identify", "email"],
        "env_prefix": "DISCORD",
    },
    "feishu": {
        # Feishu (Lark) uses app_id/app_secret naming and a two-step token exchange.
        # Scope is configured in the Feishu Open Platform console, not in the URL.
        "authorize_url": "https://open.feishu.cn/open-apis/authen/v1/index",
        "token_url": "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
        "user_info_url": "https://open.feishu.cn/open-apis/authen/v1/user_info",
        "scopes": [],
        "env_prefix": "FEISHU",
        # Feishu uses APP_ID/APP_SECRET instead of the standard CLIENT_ID/CLIENT_SECRET.
        "env_client_id": "FEISHU_APP_ID",
        "env_client_secret": "FEISHU_APP_SECRET",
    },
}


def get_provider(name: str) -> OAuthProvider | None:
    """Get provider config if env vars are set, else None."""
    cfg = _PROVIDERS.get(name)
    if not cfg:
        return None
    prefix = cfg["env_prefix"]
    # Providers can override the default {PREFIX}_CLIENT_ID naming (e.g. Feishu uses APP_ID).
    client_id = os.environ.get(cfg.get("env_client_id", f"{prefix}_CLIENT_ID"), "")
    client_secret = os.environ.get(cfg.get("env_client_secret", f"{prefix}_CLIENT_SECRET"), "")
    if not client_id or not client_secret:
        return None
    return OAuthProvider(
        name=name,
        client_id=client_id,
        client_secret=client_secret,
        authorize_url=cfg["authorize_url"],
        token_url=cfg["token_url"],
        user_info_url=cfg["user_info_url"],
        scopes=cfg["scopes"],
    )


def get_configured_providers() -> list[str]:
    """Return names of providers that have credentials configured."""
    return [name for name in _PROVIDERS if get_provider(name) is not None]


def build_authorize_url(provider: OAuthProvider, state: str, redirect_uri: str) -> str:
    """Build the OAuth authorization URL."""
    if provider.name == "feishu":
        # Feishu uses app_id (not client_id) and does not accept a scope param.
        params = {
            "app_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{provider.authorize_url}?{urlencode(params)}"

    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(provider.scopes),
    }
    if provider.name in ("google", "discord"):
        params["response_type"] = "code"
    if provider.name == "google":
        params["access_type"] = "offline"
    return f"{provider.authorize_url}?{urlencode(params)}"


async def exchange_code(provider: OAuthProvider, code: str, redirect_uri: str) -> str:
    """Exchange authorization code for access token. Returns the access token."""
    async with httpx.AsyncClient() as client:
        if provider.name == "feishu":
            # Step 1: Get a short-lived app access token using app credentials.
            app_token_resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
                json={"app_id": provider.client_id, "app_secret": provider.client_secret},
            )
            app_token_resp.raise_for_status()
            app_access_token = app_token_resp.json().get("app_access_token")
            if not app_access_token:
                raise ValueError(f"No app_access_token in Feishu response: {app_token_resp.json()}")

            # Step 2: Exchange the authorization code for a user access token.
            resp = await client.post(
                provider.token_url,
                json={"grant_type": "authorization_code", "code": code},
                headers={"Authorization": f"Bearer {app_access_token}"},
            )
            resp.raise_for_status()
            body = resp.json()
            token = body.get("data", {}).get("access_token")
            if not token:
                raise ValueError(f"No access_token in Feishu response: {body}")
            return token

        headers = {"Accept": "application/json"}
        data = {
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if provider.name in ("google", "discord"):
            data["grant_type"] = "authorization_code"
        resp = await client.post(provider.token_url, data=data, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ValueError(f"No access_token in response: {body}")
        return token


async def fetch_user_info(provider: OAuthProvider, access_token: str) -> OAuthUserInfo:
    """Fetch user profile from the OAuth provider."""
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        if provider.name == "github":
            headers["Accept"] = "application/vnd.github+json"
        resp = await client.get(provider.user_info_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if provider.name == "github":
            # Always fetch primary email from /user/emails — the /user
            # endpoint returns the "public profile" email which may differ
            # from the primary (e.g. tony@fim.com.cn vs taotao9229@gmail.com)
            email = None
            email_resp = await client.get(
                "https://api.github.com/user/emails", headers=headers
            )
            if email_resp.status_code == 200:
                emails = email_resp.json()
                primary = next((e for e in emails if e.get("primary")), None)
                email = primary["email"] if primary else None
            # Fall back to /user profile email if /user/emails failed
            if not email:
                email = data.get("email")
            return OAuthUserInfo(
                provider="github",
                id=str(data["id"]),
                username=data.get("login", ""),
                email=email,
                display_name=data.get("name"),
            )
        elif provider.name == "google":
            return OAuthUserInfo(
                provider="google",
                id=data["id"],
                username=data.get("email", "").split("@")[0],
                email=data.get("email"),
                display_name=data.get("name"),
            )
        elif provider.name == "discord":
            return OAuthUserInfo(
                provider="discord",
                id=data["id"],
                username=data.get("username", ""),
                email=data.get("email"),
                display_name=data.get("global_name") or data.get("username"),
            )
        elif provider.name == "feishu":
            # `data` already holds the full response body from the initial GET above.
            info = data.get("data", {})
            open_id = info.get("open_id", "")
            # Feishu may return enterprise_email or email; personal accounts may have neither.
            email = info.get("email") or info.get("enterprise_email") or None
            name = info.get("name") or info.get("en_name") or f"feishu_{open_id[:8]}"
            return OAuthUserInfo(
                provider="feishu",
                id=open_id,
                username=(info.get("en_name") or name).lower().replace(" ", "_"),
                email=email,
                display_name=info.get("name") or info.get("en_name"),
            )
        else:
            raise ValueError(f"Unknown provider: {provider.name}")
