"""Authentication endpoints: register, login, token refresh, and user info."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from fim_agent.web.models import User
from fim_agent.web.models.oauth_binding import UserOAuthBinding
from fim_agent.web.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    OAuthBindingInfo,
    RefreshRequest,
    RegisterRequest,
    SetPasswordRequest,
    SetupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserInfo,
)
from fim_agent.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _build_user_info(user: User) -> UserInfo:
    """Build a UserInfo schema from a User ORM instance, including oauth_bindings."""
    bindings = [
        OAuthBindingInfo(
            provider=b.provider,
            email=b.email,
            display_name=b.display_name,
            bound_at=b.bound_at,
        )
        for b in (user.oauth_bindings or [])
    ]
    return UserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_admin=user.is_admin,
        system_instructions=user.system_instructions,
        preferred_language=user.preferred_language,
        oauth_provider=user.oauth_provider,
        email=user.email,
        has_password=user.password_hash is not None,
        oauth_bindings=bindings,
    )


def _build_token_response(user: User, access: str, refresh: str) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_build_user_info(user),
    )


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Check email uniqueness
    email_result = await db.execute(
        select(User).where(User.email == body.email)
    )
    if email_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
    )
    db.add(user)
    await db.flush()

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    if body.email:
        user = await db.scalar(
            select(User).options(selectinload(User.oauth_bindings)).where(User.email == body.email)
        )
    else:
        user = await db.scalar(
            select(User).options(selectinload(User.oauth_bindings)).where(User.username == body.username)
        )
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings (commit expires loaded attributes)
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.refresh_token != body.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token does not match",
        )

    if (
        user.refresh_token_expires_at is not None
        and user.refresh_token_expires_at < datetime.utcnow()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
        )

    # Token rotation: issue new pair
    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings (commit expires loaded attributes)
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.get("/me", response_model=ApiResponse)
async def me(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    # Reload user with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == current_user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.patch("/profile", response_model=ApiResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    if body.display_name is not None:
        user.display_name = body.display_name or None
    if body.system_instructions is not None:
        user.system_instructions = body.system_instructions or None
    if body.preferred_language is not None:
        user.preferred_language = body.preferred_language
    if body.email is not None:
        if not body.email or not body.email.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email cannot be empty",
            )
        # Check email uniqueness (exclude current user)
        email_result = await db.execute(
            select(User).where(User.email == body.email, User.id != user.id)
        )
        if email_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
        user.email = body.email
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.post("/change-password", response_model=ApiResponse)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    if user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for OAuth accounts",
        )
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return ApiResponse(data={"message": "Password changed successfully"})


@router.post("/set-password", response_model=ApiResponse)
async def set_password(
    body: SetPasswordRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Allow OAuth-only users to set an initial password."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    if user.password_hash is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password already set. Use change-password instead.",
        )
    user.password_hash = hash_password(body.new_password)
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.delete("/oauth-bindings/{provider}", response_model=ApiResponse)
async def unbind_oauth(
    provider: str,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Unbind an OAuth provider from the current user."""
    # Find the binding for this user + provider
    result = await db.execute(
        select(UserOAuthBinding).where(
            UserOAuthBinding.user_id == current_user.id,
            UserOAuthBinding.provider == provider,
        )
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OAuth binding found for provider '{provider}'",
        )

    # Safety check: prevent unbinding if it's the user's only login method
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    binding_count_result = await db.execute(
        select(func.count())
        .select_from(UserOAuthBinding)
        .where(UserOAuthBinding.user_id == user.id)
    )
    binding_count = binding_count_result.scalar_one()

    has_password = user.password_hash is not None
    if not has_password and binding_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unbind the only login method. Set a password first.",
        )

    # Delete the binding
    await db.delete(binding)

    # Clear legacy columns if the unbound provider matches
    if user.oauth_provider == provider:
        user.oauth_provider = None
        user.oauth_id = None

    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.get("/setup-status")
async def setup_status(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Check whether the system has been initialized (any users exist)."""
    result = await db.execute(select(func.count(User.id)))
    count = result.scalar_one()
    return {"initialized": count > 0}


@router.post("/setup", response_model=TokenResponse)
async def setup(
    body: SetupRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """First-time admin setup. Only works when no users exist yet."""
    result = await db.execute(select(func.count(User.id)))
    count = result.scalar_one()
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System already initialized",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
        is_admin=True,
    )
    db.add(user)
    await db.flush()

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)
