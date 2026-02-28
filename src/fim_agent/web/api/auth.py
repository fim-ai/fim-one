"""Authentication endpoints: register, login, token refresh, and user info."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from fim_agent.web.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserInfo,
)
from fim_agent.web.schemas.common import ApiResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _build_token_response(user: User, access: str, refresh: str) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserInfo(id=user.id, username=user.username, display_name=user.display_name, is_admin=user.is_admin, system_instructions=user.system_instructions),
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

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
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

    return _build_token_response(user, access, refresh)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.utcnow() + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

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

    result = await db.execute(select(User).where(User.id == user_id))
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

    return _build_token_response(user, access, refresh)


@router.get("/me", response_model=ApiResponse)
async def me(
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> ApiResponse:
    return ApiResponse(
        data=UserInfo(
            id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            is_admin=current_user.is_admin,
            system_instructions=current_user.system_instructions,
        ).model_dump()
    )


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
    await db.commit()
    await db.refresh(user)
    return ApiResponse(
        data=UserInfo(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            is_admin=user.is_admin,
            system_instructions=user.system_instructions,
        ).model_dump()
    )


@router.post("/change-password", response_model=ApiResponse)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.password_hash = hash_password(body.new_password)
    await db.commit()
    return ApiResponse(data={"message": "Password changed successfully"})
