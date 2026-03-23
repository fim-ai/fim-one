"""JWT authentication, API key authentication, and bcrypt password utilities."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models.organization import OrgMembership

import asyncio

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from fim_one.db import get_session

from .models.api_key import ApiKey
from .models.user import User

logger = logging.getLogger(__name__)

def _resolve_secret_key() -> str:
    """Return the JWT secret key, auto-generating and persisting one if needed."""
    env_val = os.environ.get("JWT_SECRET_KEY", "")
    if env_val:
        return env_val

    # Auto-generate a secure secret and persist it to .env
    generated = secrets.token_hex(32)

    # Locate project root .env
    # auth.py is at src/fim_one/web/auth.py, so:
    #   parents[0] = src/fim_one/web/
    #   parents[1] = src/fim_one/
    #   parents[2] = src/
    #   parents[3] = project root (fim-one/)
    project_root = Path(__file__).resolve().parents[3]
    env_file = project_root / ".env"

    try:
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
        else:
            content = ""

        new_line = f"JWT_SECRET_KEY={generated}"
        new_content, n = re.subn(
            r'^#?\s*JWT_SECRET_KEY=.*$',
            new_line,
            content,
            flags=re.MULTILINE,
        )
        if n > 0:
            updated = new_content
        else:
            updated = content.rstrip("\n") + ("\n" if content else "") + new_line + "\n"

        env_file.write_text(updated, encoding="utf-8")
        logger.info(
            "JWT_SECRET_KEY was not set — auto-generated a secure secret and persisted it to %s. "
            "Set JWT_SECRET_KEY explicitly in your .env for production deployments.",
            env_file,
        )
    except OSError as exc:
        logger.warning(
            "JWT_SECRET_KEY was not set and could not write to %s (%s). "
            "A temporary in-memory secret will be used — tokens will be invalidated on restart.",
            env_file,
            exc,
        )

    # Also inject into the current process so submodules reading os.environ get the same value
    os.environ["JWT_SECRET_KEY"] = generated
    return generated


SECRET_KEY = _resolve_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 120
REFRESH_TOKEN_EXPIRE_DAYS = 7

_bearer_scheme = HTTPBearer()
_bearer_scheme_optional = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


async def hash_password_async(password: str) -> str:
    """Async wrapper for bcrypt hash — avoids blocking the event loop (~200ms)."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    """Async wrapper for bcrypt verify — avoids blocking the event loop (~200ms)."""
    return await asyncio.to_thread(verify_password, password, hashed)


def hash_refresh_token(token: str) -> str:
    """Return SHA-256 hex digest of a refresh token for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": expires,
        "iat": now,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, email: str) -> str:
    expires = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "email": email,
        "type": "refresh",
        "exp": expires,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ---------------------------------------------------------------------------
# Stateless tickets (multi-worker safe)
# ---------------------------------------------------------------------------


def create_sse_ticket(user_id: str, ttl: int = 60) -> str:
    """Create a short-lived JWT ticket for SSE authentication."""
    now = datetime.now(UTC)
    payload = {"sub": user_id, "type": "sse_ticket",
               "exp": now + timedelta(seconds=ttl), "iat": now}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_sse_ticket(token: str) -> str:
    """Verify an SSE ticket JWT. Returns user_id or raises."""
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    if payload.get("type") != "sse_ticket":
        raise jwt.InvalidTokenError("wrong token type")
    return str(payload["sub"])


def create_oauth_state(action: str, user_id: str | None, ttl: int = 300) -> str:
    """Create a JWT-signed OAuth CSRF state token."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {"type": "oauth_state", "action": action,
                     "exp": now + timedelta(seconds=ttl), "iat": now}
    if user_id is not None:
        payload["sub"] = user_id
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_oauth_state(token: str) -> dict[str, Any] | None:
    """Verify an OAuth state JWT. Returns payload dict or None on failure."""
    try:
        p = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return p if p.get("type") == "oauth_state" else None
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def create_bind_ticket(user_id: str, ttl: int = 60) -> str:
    """Create a short-lived JWT ticket for OAuth bind flow."""
    now = datetime.now(UTC)
    payload = {"sub": user_id, "type": "bind_ticket",
               "exp": now + timedelta(seconds=ttl), "iat": now}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_bind_ticket(token: str) -> str | None:
    """Verify a bind ticket JWT. Returns user_id or None on failure."""
    try:
        p = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return p["sub"] if p.get("type") == "bind_ticket" else None
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------


async def _authenticate_api_key(raw_key: str, db: AsyncSession) -> User:
    """Authenticate a request using an API key (``fim_``-prefixed Bearer token).

    Validates the key, checks expiry and active status, updates usage stats,
    and returns the associated :class:`User` with a transient
    ``_api_key_scopes`` attribute attached.

    Raises :class:`HTTPException` (401/403) on any failure.
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is disabled",
        )

    if api_key.expires_at is not None:
        expires = api_key.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
            )

    if api_key.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="System API keys cannot be used for user authentication",
        )

    # Fetch the associated user
    user_result = await db.execute(select(User).where(User.id == api_key.user_id))
    user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    # Update usage stats with a direct UPDATE (avoids ORM load overhead)
    await db.execute(
        sa_update(ApiKey)
        .where(ApiKey.id == api_key.id)
        .values(last_used_at=datetime.now(UTC), total_requests=ApiKey.total_requests + 1)
    )
    await db.flush()

    # Attach scopes as a transient attribute (None = unrestricted)
    user._api_key_scopes = (  # type: ignore[attr-defined]
        set(api_key.scopes.split(",")) if api_key.scopes else None
    )
    return user


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> User:
    token = credentials.credentials

    # API key authentication (fim_-prefixed tokens)
    if token.startswith("fim_"):
        return await _authenticate_api_key(token, db)

    # JWT authentication
    payload = decode_token(token)
    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )
    # Check if this token was issued before a force-logout event
    if user.tokens_invalidated_at is not None:
        iat = payload.get("iat")
        if iat is None:
            # Token predates the iat claim — can't verify it post-dates invalidation, reject it
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated",
            )
        token_issued = datetime.fromtimestamp(iat, tz=UTC) if isinstance(iat, (int, float)) else iat
        if token_issued <= user.tokens_invalidated_at.replace(tzinfo=UTC):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated",
            )
    # JWT users are unrestricted (no scope limitations)
    user._api_key_scopes = None  # type: ignore[attr-defined]
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(  # noqa: B008
        _bearer_scheme_optional
    ),
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> User | None:
    if credentials is None:
        return None

    token = credentials.credentials

    # API key authentication (fim_-prefixed tokens)
    if token.startswith("fim_"):
        try:
            return await _authenticate_api_key(token, db)
        except HTTPException:
            return None

    # JWT authentication
    try:
        payload = decode_token(token)
    except HTTPException:
        return None
    user_id: str | None = payload.get("sub")
    if user_id is None:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is not None and not user.is_active:
        return None
    # Check if this token was issued before a force-logout event
    if user is not None and user.tokens_invalidated_at is not None:
        iat = payload.get("iat")
        if iat is None:
            return None
        token_issued = datetime.fromtimestamp(iat, tz=UTC) if isinstance(iat, (int, float)) else iat
        if token_issued <= user.tokens_invalidated_at.replace(tzinfo=UTC):
            return None
    if user is not None:
        user._api_key_scopes = None  # type: ignore[attr-defined]
    return user


async def get_current_admin(
    user: User = Depends(get_current_user),  # noqa: B008
) -> User:
    """Require the authenticated user to be an admin (is_admin=True).
    Raises 403 Forbidden if not admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# ---------------------------------------------------------------------------
# Organization authorization helpers
# ---------------------------------------------------------------------------


async def get_user_org_ids(user_id: str, db: AsyncSession) -> list[str]:
    """All org IDs the user belongs to."""
    from .models.organization import OrgMembership

    result = await db.execute(
        select(OrgMembership.org_id).where(OrgMembership.user_id == user_id)
    )
    return list(result.scalars().all())


async def require_org_member(
    org_id: str, user: User, db: AsyncSession
) -> "OrgMembership":
    """Return membership or raise 403."""
    from .models.organization import OrgMembership

    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )
    return membership


async def require_org_admin(
    org_id: str, user: User, db: AsyncSession
) -> "OrgMembership":
    """Require admin or owner role. Returns membership or raises 403."""
    membership = await require_org_member(org_id, user, db)
    if membership.role not in ("admin", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin access required",
        )
    return membership


async def require_org_owner(
    org_id: str, user: User, db: AsyncSession
) -> "OrgMembership":
    """Require owner role. Returns membership or raises 403."""
    membership = await require_org_member(org_id, user, db)
    if membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization owner access required",
        )
    return membership


# ---------------------------------------------------------------------------
# Scope-based authorization for API keys
# ---------------------------------------------------------------------------


def require_scope(scope: str) -> Any:
    """Dependency factory: rejects API key requests missing the given scope."""

    async def _check(user: User = Depends(get_current_user)) -> User:  # noqa: B008
        scopes = getattr(user, "_api_key_scopes", None)
        if scopes is not None and scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scope: {scope}",
            )
        return user

    return _check
