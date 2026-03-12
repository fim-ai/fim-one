"""Authentication endpoints: register, login, token refresh, and user info."""

from __future__ import annotations

import os
import secrets
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update

from fim_one.web.email import _smtp_configured, send_verification_email
from fim_one.web.exceptions import AppError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db import get_session
from fim_one.web.api.admin import (
    SETTING_ANNOUNCEMENT_ENABLED,
    SETTING_ANNOUNCEMENT_TEXT,
    SETTING_REGISTRATION_ENABLED,
    get_setting,
)
from fim_one.web.models.audit_log import AuditLog
from fim_one.web.models.email_verification import EmailVerification
from fim_one.web.models.invite_code import InviteCode
from fim_one.web.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from fim_one.web.models import LoginHistory, User
from fim_one.web.models.agent import Agent
from fim_one.web.models.connector import Connector
from fim_one.web.models.conversation import Conversation
from fim_one.web.models.oauth_binding import UserOAuthBinding
from fim_one.web.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginWithCodeRequest,
    OAuthBindingInfo,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendForgotCodeRequest,
    SendLoginCodeRequest,
    SendResetCodeRequest,
    SendVerificationCodeRequest,
    SetPasswordRequest,
    SetupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserInfo,
    VerifyForgotCodeRequest,
)
from fim_one.web.schemas.common import ApiResponse
from fim_one.web.platform import ensure_platform_org, ensure_platform_membership

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _build_user_info(
    user: User,
    *,
    has_connector: bool = False,
    has_agent: bool = False,
    has_conversation: bool = False,
) -> UserInfo:
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
        onboarding_completed=user.onboarding_completed,
        avatar=user.avatar,
        has_connector=has_connector,
        has_agent=has_agent,
        has_conversation=has_conversation,
    )


def _build_token_response(user: User, access: str, refresh: str) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_build_user_info(user),
    )


async def _record_login(
    db: AsyncSession,
    request: Request,
    user: User | None,
    *,
    success: bool,
    failure_reason: str | None = None,
    email: str | None = None,
) -> None:
    """Record a login attempt in login_history for security auditing."""
    ip_address = request.client.host if request.client else None

    user_agent = request.headers.get("User-Agent", "") or None

    record = LoginHistory(
        user_id=user.id if user else None,
        username=user.username if user else None,
        email=user.email if user else email,
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        failure_reason=failure_reason,
    )
    db.add(record)
    await db.commit()


@router.get("/registration-status")
async def registration_status(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Public endpoint: returns registration mode and legacy enabled flag."""
    reg_mode = await get_setting(db, "registration_mode", "")
    if not reg_mode:
        value = await get_setting(db, SETTING_REGISTRATION_ENABLED, default="true")
        reg_mode = "open" if value.lower() != "false" else "disabled"
    email_verif = await get_setting(db, "email_verification_enabled", default="false")
    smtp_ok = _smtp_configured()
    return {
        "registration_enabled": reg_mode != "disabled",
        "registration_mode": reg_mode,
        "email_verification_enabled": email_verif.lower() == "true" and smtp_ok,
        "smtp_configured": smtp_ok,
    }


VERIFICATION_CODE_EXPIRY_MINUTES = 5
VERIFICATION_CODE_RATE_LIMIT_SECONDS = 60
VERIFICATION_MAX_ATTEMPTS = 5


@router.post("/send-verification-code")
async def send_verification_code(
    body: SendVerificationCodeRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Send a 6-digit verification code to the given email address."""
    if not _smtp_configured():
        raise AppError("smtp_not_configured", status_code=503)

    # Check email not already registered
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise AppError("email_already_registered", status_code=409)

    # Rate limit: no new code if one was sent < 60s ago for same email+purpose
    recent = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "register",
            EmailVerification.created_at > datetime.now(UTC) - timedelta(seconds=VERIFICATION_CODE_RATE_LIMIT_SECONDS),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise AppError("verification_rate_limited", status_code=429)

    code = f"{secrets.randbelow(1000000):06d}"
    verification = EmailVerification(
        email=body.email,
        code=code,
        purpose="register",
        expires_at=datetime.now(UTC) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
    )
    db.add(verification)
    await db.commit()

    await send_verification_email(body.email, code, locale=body.locale or "en")

    return {
        "message": "Verification code sent",
        "expires_in": VERIFICATION_CODE_EXPIRY_MINUTES * 60,
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

    # Check email uniqueness
    email_result = await db.execute(
        select(User).where(User.email == body.email)
    )
    if email_result.scalar_one_or_none() is not None:
        raise AppError("email_already_registered", status_code=409)

    # Validate email verification code if email verification is enabled AND SMTP is available
    email_verif_enabled = await get_setting(db, "email_verification_enabled", default="false")
    if email_verif_enabled.lower() == "true" and _smtp_configured() and not is_first_user_check:
        if not body.verification_code:
            raise AppError("email_verification_required")
        # Find the latest unexpired, unverified code for this email
        verif_result = await db.execute(
            select(EmailVerification)
            .where(
                EmailVerification.email == body.email,
                EmailVerification.purpose == "register",
                EmailVerification.verified_at == None,  # noqa: E711
                EmailVerification.expires_at > datetime.now(UTC),
            )
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
        verif = verif_result.scalar_one_or_none()
        if verif is None:
            raise AppError("verification_code_expired")
        if verif.attempts >= VERIFICATION_MAX_ATTEMPTS:
            raise AppError("verification_code_too_many_attempts")
        if verif.code != body.verification_code:
            verif.attempts += 1
            await db.commit()
            raise AppError("verification_code_invalid")
        # Mark as verified
        verif.verified_at = datetime.now(UTC)
        await db.flush()

    # First registered user automatically becomes admin
    user = User(
        password_hash=hash_password(body.password),
        email=body.email,
        is_admin=is_first_user_check,
    )
    db.add(user)
    await db.flush()

    # Enroll user in Platform org
    if is_first_user_check:
        await ensure_platform_org(db, owner_id=user.id)
    else:
        await ensure_platform_membership(db, user.id)

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

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
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    user = await db.scalar(
        select(User).options(selectinload(User.oauth_bindings)).where(User.email == body.email)
    )
    if user is None:
        await _record_login(db, request, None, success=False, failure_reason="user_not_found", email=body.email)
        raise AppError("invalid_credentials", status_code=401)

    if user.password_hash is None or not verify_password(body.password, user.password_hash):
        await _record_login(db, request, user, success=False, failure_reason="wrong_password")
        raise AppError("invalid_credentials", status_code=401)

    if not user.is_active:
        await _record_login(db, request, user, success=False, failure_reason="account_disabled")
        raise AppError("account_disabled", status_code=403)

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    await _record_login(db, request, user, success=True)

    # Reload with oauth_bindings (commit expires loaded attributes)
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.post("/send-login-code")
async def send_login_code(
    body: SendLoginCodeRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Send a 6-digit OTP code for passwordless login."""
    if not _smtp_configured():
        raise AppError("smtp_not_configured", status_code=503)

    # Email MUST be registered (opposite of send-verification-code)
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise AppError("email_not_registered", status_code=404)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    # Rate limit: no new code if one was sent < 60s ago for same email+purpose
    recent = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "login",
            EmailVerification.created_at > datetime.now(UTC) - timedelta(seconds=VERIFICATION_CODE_RATE_LIMIT_SECONDS),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise AppError("verification_rate_limited", status_code=429)

    code = f"{secrets.randbelow(1000000):06d}"
    verification = EmailVerification(
        email=body.email,
        code=code,
        purpose="login",
        expires_at=datetime.now(UTC) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
    )
    db.add(verification)
    await db.commit()

    await send_verification_email(body.email, code, purpose="login", locale=body.locale or "en")

    return {
        "message": "Login code sent",
        "expires_in": VERIFICATION_CODE_EXPIRY_MINUTES * 60,
    }


@router.post("/login-with-code", response_model=TokenResponse)
async def login_with_code(
    body: LoginWithCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Passwordless login using an email OTP code."""
    user = await db.scalar(
        select(User).options(selectinload(User.oauth_bindings)).where(User.email == body.email)
    )
    if user is None:
        await _record_login(db, request, None, success=False, failure_reason="email_not_registered", email=body.email)
        raise AppError("email_not_registered", status_code=404)

    if not user.is_active:
        await _record_login(db, request, user, success=False, failure_reason="account_disabled")
        raise AppError("account_disabled", status_code=403)

    # Find the latest unexpired, unverified code for this email+purpose
    verif_result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "login",
            EmailVerification.verified_at == None,  # noqa: E711
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    verif = verif_result.scalar_one_or_none()

    if verif is None:
        await _record_login(db, request, user, success=False, failure_reason="verification_code_expired")
        raise AppError("verification_code_expired")

    if verif.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        await _record_login(db, request, user, success=False, failure_reason="verification_code_expired")
        raise AppError("verification_code_expired")

    if verif.attempts >= VERIFICATION_MAX_ATTEMPTS:
        await _record_login(db, request, user, success=False, failure_reason="verification_code_too_many_attempts")
        raise AppError("verification_code_too_many_attempts")

    if verif.code != body.code:
        verif.attempts += 1
        await db.commit()
        await _record_login(db, request, user, success=False, failure_reason="verification_code_invalid")
        raise AppError("verification_code_invalid")

    # Mark as verified
    verif.verified_at = datetime.now(UTC)
    await db.flush()

    # Issue tokens (same pattern as /login)
    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = refresh
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    await _record_login(db, request, user, success=True)

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
    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

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

    # Check getting-started steps via lightweight EXISTS queries
    uid = current_user.id
    has_connector_row = await db.execute(select(Connector.id).where(Connector.user_id == uid).limit(1))
    has_agent_row = await db.execute(select(Agent.id).where(Agent.user_id == uid).limit(1))
    has_conversation_row = await db.execute(select(Conversation.id).where(Conversation.user_id == uid).limit(1))

    return ApiResponse(data=_build_user_info(
        user,
        has_connector=has_connector_row.first() is not None,
        has_agent=has_agent_row.first() is not None,
        has_conversation=has_conversation_row.first() is not None,
    ).model_dump())


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
    if body.onboarding_completed is not None:
        user.onboarding_completed = body.onboarding_completed
    if body.avatar is not None:
        user.avatar = body.avatar or None
    if body.username is not None:
        if not body.username or not body.username.strip():
            raise AppError("username_empty")
        new_username = body.username.strip()
        # Only apply cooldown & uniqueness check if username is actually changing
        if new_username != user.username:
            # First-time username setup (NULL) skips cooldown
            if user.username is not None and user.username_changed_at is not None:
                cooldown_days = 7
                cooldown_end = user.username_changed_at + timedelta(days=cooldown_days)
                now = datetime.now(UTC)
                # username_changed_at may be naive (UTC assumed) — compare accordingly
                naive_now = now.replace(tzinfo=None)
                if naive_now < cooldown_end:
                    remaining = (cooldown_end - naive_now).days + 1
                    raise AppError(
                        "username_cooldown",
                        detail=f"Username can only be changed once every {cooldown_days} days. "
                               f"Please wait {remaining} more day(s).",
                        detail_args={"days": remaining},
                    )
            # Check uniqueness (exclude current user)
            username_result = await db.execute(
                select(User).where(User.username == new_username, User.id != user.id)
            )
            if username_result.scalar_one_or_none() is not None:
                raise AppError("username_taken", status_code=409)
            user.username = new_username
            user.username_changed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


AVATAR_MAX_SIZE = 5 * 1024 * 1024  # 5MB
AVATAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@router.post("/avatar", response_model=ApiResponse)
async def upload_avatar(
    file: UploadFile = File(...),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Upload a custom avatar image."""
    # Validate file extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        raise AppError("avatar_invalid_format", status_code=400)

    # Read and validate size
    content = await file.read()
    if len(content) > AVATAR_MAX_SIZE:
        raise AppError("avatar_too_large", status_code=400)

    # Save to uploads/avatars/{user_id}{ext}
    uploads_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))
    avatar_dir = uploads_dir / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Remove old avatar file if exists (may have different extension/timestamp)
    for old_file in avatar_dir.glob(f"{current_user.id}_*"):
        old_file.unlink(missing_ok=True)
    # Also clean up legacy files without timestamp
    for old_file in avatar_dir.glob(f"{current_user.id}.*"):
        old_file.unlink(missing_ok=True)

    ts = int(datetime.now(UTC).timestamp())
    avatar_filename = f"{current_user.id}_{ts}{ext}"
    avatar_path = avatar_dir / avatar_filename
    avatar_path.write_bytes(content)

    # Update user record
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    user.avatar = f"avatars/{avatar_filename}"
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.delete("/avatar", response_model=ApiResponse)
async def delete_avatar(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Remove the current user's avatar."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    # Delete file if it's an uploaded avatar
    if user.avatar and not user.avatar.startswith("builtin:"):
        uploads_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))
        avatar_path = uploads_dir / user.avatar
        avatar_path.unlink(missing_ok=True)

    user.avatar = None
    await db.commit()

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.get("/avatar/{user_id}")
async def get_avatar(
    user_id: str,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> FileResponse:
    """Serve a user's uploaded avatar image (public, no auth required)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.avatar or user.avatar.startswith("builtin:"):
        raise AppError("avatar_not_found", status_code=404)

    uploads_dir = Path(os.environ.get("UPLOADS_DIR", "uploads"))
    avatar_path = (uploads_dir / user.avatar).resolve()
    if not avatar_path.is_relative_to(uploads_dir.resolve()):
        raise AppError("avatar_not_found", status_code=404)
    if not avatar_path.exists():
        raise AppError("avatar_not_found", status_code=404)

    return FileResponse(avatar_path)


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


@router.post("/send-reset-code")
async def send_reset_code(
    body: SendResetCodeRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Send a 6-digit OTP code for password reset (authenticated users only)."""
    if not _smtp_configured():
        raise AppError("smtp_not_configured", status_code=503)

    if not current_user.email:
        raise AppError("email_not_set", status_code=400)

    # Rate limit: no new code if one was sent < 60s ago for same email+purpose
    recent = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == current_user.email,
            EmailVerification.purpose == "reset_password",
            EmailVerification.created_at > datetime.now(UTC) - timedelta(seconds=VERIFICATION_CODE_RATE_LIMIT_SECONDS),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise AppError("verification_rate_limited", status_code=429)

    code = f"{secrets.randbelow(1000000):06d}"
    verification = EmailVerification(
        email=current_user.email,
        code=code,
        purpose="reset_password",
        expires_at=datetime.now(UTC) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
    )
    db.add(verification)
    await db.commit()

    await send_verification_email(current_user.email, code, purpose="reset_password", locale=body.locale or "en")

    return {
        "message": "Reset code sent",
        "expires_in": VERIFICATION_CODE_EXPIRY_MINUTES * 60,
    }


@router.post("/reset-password", response_model=ApiResponse)
async def reset_password(
    body: ResetPasswordRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Reset password using OTP verification (authenticated users who forgot their password)."""
    if not current_user.email:
        raise AppError("email_not_set", status_code=400)

    # Find the latest unexpired, unverified code for this email+purpose
    verif_result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == current_user.email,
            EmailVerification.purpose == "reset_password",
            EmailVerification.verified_at == None,  # noqa: E711
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    verif = verif_result.scalar_one_or_none()

    if verif is None:
        raise AppError("verification_code_expired")

    if verif.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise AppError("verification_code_expired")

    if verif.attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise AppError("verification_code_too_many_attempts")

    if verif.code != body.code:
        verif.attempts += 1
        await db.commit()
        raise AppError("verification_code_invalid")

    # Mark as verified
    verif.verified_at = datetime.now(UTC)
    await db.flush()

    # Set new password
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    user.password_hash = hash_password(body.new_password)
    await db.commit()

    return ApiResponse(data={"message": "Password reset successfully"})


@router.post("/send-forgot-code")
async def send_forgot_code(
    body: SendForgotCodeRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Send a 6-digit OTP for password reset (unauthenticated — login page)."""
    if not _smtp_configured():
        raise AppError("smtp_not_configured", status_code=503)

    # Email must belong to an existing user
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise AppError("email_not_registered", status_code=404)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    # Rate limit: no new code if one was sent < 60s ago
    recent = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "reset_password",
            EmailVerification.created_at > datetime.now(UTC) - timedelta(seconds=VERIFICATION_CODE_RATE_LIMIT_SECONDS),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise AppError("verification_rate_limited", status_code=429)

    code = f"{secrets.randbelow(1000000):06d}"
    verification = EmailVerification(
        email=body.email,
        code=code,
        purpose="reset_password",
        expires_at=datetime.now(UTC) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
    )
    db.add(verification)
    await db.commit()

    await send_verification_email(body.email, code, purpose="reset_password", locale=body.locale or "en")

    return {
        "message": "Reset code sent",
        "expires_in": VERIFICATION_CODE_EXPIRY_MINUTES * 60,
    }


@router.post("/verify-forgot-code", response_model=ApiResponse)
async def verify_forgot_code(
    body: VerifyForgotCodeRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Verify OTP code for forgot-password flow. Returns a reset_token for the next step."""
    # Verify user exists
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise AppError("email_not_registered", status_code=404)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    # Find the latest unverified code for this email+purpose
    verif_result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "reset_password",
            EmailVerification.verified_at == None,  # noqa: E711
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    verif = verif_result.scalar_one_or_none()

    if verif is None:
        raise AppError("verification_code_expired")

    if verif.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise AppError("verification_code_expired")

    if verif.attempts >= VERIFICATION_MAX_ATTEMPTS:
        raise AppError("verification_code_too_many_attempts")

    if verif.code != body.code:
        verif.attempts += 1
        await db.commit()
        raise AppError("verification_code_invalid")

    # Mark as verified and generate reset token
    verif.verified_at = datetime.now(UTC)
    verif.reset_token = str(uuid.uuid4())
    await db.commit()

    return ApiResponse(data={"reset_token": verif.reset_token})


@router.post("/forgot-password", response_model=ApiResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Reset password using a verified reset token (from verify-forgot-code)."""
    # Verify user exists
    user = await db.scalar(select(User).where(User.email == body.email))
    if user is None:
        raise AppError("email_not_registered", status_code=404)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    # Find the verified record with matching reset_token
    verif_result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.email,
            EmailVerification.purpose == "reset_password",
            EmailVerification.verified_at != None,  # noqa: E711
            EmailVerification.reset_token == body.reset_token,
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    verif = verif_result.scalar_one_or_none()

    if verif is None:
        raise AppError("verification_code_expired")

    # Check the verification is still recent (within 10 minutes of verification)
    if verif.verified_at.replace(tzinfo=UTC) < datetime.now(UTC) - timedelta(minutes=10):
        raise AppError("verification_code_expired")

    # Set new password
    user.password_hash = hash_password(body.new_password)
    # Clear the reset token so it can't be reused
    verif.reset_token = None
    await db.commit()

    return ApiResponse(data={"message": "Password reset successfully"})


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
        password_hash=hash_password(body.password),
        email=body.email,
        is_admin=True,
    )
    db.add(user)
    await db.flush()

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

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


# ---------------------------------------------------------------------------
# Self account deletion
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONVERSATIONS_DIR = _PROJECT_ROOT.parent / "data" / "sandbox"
_uploads_base = Path(os.environ.get("UPLOADS_DIR", "uploads"))
_UPLOADS_BASE = _uploads_base if _uploads_base.is_absolute() else _PROJECT_ROOT / _uploads_base
_UPLOADS_CONVERSATIONS_DIR = _UPLOADS_BASE / "conversations"


@router.delete("/account")
async def delete_own_account(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    """Permanently delete the current user's own account and all associated data."""
    user_id = current_user.id
    label = current_user.username or current_user.email

    # --- Clean up file-system resources before DB delete ---
    conv_result = await db.execute(
        select(Conversation.id).where(Conversation.user_id == user_id)
    )
    conv_ids = [row[0] for row in conv_result.fetchall()]

    for conv_id in conv_ids:
        sandbox_dir = _CONVERSATIONS_DIR / conv_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        uploads_dir = _UPLOADS_CONVERSATIONS_DIR / conv_id
        if uploads_dir.exists():
            shutil.rmtree(uploads_dir, ignore_errors=True)

    user_uploads = _UPLOADS_BASE / f"user_{user_id}"
    if user_uploads.exists():
        shutil.rmtree(user_uploads, ignore_errors=True)

    # --- Audit trail (must happen before DB delete since user is the actor) ---
    audit_detail = f"user self-deleted; cleaned {len(conv_ids)} conversations, sandbox & upload dirs"
    db.add(
        AuditLog(
            admin_id=user_id,
            admin_username=label,
            action="account.self_delete",
            target_type="user",
            target_id=user_id,
            target_label=label,
            detail=audit_detail,
        )
    )

    # --- DB delete (eagerly load all relationships for cascade) ---
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.conversations).selectinload(Conversation.messages),
            selectinload(User.agents),
            selectinload(User.knowledge_bases),
            selectinload(User.model_configs),
            selectinload(User.connectors),
            selectinload(User.mcp_servers),
            selectinload(User.oauth_bindings),
        )
        .where(User.id == user_id)
    )
    user_obj = result.scalar_one()
    await db.delete(user_obj)
    await db.commit()

    return {"deleted": True}
