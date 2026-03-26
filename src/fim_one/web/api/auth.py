"""Authentication endpoints: register, login, token refresh, and user info."""

from __future__ import annotations

from typing import Any

import asyncio
import os
import secrets
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, select, update

from fim_one.web.admin_notify import notify_admins
from fim_one.web.email import _smtp_configured, send_verification_email
from fim_one.web.exceptions import AppError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fim_one.db import get_session
from fim_one.web.api.admin import (
    SETTING_ANNOUNCEMENT_ENABLED,
    SETTING_ANNOUNCEMENT_TEXT,
    SETTING_REGISTRATION_ENABLED,
)
from fim_one.web.api.admin_utils import get_setting
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
    hash_password_async,
    hash_refresh_token,
    verify_password,
    verify_password_async,
)
from fim_one.web.models import LoginHistory, User
from fim_one.web.models.agent import Agent
from fim_one.web.models.api_key import ApiKey
from fim_one.web.models.connector import Connector
from fim_one.web.models.connector_call_log import ConnectorCallLog
from fim_one.web.models.conversation import Conversation
from fim_one.web.models.eval import EvalCase, EvalCaseResult, EvalDataset, EvalRun
from fim_one.web.models.knowledge_base import KnowledgeBase
from fim_one.web.models.model_config import ModelConfig
from fim_one.web.models.oauth_binding import UserOAuthBinding
from fim_one.web.models.organization import OrgMembership
from fim_one.web.models.resource_subscription import ResourceSubscription
from fim_one.web.models.skill import Skill
from fim_one.web.models.workflow import Workflow, WorkflowRun
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
from fim_one.web.platform import ensure_market_org

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
        privacy_accepted_at=getattr(user, "privacy_accepted_at", None),
        timezone=user.timezone,
        default_agent_id=user.default_agent_id,
        default_exec_mode=user.default_exec_mode,
        default_reasoning=user.default_reasoning,
        totp_enabled=user.totp_enabled,
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
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
        password_hash=await hash_password_async(body.password),
        email=body.email,
        is_admin=is_first_user_check,
        privacy_accepted_at=datetime.now(UTC) if body.privacy_accepted else None,
    )
    db.add(user)
    await db.flush()

    # Create Market org on first user registration (admin becomes owner)
    if is_first_user_check:
        await ensure_market_org(db, owner_id=user.id)

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = hash_refresh_token(refresh)
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Notify admins of new registration (fire-and-forget, skip for first user)
    if not is_first_user_check:
        asyncio.create_task(
            notify_admins(
                "new_user_registration",
                "New User Registered",
                [
                    f"Email: {body.email}",
                ],
                event_time=datetime.now(UTC),
            )
        )

    # Reload with oauth_bindings for response serialization
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


@router.post("/login", response_model=None)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse | JSONResponse:
    user = await db.scalar(
        select(User).options(selectinload(User.oauth_bindings)).where(User.email == body.email)
    )
    if user is None:
        await _record_login(db, request, None, success=False, failure_reason="user_not_found", email=body.email)
        raise AppError("invalid_credentials", status_code=401)

    if user.password_hash is None or not await verify_password_async(body.password, user.password_hash):
        await _record_login(db, request, user, success=False, failure_reason="wrong_password")
        raise AppError("invalid_credentials", status_code=401)

    if not user.is_active:
        await _record_login(db, request, user, success=False, failure_reason="account_disabled")
        raise AppError("account_disabled", status_code=403)

    # 2FA check: if TOTP is enabled, return a temp token instead of full auth
    if user.totp_enabled:
        temp_token = _create_2fa_temp_token(user.id)
        await _record_login(db, request, user, success=True, failure_reason="2fa_pending")
        return JSONResponse(content={"requires_2fa": True, "temp_token": temp_token})

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = hash_refresh_token(refresh)
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
) -> dict[str, Any]:
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

    user.refresh_token = hash_refresh_token(refresh)
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

    if user.refresh_token != hash_refresh_token(body.refresh_token):
        raise AppError("refresh_token_mismatch", status_code=401)

    if (
        user.refresh_token_expires_at is not None
        and user.refresh_token_expires_at.replace(tzinfo=UTC) < datetime.now(UTC)
    ):
        raise AppError("refresh_token_expired", status_code=401)

    # Token rotation: issue new pair
    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = hash_refresh_token(refresh)
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


def _dt(v: datetime | None) -> str | None:
    """Serialize a datetime to ISO 8601 string or None."""
    return v.isoformat() if v else None


@router.get("/me/export")
async def export_my_data(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """Export all user data as JSON (GDPR Article 15 & 20 — right of access & portability)."""
    uid = current_user.id

    # --- Profile ---
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == uid)
    )
    user = result.scalar_one()
    profile = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_admin": user.is_admin,
        "preferred_language": user.preferred_language,
        "onboarding_completed": user.onboarding_completed,
        "avatar": user.avatar,
        "oauth_provider": user.oauth_provider,
        "system_instructions": user.system_instructions,
        "privacy_accepted_at": _dt(getattr(user, "privacy_accepted_at", None)),
        "created_at": _dt(user.created_at),
        "updated_at": _dt(user.updated_at),
        "oauth_bindings": [
            {
                "provider": b.provider,
                "email": b.email,
                "display_name": b.display_name,
                "bound_at": _dt(b.bound_at),
            }
            for b in (user.oauth_bindings or [])
        ],
    }

    # --- Conversations + Messages ---
    conv_result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.user_id == uid)
    )
    conversations = []
    for conv in conv_result.scalars().all():
        conversations.append({
            "id": conv.id,
            "title": conv.title,
            "mode": conv.mode,
            "agent_id": conv.agent_id,
            "status": conv.status,
            "starred": conv.starred,
            "model_name": conv.model_name,
            "total_tokens": conv.total_tokens,
            "created_at": _dt(conv.created_at),
            "updated_at": _dt(conv.updated_at),
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "message_type": m.message_type,
                    "created_at": _dt(m.created_at),
                }
                for m in (conv.messages or [])
            ],
        })

    # --- Agents ---
    agent_result = await db.execute(select(Agent).where(Agent.user_id == uid))
    agents = [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "instructions": a.instructions,
            "execution_mode": a.execution_mode,
            "status": a.status,
            "icon": a.icon,
            "created_at": _dt(a.created_at),
            "updated_at": _dt(a.updated_at),
        }
        for a in agent_result.scalars().all()
    ]

    # --- Knowledge Bases ---
    kb_result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.user_id == uid))
    knowledge_bases = [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "chunk_strategy": kb.chunk_strategy,
            "chunk_size": kb.chunk_size,
            "document_count": kb.document_count,
            "status": kb.status,
            "created_at": _dt(kb.created_at),
            "updated_at": _dt(kb.updated_at),
        }
        for kb in kb_result.scalars().all()
    ]

    # --- Connectors (exclude credentials) ---
    conn_result = await db.execute(select(Connector).where(Connector.user_id == uid))
    connectors = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "type": c.type,
            "base_url": c.base_url,
            "auth_type": c.auth_type,
            "status": c.status,
            "created_at": _dt(c.created_at),
            "updated_at": _dt(c.updated_at),
        }
        for c in conn_result.scalars().all()
    ]

    # --- Model Configs (exclude api_key) ---
    mc_result = await db.execute(select(ModelConfig).where(ModelConfig.user_id == uid))
    model_configs = [
        {
            "id": mc.id,
            "name": mc.name,
            "provider": mc.provider,
            "model_name": mc.model_name,
            "base_url": mc.base_url,
            "category": mc.category,
            "role": mc.role,
            "is_default": mc.is_default,
            "created_at": _dt(mc.created_at),
            "updated_at": _dt(mc.updated_at),
        }
        for mc in mc_result.scalars().all()
    ]

    # --- Workflows + Runs ---
    wf_result = await db.execute(
        select(Workflow).options(selectinload(Workflow.runs)).where(Workflow.user_id == uid)
    )
    workflows = []
    for wf in wf_result.scalars().all():
        workflows.append({
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "status": wf.status,
            "created_at": _dt(wf.created_at),
            "updated_at": _dt(wf.updated_at),
            "runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "started_at": _dt(r.started_at),
                    "completed_at": _dt(r.completed_at),
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                    "created_at": _dt(r.created_at),
                }
                for r in (wf.runs or [])
            ],
        })

    # --- Skills ---
    skill_result = await db.execute(select(Skill).where(Skill.user_id == uid))
    skills = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "content": s.content,
            "status": s.status,
            "created_at": _dt(s.created_at),
            "updated_at": _dt(s.updated_at),
        }
        for s in skill_result.scalars().all()
    ]

    # --- API Keys (metadata only — exclude key_hash) ---
    ak_result = await db.execute(select(ApiKey).where(ApiKey.user_id == uid))
    api_keys = [
        {
            "id": ak.id,
            "name": ak.name,
            "key_prefix": ak.key_prefix,
            "scopes": ak.scopes,
            "is_active": ak.is_active,
            "expires_at": _dt(ak.expires_at),
            "last_used_at": _dt(ak.last_used_at),
            "total_requests": ak.total_requests,
            "created_at": _dt(ak.created_at),
        }
        for ak in ak_result.scalars().all()
    ]

    # --- Login History ---
    lh_result = await db.execute(select(LoginHistory).where(LoginHistory.user_id == uid))
    login_history = [
        {
            "id": lh.id,
            "ip_address": lh.ip_address,
            "user_agent": lh.user_agent,
            "success": lh.success,
            "failure_reason": lh.failure_reason,
            "created_at": _dt(lh.created_at),
        }
        for lh in lh_result.scalars().all()
    ]

    # --- Org Memberships ---
    om_result = await db.execute(select(OrgMembership).where(OrgMembership.user_id == uid))
    org_memberships = [
        {
            "id": om.id,
            "org_id": om.org_id,
            "role": om.role,
            "created_at": _dt(om.created_at),
        }
        for om in om_result.scalars().all()
    ]

    # --- Eval Data ---
    ed_result = await db.execute(select(EvalDataset).where(EvalDataset.user_id == uid))
    eval_datasets = [
        {
            "id": ed.id,
            "name": ed.name,
            "description": ed.description,
            "created_at": _dt(ed.created_at),
        }
        for ed in ed_result.scalars().all()
    ]

    ec_result = await db.execute(select(EvalCase).where(EvalCase.user_id == uid))
    eval_cases = [
        {
            "id": ec.id,
            "dataset_id": ec.dataset_id,
            "prompt": ec.prompt,
            "expected_behavior": ec.expected_behavior,
            "created_at": _dt(ec.created_at),
        }
        for ec in ec_result.scalars().all()
    ]

    er_result = await db.execute(select(EvalRun).where(EvalRun.user_id == uid))
    eval_runs = [
        {
            "id": er.id,
            "agent_id": er.agent_id,
            "dataset_id": er.dataset_id,
            "status": er.status,
            "total_cases": er.total_cases,
            "passed_cases": er.passed_cases,
            "failed_cases": er.failed_cases,
            "completed_at": _dt(er.completed_at),
            "created_at": _dt(er.created_at),
        }
        for er in er_result.scalars().all()
    ]

    # --- Assemble export ---
    export_data = {
        "export_date": datetime.now(UTC).isoformat(),
        "profile": profile,
        "conversations": conversations,
        "agents": agents,
        "knowledge_bases": knowledge_bases,
        "connectors": connectors,
        "model_configs": model_configs,
        "workflows": workflows,
        "skills": skills,
        "api_keys": api_keys,
        "login_history": login_history,
        "org_memberships": org_memberships,
        "eval_datasets": eval_datasets,
        "eval_cases": eval_cases,
        "eval_runs": eval_runs,
    }

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="fim-one-data-export-{today}.json"',
        },
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
    if body.preferred_language is not None:
        user.preferred_language = body.preferred_language
    if body.onboarding_completed is not None:
        user.onboarding_completed = body.onboarding_completed
    if body.avatar is not None:
        user.avatar = body.avatar or None
    if body.timezone is not None:
        user.timezone = body.timezone or None
    if body.default_agent_id is not None:
        user.default_agent_id = body.default_agent_id or None
    if body.default_exec_mode is not None:
        user.default_exec_mode = body.default_exec_mode or None
    if body.default_reasoning is not None:
        user.default_reasoning = body.default_reasoning
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
    if not await verify_password_async(body.current_password, user.password_hash):
        raise AppError("current_password_incorrect")
    user.password_hash = await hash_password_async(body.new_password)
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
    user.password_hash = await hash_password_async(body.new_password)
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
) -> dict[str, Any]:
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
    user.password_hash = await hash_password_async(body.new_password)
    await db.commit()

    return ApiResponse(data={"message": "Password reset successfully"})


@router.post("/send-forgot-code")
async def send_forgot_code(
    body: SendForgotCodeRequest,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
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
    if verif.verified_at is None or verif.verified_at.replace(tzinfo=UTC) < datetime.now(UTC) - timedelta(minutes=10):
        raise AppError("verification_code_expired")

    # Set new password
    user.password_hash = await hash_password_async(body.new_password)
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
    user_result = await db.execute(select(User).where(User.id == current_user.id))
    user = user_result.scalar_one()

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
    reload_result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = reload_result.scalar_one()
    return ApiResponse(data=_build_user_info(user).model_dump())


@router.get("/setup-status")
async def setup_status(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
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
        password_hash=await hash_password_async(body.password),
        email=body.email,
        is_admin=True,
    )
    db.add(user)
    await db.flush()

    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = hash_refresh_token(refresh)
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    # Reload with oauth_bindings for response serialization
    user_reload = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user.id)
    )
    user = user_reload.scalar_one()

    return _build_token_response(user, access, refresh)


@router.get("/announcement")
async def get_announcement(
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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

    # --- Explicit deletion of tables NOT covered by ORM cascade ---

    # 1. API keys (user_id nullable, no cascade)
    await db.execute(delete(ApiKey).where(ApiKey.user_id == user_id))

    # 2. Login history (user_id nullable, no cascade)
    await db.execute(delete(LoginHistory).where(LoginHistory.user_id == user_id))

    # 3. Eval data (FK to users, no cascade) — delete in order: case_results → runs → cases → datasets
    eval_run_ids_q = select(EvalRun.id).where(EvalRun.user_id == user_id)
    await db.execute(delete(EvalCaseResult).where(EvalCaseResult.run_id.in_(eval_run_ids_q)))
    await db.execute(delete(EvalRun).where(EvalRun.user_id == user_id))
    await db.execute(delete(EvalCase).where(EvalCase.user_id == user_id))
    await db.execute(delete(EvalDataset).where(EvalDataset.user_id == user_id))

    # 4. Connector call logs (user_id nullable, no cascade)
    await db.execute(delete(ConnectorCallLog).where(ConnectorCallLog.user_id == user_id))

    # 5. Resource subscriptions (user_id FK, no cascade)
    await db.execute(delete(ResourceSubscription).where(ResourceSubscription.user_id == user_id))

    # 6. Email verifications (matched by email, no FK to users)
    user_email = current_user.email
    await db.execute(delete(EmailVerification).where(EmailVerification.email == user_email))

    # 7. Org memberships (user_id FK, no cascade on user delete)
    await db.execute(delete(OrgMembership).where(OrgMembership.user_id == user_id))

    # 8. Anonymize audit_logs — preserve records but remove admin_id reference
    await db.execute(
        update(AuditLog)
        .where(AuditLog.admin_id == user_id)
        .values(admin_id=None)
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


# ---------------------------------------------------------------------------
# Two-Factor Authentication (TOTP)
# ---------------------------------------------------------------------------

import hashlib
import json

import jwt as _jwt
import pyotp

from fim_one.web.schemas.user_settings import (
    ChangeEmailConfirmBody,
    ChangeEmailRequestBody,
    TwoFactorBackupCodesRequest,
    TwoFactorBackupCodesResponse,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorEnableResponse,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
)

_2FA_TEMP_TOKEN_EXPIRE_MINUTES = 5


def _create_2fa_temp_token(user_id: str) -> str:
    """Create a short-lived JWT for 2FA verification step."""
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "purpose": "2fa_verify",
        "exp": now + timedelta(minutes=_2FA_TEMP_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    return _jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_2fa_temp_token(token: str) -> str:
    """Decode a 2FA temp token. Returns user_id or raises."""
    from fim_one.web.auth import ALGORITHM, SECRET_KEY

    try:
        payload = _jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except _jwt.ExpiredSignatureError:
        raise AppError("2fa_token_expired", status_code=401) from None
    except _jwt.InvalidTokenError:
        raise AppError("2fa_token_invalid", status_code=401) from None

    if payload.get("purpose") != "2fa_verify":
        raise AppError("2fa_token_invalid", status_code=401)
    user_id = payload.get("sub")
    if not user_id:
        raise AppError("2fa_token_invalid", status_code=401)
    return str(user_id)


def _generate_backup_codes(count: int = 10) -> list[str]:
    """Generate random 8-character alphanumeric backup codes."""
    return [secrets.token_hex(4) for _ in range(count)]


def _hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TwoFactorSetupResponse:
    """Generate a TOTP secret for 2FA setup. Does NOT enable 2FA yet."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    if user.totp_enabled:
        raise AppError("2fa_already_enabled")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    await db.commit()

    totp = pyotp.TOTP(secret)
    otpauth_uri = totp.provisioning_uri(
        name=user.email,
        issuer_name="FIM One",
    )

    return TwoFactorSetupResponse(secret=secret, otpauth_uri=otpauth_uri)


@router.post("/2fa/enable", response_model=TwoFactorEnableResponse)
async def enable_2fa(
    body: TwoFactorEnableRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TwoFactorEnableResponse:
    """Verify TOTP code and enable 2FA. Returns backup codes."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    if user.totp_enabled:
        raise AppError("2fa_already_enabled")

    if not user.totp_secret:
        raise AppError("2fa_not_setup", detail="Call POST /api/auth/2fa/setup first")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code):
        raise AppError("2fa_code_invalid")

    # Generate backup codes
    plain_codes = _generate_backup_codes(10)
    hashed_codes = [_hash_backup_code(c) for c in plain_codes]

    user.totp_enabled = True
    user.totp_backup_codes = json.dumps(hashed_codes)
    await db.commit()

    return TwoFactorEnableResponse(backup_codes=plain_codes)


@router.post("/2fa/disable", response_model=ApiResponse)
async def disable_2fa(
    body: TwoFactorDisableRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Disable 2FA. Requires password verification."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    if not user.totp_enabled:
        raise AppError("2fa_not_enabled")

    if user.password_hash is None or not await verify_password_async(body.password, user.password_hash):
        raise AppError("current_password_incorrect")

    user.totp_secret = None
    user.totp_enabled = False
    user.totp_backup_codes = None
    await db.commit()

    return ApiResponse(data={"message": "2FA disabled successfully"})


@router.post("/2fa/backup-codes", response_model=TwoFactorBackupCodesResponse)
async def regenerate_backup_codes(
    body: TwoFactorBackupCodesRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TwoFactorBackupCodesResponse:
    """Regenerate backup codes. Requires password verification."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    if not user.totp_enabled:
        raise AppError("2fa_not_enabled")

    if user.password_hash is None or not await verify_password_async(body.password, user.password_hash):
        raise AppError("current_password_incorrect")

    plain_codes = _generate_backup_codes(10)
    hashed_codes = [_hash_backup_code(c) for c in plain_codes]
    user.totp_backup_codes = json.dumps(hashed_codes)
    await db.commit()

    return TwoFactorBackupCodesResponse(backup_codes=plain_codes)


@router.post("/login/verify-2fa", response_model=TokenResponse)
async def verify_2fa_login(
    body: TwoFactorVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> TokenResponse:
    """Complete login with 2FA verification using TOTP code or backup code."""
    user_id = _decode_2fa_temp_token(body.temp_token)

    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError("user_not_found", status_code=401)

    if not user.is_active:
        raise AppError("account_disabled", status_code=403)

    if not user.totp_enabled or not user.totp_secret:
        raise AppError("2fa_not_enabled")

    # Try TOTP code first
    totp = pyotp.TOTP(user.totp_secret)
    code_valid = totp.verify(body.code)

    # If TOTP didn't match, try backup codes
    if not code_valid and user.totp_backup_codes:
        code_hash = _hash_backup_code(body.code)
        try:
            stored_codes: list[str] = json.loads(user.totp_backup_codes)
        except (json.JSONDecodeError, TypeError):
            stored_codes = []

        if code_hash in stored_codes:
            code_valid = True
            # Remove used backup code
            stored_codes.remove(code_hash)
            user.totp_backup_codes = json.dumps(stored_codes)

    if not code_valid:
        await _record_login(db, request, user, success=False, failure_reason="2fa_code_invalid")
        raise AppError("2fa_code_invalid", status_code=401)

    # Issue tokens (same as normal login)
    access = create_access_token(user.id, user.email)
    refresh = create_refresh_token(user.id, user.email)

    user.refresh_token = hash_refresh_token(refresh)
    user.refresh_token_expires_at = datetime.now(UTC) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    await db.commit()

    await _record_login(db, request, user, success=True)

    # Reload with oauth_bindings (commit expires loaded attributes)
    result = await db.execute(
        select(User).options(selectinload(User.oauth_bindings)).where(User.id == user_id)
    )
    user = result.scalar_one()

    return _build_token_response(user, access, refresh)


# ---------------------------------------------------------------------------
# Email Change
# ---------------------------------------------------------------------------


@router.post("/change-email/request", response_model=ApiResponse, deprecated=True)
async def request_email_change(
    body: ChangeEmailRequestBody,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Request an email change. Sends OTP to the new email address.

    Temporarily disabled due to OAuth binding safety concerns — changing email
    breaks the email-match constraint for re-binding OAuth providers.
    """
    raise AppError("feature_disabled", status_code=501)

    # Verify password
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    if user.password_hash is None or not await verify_password_async(body.password, user.password_hash):
        raise AppError("current_password_incorrect")

    # Check new_email not already taken
    existing = await db.execute(select(User).where(User.email == body.new_email))
    if existing.scalar_one_or_none() is not None:
        raise AppError("email_already_registered", status_code=409)

    # Rate limit
    recent = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.new_email,
            EmailVerification.purpose == "change_email",
            EmailVerification.created_at > datetime.now(UTC) - timedelta(seconds=VERIFICATION_CODE_RATE_LIMIT_SECONDS),
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    if recent.scalar_one_or_none() is not None:
        raise AppError("verification_rate_limited", status_code=429)

    code = f"{secrets.randbelow(1000000):06d}"
    verification = EmailVerification(
        email=body.new_email,
        code=code,
        purpose="change_email",
        expires_at=datetime.now(UTC) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES),
    )
    db.add(verification)
    await db.commit()

    await send_verification_email(body.new_email, code, purpose="change_email")

    return ApiResponse(data={
        "message": "Verification code sent to new email",
        "expires_in": VERIFICATION_CODE_EXPIRY_MINUTES * 60,
    })


@router.post("/change-email/confirm", response_model=ApiResponse, deprecated=True)
async def confirm_email_change(
    body: ChangeEmailConfirmBody,
    current_user: User = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_session),  # noqa: B008
) -> ApiResponse:
    """Confirm email change with the OTP code sent to the new email.

    Temporarily disabled — see request_email_change.
    """
    raise AppError("feature_disabled", status_code=501)
    # Find the latest unexpired, unverified code for the new email
    verif_result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == body.new_email,
            EmailVerification.purpose == "change_email",
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

    # Check the new email is still available (race condition guard)
    existing = await db.execute(
        select(User).where(User.email == body.new_email, User.id != current_user.id)
    )
    if existing.scalar_one_or_none() is not None:
        await db.commit()
        raise AppError("email_already_registered", status_code=409)

    # Update the user's email
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    user.email = body.new_email
    await db.commit()

    return ApiResponse(data={"message": "Email changed successfully"})
