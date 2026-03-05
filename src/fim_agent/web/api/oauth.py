"""OAuth authentication endpoints."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.api.admin import SETTING_REGISTRATION_ENABLED, get_setting
from fim_agent.web.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from fim_agent.web.models import User, UserOAuthBinding
from fim_agent.web.oauth import (
    build_authorize_url,
    exchange_code,
    fetch_user_info,
    get_configured_providers,
    get_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oauth", tags=["oauth"])

# In-memory CSRF state store
# Each entry: {"expiry": float, "action": "login"|"bind", "user_id": str|None}
_oauth_states: dict[str, dict] = {}
_STATE_TTL = 300  # 5 minutes


def _cleanup_expired_states() -> None:
    now = time.time()
    expired = [s for s, entry in _oauth_states.items() if entry["expiry"] < now]
    for s in expired:
        del _oauth_states[s]


def _get_callback_url(provider_name: str) -> str:
    """Build the OAuth callback URL."""
    base = os.environ.get("API_BASE_URL", "http://localhost:8000")
    return f"{base}/api/auth/oauth/{provider_name}/callback"


def _get_frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000")


@router.get("/providers")
async def list_providers():
    """Return which OAuth providers are configured."""
    return {"providers": get_configured_providers()}


@router.get("/{provider_name}/authorize")
async def authorize(
    provider_name: str,
    action: str | None = None,
    token: str | None = None,
):
    """Redirect user to OAuth provider for authentication.

    Query params for bind flow:
    - action=bind — indicates this is an account-binding request
    - token — JWT access token of the current user (needed because this
      is a browser redirect and can't use the Authorization header)
    """
    provider = get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not configured")

    _cleanup_expired_states()
    state = secrets.token_urlsafe(32)

    state_entry: dict = {
        "expiry": time.time() + _STATE_TTL,
        "action": "login",
        "user_id": None,
    }

    if action == "bind":
        if not token:
            raise HTTPException(status_code=400, detail="Token required for bind action")
        try:
            payload = decode_token(token)
        except HTTPException:
            frontend_url = _get_frontend_url()
            return RedirectResponse(
                url=f"{frontend_url}/settings?tab=account&bind_error=invalid_token",
                status_code=302,
            )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid token: missing user id")
        state_entry["action"] = "bind"
        state_entry["user_id"] = user_id

    _oauth_states[state] = state_entry

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
):
    """Handle OAuth callback from provider."""
    frontend_url = _get_frontend_url()

    # Handle provider-side errors
    if error:
        logger.warning("OAuth error from %s: %s", provider_name, error)
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")

    if not code or not state:
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")

    # Validate CSRF state
    _cleanup_expired_states()
    if state not in _oauth_states:
        logger.warning("Invalid or expired OAuth state")
        return RedirectResponse(url=f"{frontend_url}/auth/callback?error=oauth_failed")
    state_entry = _oauth_states.pop(state)

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
    state_entry: dict,
    user_info,
    provider_name: str,
    frontend_url: str,
) -> RedirectResponse:
    """Handle the OAuth bind flow: link a third-party account to the current user."""
    settings_url = f"{frontend_url}/settings?tab=account"

    # Load the current user by user_id stored in the state
    user_result = await db.execute(
        select(User).where(User.id == state_entry["user_id"])
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        logger.warning("Bind flow: user_id %s not found", state_entry["user_id"])
        return RedirectResponse(
            url=f"{settings_url}&bind_error=user_not_found", status_code=302
        )

    # Email matching: OAuth provider email MUST match the current user's email
    oauth_email = user_info.email
    if not oauth_email or oauth_email.lower() != (user.email or "").lower():
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
    user_info,
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
        # 2. No binding found -- try to match by email
        user = None
        if user_info.email:
            email_result = await db.execute(
                select(User).where(User.email == user_info.email)
            )
            user = email_result.scalar_one_or_none()

        if user is None:
            # 3. No email match -- create new user, but check registration setting first
            reg_value = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
            if reg_value.lower() == "false":
                logger.warning(
                    "OAuth new-user creation blocked: registration disabled (provider=%s email=%s)",
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
    if not user.oauth_provider:
        user.oauth_provider = user_info.provider
        user.oauth_id = user_info.id
    # Update email if changed
    if user_info.email and user.email != user_info.email:
        user.email = user_info.email

    # Issue JWT tokens
    jwt_access = create_access_token(user.id, user.username)
    jwt_refresh = create_refresh_token(user.id, user.username)
    user.refresh_token = jwt_refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
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

    redirect_url = (
        f"{frontend_url}/auth/callback"
        f"?access_token={jwt_access}"
        f"&refresh_token={jwt_refresh}"
        f"&user={quote(user_data)}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)
