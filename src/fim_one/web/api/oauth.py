"""OAuth authentication endpoints."""

from __future__ import annotations

from typing import Any

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db import get_session
from fim_one.web.exceptions import AppError
from fim_one.web.api.admin import SETTING_REGISTRATION_ENABLED, SETTING_REGISTRATION_MODE
from fim_one.web.api.admin_utils import get_setting
from fim_one.web.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from fim_one.web.models import User, UserOAuthBinding
from fim_one.web.oauth import (
    OAuthUserInfo,
    build_authorize_url,
    exchange_code,
    fetch_user_info,
    get_configured_providers,
    get_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oauth", tags=["oauth"])


def _get_callback_url(provider_name: str) -> str:
    """Build the OAuth callback URL."""
    base = os.environ.get("API_BASE_URL", "http://localhost:8000")
    return f"{base}/api/auth/oauth/{provider_name}/callback"


def _get_frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000")


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    """Return which OAuth providers are configured."""
    return {"providers": get_configured_providers()}


@router.get("/{provider_name}/authorize")
async def authorize(
    provider_name: str,
    action: str | None = None,
    token: str | None = None,
    ticket: str | None = None,
) -> RedirectResponse:
    """Redirect user to OAuth provider for authentication.

    Query params for bind flow:
    - action=bind — indicates this is an account-binding request
    - ticket — one-time bind ticket (preferred, from POST /api/auth/oauth/bind-ticket)
    - token — JWT access token (deprecated, still accepted for backward compat)
    """
    provider = get_provider(provider_name)
    if not provider:
        raise AppError(
            "oauth_provider_not_configured",
            status_code=400,
            detail=f"OAuth provider '{provider_name}' is not configured",
            detail_args={"provider": provider_name},
        )

    action_value = "login"
    user_id: str | None = None

    if action == "bind":
        # Prefer ticket over raw JWT token
        if ticket:
            from fim_one.web.auth import verify_bind_ticket
            user_id = verify_bind_ticket(ticket)
            if user_id is None:
                frontend_url = _get_frontend_url()
                return RedirectResponse(
                    url=f"{frontend_url}/settings?tab=account&bind_error=invalid_ticket",
                    status_code=302,
                )
        elif token:
            # Deprecated: raw JWT in query param
            logger.warning("OAuth bind using deprecated raw JWT token param — use ticket instead")
            try:
                payload = decode_token(token)
            except HTTPException:
                frontend_url = _get_frontend_url()
                return RedirectResponse(
                    url=f"{frontend_url}/settings?tab=account&bind_error=invalid_token",
                    status_code=302,
                )
            user_id = payload.get("sub")
        else:
            raise AppError("oauth_bind_credentials_required", status_code=400)

        if not user_id:
            raise AppError("oauth_bind_credentials_required", status_code=400)
        action_value = "bind"

    from fim_one.web.auth import create_oauth_state
    state = create_oauth_state(action=action_value, user_id=user_id)

    redirect_uri = _get_callback_url(provider_name)
    url = build_authorize_url(provider, state, redirect_uri)
    return RedirectResponse(url=url, status_code=302)


@router.get("/{provider_name}/callback")
async def callback(
    provider_name: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> RedirectResponse:
    """Handle OAuth callback from provider."""
    frontend_url = _get_frontend_url()

    # Handle provider-side errors
    if error:
        logger.warning("OAuth error from %s: %s", provider_name, error)
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")

    if not code or not state:
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")

    # Validate CSRF state (JWT-signed)
    from fim_one.web.auth import verify_oauth_state
    state_entry = verify_oauth_state(state)
    if not state_entry:
        logger.warning("Invalid or expired OAuth state")
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")

    # Helper: error redirect based on action (bind → settings, login → auth/callback)
    def _error_redirect(error_code: str = "oauth_failed") -> RedirectResponse:
        if state_entry["action"] == "bind":
            return RedirectResponse(
                url=f"{frontend_url}/settings?tab=account&bind_error={error_code}",
                status_code=302,
            )
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error={error_code}")

    provider = get_provider(provider_name)
    if not provider:
        return _error_redirect()

    try:
        # Exchange code for token
        redirect_uri = _get_callback_url(provider_name)
        access_token = await exchange_code(provider, code, redirect_uri)

        # Fetch user info
        user_info = await fetch_user_info(provider, access_token)
    except Exception:
        logger.exception("OAuth exchange/fetch failed for %s", provider_name)
        return _error_redirect()

    # ---- Branch on action ----
    if state_entry["action"] == "bind":
        return await _handle_bind(db, state_entry, user_info, provider_name, frontend_url)
    else:
        return await _handle_login(db, user_info, provider_name, frontend_url)


async def _handle_bind(
    db: AsyncSession,
    state_entry: dict[str, Any],
    user_info: OAuthUserInfo,
    provider_name: str,
    frontend_url: str,
) -> RedirectResponse:
    """Handle the OAuth bind flow: link a third-party account to the current user."""
    settings_url = f"{frontend_url}/settings?tab=account"

    # Load the current user by user_id stored in the state
    user_result = await db.execute(
        select(User).where(User.id == state_entry["sub"])
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        logger.warning("Bind flow: user_id %s not found", state_entry["sub"])
        return RedirectResponse(
            url=f"{settings_url}&bind_error=user_not_found", status_code=302
        )

    # Email matching: only enforced when BOTH sides have an email.
    # Some providers (e.g. Feishu personal accounts) return an empty email even
    # when the user has a login email — we rely on ticket-based auth instead.
    oauth_email = user_info.email
    if oauth_email and user.email:
        if oauth_email.lower() != user.email.lower():
            logger.warning(
                "Bind email mismatch: oauth=%s user=%s", oauth_email, user.email
            )
            return RedirectResponse(
                url=f"{settings_url}&bind_error=email_mismatch", status_code=302
            )

    # Check if a binding already exists for this (provider, oauth_id)
    existing_binding_result = await db.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.provider == user_info.provider,
            UserOAuthBinding.oauth_id == user_info.id,
        )
    )
    if existing_binding_result.scalar_one_or_none() is not None:
        return RedirectResponse(
            url=f"{settings_url}&bind_error=already_bound", status_code=302
        )

    # Check if this user already has a binding for this provider
    user_provider_result = await db.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.user_id == user.id,
            UserOAuthBinding.provider == user_info.provider,
        )
    )
    if user_provider_result.scalar_one_or_none() is not None:
        return RedirectResponse(
            url=f"{settings_url}&bind_error=already_connected", status_code=302
        )

    # Create the binding
    new_binding = UserOAuthBinding(
        user_id=user.id,
        provider=user_info.provider,
        oauth_id=user_info.id,
        email=user_info.email,
        display_name=user_info.display_name,
    )
    db.add(new_binding)

    # Keep legacy columns in sync if user has no oauth_provider yet
    if not user.oauth_provider:
        user.oauth_provider = user_info.provider
        user.oauth_id = user_info.id

    await db.commit()

    return RedirectResponse(
        url=f"{settings_url}&bind_success={provider_name}", status_code=302
    )


async def _handle_login(
    db: AsyncSession,
    user_info: OAuthUserInfo,
    provider_name: str,
    frontend_url: str,
) -> RedirectResponse:
    """Handle the standard OAuth login flow (existing logic, unchanged)."""
    # --- Find or create user via OAuth binding ---
    # 1. Look up by (provider, oauth_id) in bindings table
    binding_result = await db.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.provider == user_info.provider,
            UserOAuthBinding.oauth_id == user_info.id,
        )
    )
    binding = binding_result.scalar_one_or_none()

    if binding is not None:
        # Existing binding -> login as that user
        user_result = await db.execute(select(User).where(User.id == binding.user_id))
        user = user_result.scalar_one()
        # Update binding info if changed
        if user_info.email and binding.email != user_info.email:
            binding.email = user_info.email
        if user_info.display_name and binding.display_name != user_info.display_name:
            binding.display_name = user_info.display_name
    else:
        # 2. No binding found -- check if a local account with this email exists.
        #    If found, auto-bind: log in as that existing user.
        user = None
        if user_info.email:
            email_result = await db.execute(
                select(User).where(func.lower(User.email) == user_info.email.lower())
            )
            matched = email_result.scalar_one_or_none()
            if matched is not None:
                user = matched

        if user is None:
            # 3. No email match -- create new user, but check registration_mode first
            reg_mode = await get_setting(db, SETTING_REGISTRATION_MODE, default="")
            if not reg_mode:
                # Backward compat: fall back to legacy boolean
                reg_value = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
                reg_mode = "open" if reg_value.lower() != "false" else "disabled"

            if reg_mode in ("disabled", "invite"):
                # "invite" mode blocks OAuth new-user creation — no way to supply an invite
                # code through the OAuth redirect flow, so the only safe option is to block.
                logger.warning(
                    "OAuth new-user creation blocked: registration_mode=%s (provider=%s email=%s)",
                    reg_mode,
                    provider_name,
                    user_info.email,
                )
                return RedirectResponse(
                    url=f"{frontend_url}/auth/callback?error=registration_disabled",
                    status_code=302,
                )

            base_username = user_info.username or f"{provider_name}_user"
            username = base_username
            suffix = 0
            while True:
                existing = await db.execute(select(User).where(User.username == username))
                if existing.scalar_one_or_none() is None:
                    break
                suffix += 1
                username = f"{base_username}_{suffix}"

            user = User(
                username=username,
                display_name=user_info.display_name,
                password_hash=None,
                oauth_provider=user_info.provider,
                oauth_id=user_info.id,
                email=user_info.email,
            )
            db.add(user)
            await db.flush()

        # Create binding for this user
        new_binding = UserOAuthBinding(
            user_id=user.id,
            provider=user_info.provider,
            oauth_id=user_info.id,
            email=user_info.email,
            display_name=user_info.display_name,
        )
        db.add(new_binding)
        await db.flush()

    # Keep legacy columns in sync for backward compatibility
    assert user is not None  # guaranteed by above branches
    if not user.oauth_provider:
        user.oauth_provider = user_info.provider
        user.oauth_id = user_info.id
    # NOTE: We deliberately do NOT update user.email from the OAuth provider.
    # Silently overwriting the stored email whenever a provider reports a new
    # address would allow an attacker who changes their provider email to
    # overwrite another user's email address.

    # Issue JWT tokens
    jwt_access = create_access_token(user.id, user.email)
    jwt_refresh = create_refresh_token(user.id, user.email)
    user.refresh_token = jwt_refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    # Build user info JSON for frontend (URL-encoded)
    bindings_data = [
        {
            "provider": b.provider,
            "email": b.email,
            "display_name": b.display_name,
            "bound_at": b.bound_at.isoformat() if b.bound_at else None,
        }
        for b in (user.oauth_bindings or [])
    ]
    user_data = json.dumps({
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "oauth_provider": user.oauth_provider,
        "email": user.email,
        "has_password": user.password_hash is not None,
        "oauth_bindings": bindings_data,
    })

    # Use URL fragment (#) instead of query params (?) so tokens never
    # appear in server logs, nginx access logs, or browser history.
    redirect_url = (
        f"{frontend_url}/auth/callback"
        f"#access_token={jwt_access}"
        f"&refresh_token={jwt_refresh}"
        f"&user={quote(user_data)}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)


# ---------------------------------------------------------------------------
# Bind ticket endpoint — issue a short-lived one-time token for OAuth bind
# ---------------------------------------------------------------------------


@router.post("/bind-ticket")
async def issue_bind_ticket(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    """Generate a short-lived JWT bind ticket.

    The ticket is valid for 60 seconds and should be passed as the ``ticket``
    query parameter to ``/{provider}/authorize?action=bind&ticket=...``.
    """
    from fim_one.web.auth import create_bind_ticket
    return {"ticket": create_bind_ticket(str(current_user.id))}
