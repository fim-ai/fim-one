"""Authentication endpoints: register, login, token refresh, and user info."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update

from fim_agent.web.exceptions import AppError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_agent.db import get_session
from fim_agent.web.api.admin import (
    SETTING_ANNOUNCEMENT_ENABLED,
    SETTING_ANNOUNCEMENT_TEXT,
    SETTING_REGISTRATION_ENABLED,
    get_setting,
)
from fim_agent.web.models.invite_code import InviteCode
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


@router.get("/registration-status")
async def registration_status(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Public endpoint: returns registration mode and legacy enabled flag."""
    reg_mode = await get_setting(db, "registration_mode", "")
    if not reg_mode:
        value = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
        reg_mode = "open" if value.lower() != "false" else "disabled"
    return {
        "registration_enabled": reg_mode != "disabled",
        "registration_mode": reg_mode,
    }


@router.post("/register", response_model=TokenResponse)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    # Check if registration is enabled (first user is always allowed as bootstrap)
    user_count_check = await db.execute(select(func.count(User.id)))
    is_first_user_check = user_count_check.scalar_one() == 0
    if not is_first_user_check:
        # Try new registration_mode first, fall back to legacy registration_enabled
        reg_mode = await get_setting(db, "registration_mode", "")
        if not reg_mode:
            reg_enabled = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
            reg_mode = "open" if reg_enabled.lower() != "false" else "disabled"

        if reg_mode == "disabled":
            raise AppError("registration_disabled", status_code=403)
        elif reg_mode == "invite":
            if not body.invite_code:
                raise AppError("invite_code_required")
                # Atomic increment: the UPDATE only succeeds when the code exists,
            # is active, has not expired, and still has remaining uses.
            # This prevents the non-atomic read-modify-write race condition where
            # two concurrent requests both pass the use_count check before either
            # commits.
            now_utc = datetime.now(UTC)
            invite_result = await db.execute(
                update(InviteCode)
                .where(
                    InviteCode.code == body.invite_code,
                    InviteCode.is_active == True,  # noqa: E712
                    InviteCode.use_count < InviteCode.max_uses,
                    (InviteCode.expires_at == None)  # noqa: E711
                    | (InviteCode.expires_at > now_utc),
                )
                .values(use_count=InviteCode.use_count + 1)
                .returning(InviteCode)
            )
            invite = invite_result.scalar_one_or_none()
            if invite is None:
                # Distinguish the failure reason with a second read (read-only,
                # no TOCTOU risk here — we are only producing a helpful error
                # message after the atomic operation already rejected the request).
                diag_result = await db.execute(
                    select(InviteCode).where(InviteCode.code == body.invite_code)
                )
                diag = diag_result.scalar_one_or_none()
                if diag is None or not diag.is_active:
                    raise AppError("invite_code_invalid")
                elif diag.expires_at and diag.expires_at.replace(tzinfo=UTC) < now_utc:
                    raise AppError("invite_code_expired")
                else:
                    raise AppError("invite_code_exhausted")
            await db.flush()

    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none() is not None:
        raise AppError("username_taken", status_code=409)

    # Check email uniqueness
    email_result = await db.execute(
        select(User).where(User.email == body.email)
    )
    if email_result.scalar_one_or_none() is not None:
        raise AppError("email_already_registered", status_code=409)

    # First registered user automatically becomes admin
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
        is_admin=is_first_user_check,
    )
    db.add(user)
    await db.flush()

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
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
        raise AppError("invalid_credentials", status_code=401)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
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
        raise AppError("invalid_token_type", status_code=401)

    user_id = payload.get("sub")
    if not user_id:
        raise AppError("invalid_token_payload", status_code=401)

    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError("user_not_found", status_code=401)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    if user.refresh_token != body.refresh_token:
        raise AppError("refresh_token_mismatch", status_code=401)

    if (
        user.refresh_token_expires_at is not None
        and user.refresh_token_expires_at.replace(tzinfo=UTC) < datetime.now(UTC)
    ):
        raise AppError("refresh_token_expired", status_code=401)

    # Token rotation: issue new pair
    access = create_access_token(user.id, user.username)
    refresh = create_refresh_token(user.id, user.username)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
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
            raise AppError("email_empty")
        # Check email uniqueness (exclude current user)
        email_result = await db.execute(
            select(User).where(User.email == body.email, User.id != user.id)
        )
        if email_result.scalar_one_or_none() is not None:
            raise AppError("email_already_registered", status_code=409)
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
        raise AppError("oauth_password_change")
    if not verify_password(body.current_password, user.password_hash):
        raise AppError("current_password_incorrect")
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
        raise AppError("password_already_set")
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
        raise AppError(
            "oauth_binding_not_found",
            status_code=404,
            detail=f"No OAuth binding found for provider '{provider}'",
            detail_args={"provider": provider},
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
        raise AppError("cannot_unbind_only_login")

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
        raise AppError("system_already_initialized", status_code=403)

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
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.get("/announcement")
async def get_announcement(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Public endpoint: returns the current system announcement if enabled."""
    enabled = await get_setting(db, SETTING_ANNOUNCEMENT_ENABLED, default="false")
    text = await get_setting(db, SETTING_ANNOUNCEMENT_TEXT, default="")
    if enabled.lower() == "true" and text.strip():
        return {"enabled": True, "text": text}
    return {"enabled": False, "text": ""}
