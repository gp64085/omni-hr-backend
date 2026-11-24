import asyncio
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.services.email_service import EmailService
from app.core.services.password_service import PasswordService
from app.core.services.token_service import TokenService
from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.user import PasswordResetToken, RefreshToken, User
from app.modules.audit.repository import AuditLogRepository
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    _user_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def get_user_lock(cls, user_id: uuid.UUID | str) -> asyncio.Lock:
        uid = str(user_id)
        if uid not in cls._user_locks:
            cls._user_locks[uid] = asyncio.Lock()
        return cls._user_locks[uid]

    def __init__(
        self,
        user_repository: UserRepository,
        audit_repository: AuditLogRepository,
        email_service: Optional[EmailService] = None,
    ):
        self._user_repository = user_repository
        self._audit_repository = audit_repository
        self._email_service = email_service or EmailService()

    async def authenticate_user(self, payload: LoginRequest) -> TokenResponse:
        user_entity = await self._user_repository.get_by_email(payload.email)
        if not user_entity or not PasswordService.verify_password(
            payload.password, user_entity.password_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_CREDENTIALS",
                    "message": "Incorrect email address or password.",
                },
            )

        if not user_entity.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "USER_DEACTIVATED",
                    "message": "User account has been deactivated.",
                },
            )

        tokens = await self._issue_tokens(user_entity)

        # Audit log for user login
        login_audit = AuditLog(
            user_id=user_entity.id,
            module=AuditModule.AUTH.value,
            action=AuditAction.USER_LOGIN.value,
            entity=AuditEntity.USER.value,
            entity_id=user_entity.id,
        )
        await self._audit_repository.create_log(login_audit)

        return tokens

    async def refresh_tokens(self, refresh_token_string: str) -> TokenResponse:
        decoded_payload = TokenService.decode_token(
            refresh_token_string, is_refresh=True
        )
        if not decoded_payload or "sub" not in decoded_payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_REFRESH_TOKEN",
                    "message": "Refresh token is invalid or expired.",
                },
            )

        try:
            user_uuid = uuid.UUID(str(decoded_payload["sub"]))
        except (ValueError, TypeError) as err:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_REFRESH_TOKEN",
                    "message": "Refresh token is invalid or expired.",
                },
            ) from err

        # Serialize token refresh with password reset and token revocation for the same user
        async with self.get_user_lock(user_uuid):
            user_entity = await self._user_repository.get_for_update(user_uuid)
            if not user_entity or not user_entity.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "USER_INACTIVE",
                        "message": "User account is inactive or deleted.",
                    },
                )

            hashed_refresh_token = TokenService.hash_token(refresh_token_string)
            persisted_token = await self._user_repository.get_refresh_token(
                hashed_refresh_token
            )

            if (
                not persisted_token
                or persisted_token.is_revoked
                or persisted_token.user_id != user_uuid
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "REVOKED_REFRESH_TOKEN",
                        "message": "Refresh token has been revoked or expired.",
                    },
                )

            expiration_datetime = persisted_token.expires_at
            if expiration_datetime.tzinfo is None:
                expiration_datetime = expiration_datetime.replace(tzinfo=timezone.utc)

            if expiration_datetime < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "EXPIRED_REFRESH_TOKEN",
                        "message": "Refresh token has expired.",
                    },
                )

            persisted_token.is_revoked = True

            tokens = await self._issue_tokens(user_entity)

            # Audit log for token refresh
            refresh_audit = AuditLog(
                user_id=user_entity.id,
                module=AuditModule.AUTH.value,
                action=AuditAction.TOKEN_REFRESH.value,
                entity=AuditEntity.USER.value,
                entity_id=user_entity.id,
            )
            await self._audit_repository.create_log(refresh_audit)

            return tokens

    async def logout(self, user_id_string: str, refresh_token_string: str) -> None:
        hashed_refresh_token = TokenService.hash_token(refresh_token_string)
        persisted_token = await self._user_repository.get_refresh_token(
            hashed_refresh_token
        )
        user_uuid = uuid.UUID(user_id_string)
        if persisted_token and str(persisted_token.user_id) == user_id_string:
            persisted_token.is_revoked = True

        # Audit log for logout
        logout_audit = AuditLog(
            user_id=user_uuid,
            module=AuditModule.AUTH.value,
            action=AuditAction.USER_LOGOUT.value,
            entity=AuditEntity.USER.value,
            entity_id=user_uuid,
        )
        await self._audit_repository.create_log(logout_audit)

    async def _issue_tokens(self, user_entity: User) -> TokenResponse:
        role_name = user_entity.role.name if user_entity.role else "employee"
        access_token_jwt = TokenService.create_access_token(
            subject=str(user_entity.id), roles=[role_name]
        )
        refresh_token_jwt = TokenService.create_refresh_token(
            subject=str(user_entity.id)
        )

        token_record = RefreshToken(
            user_id=user_entity.id,
            token_hash=TokenService.hash_token(refresh_token_jwt),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            is_revoked=False,
        )
        await self._user_repository.save_refresh_token(token_record)

        return TokenResponse(
            access_token=access_token_jwt,
            refresh_token=refresh_token_jwt,
            token_type="bearer",
            expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def request_password_reset(self, payload: ForgotPasswordRequest) -> None:
        start_time = time.monotonic()
        try:
            user_entity = await self._user_repository.get_by_email(payload.email)
            if user_entity and user_entity.is_active:
                raw_token = secrets.token_urlsafe(32)
                token_hash = TokenService.hash_token(raw_token)
                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
                )
                reset_record = PasswordResetToken(
                    user_id=user_entity.id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    is_used=False,
                )

                recipient_name = (
                    f"{user_entity.first_name} {user_entity.last_name}".strip()
                )
                reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw_token}"

                try:
                    async with self._user_repository.session.begin_nested():
                        await self._user_repository.invalidate_user_reset_tokens(
                            user_entity.id
                        )
                        await self._user_repository.create_password_reset_token(
                            reset_record
                        )

                        await self._email_service.send_password_reset_email(
                            to_email=user_entity.email,
                            recipient_name=recipient_name,
                            reset_url=reset_url,
                        )

                        # Audit log for requested reset
                        audit = AuditLog(
                            user_id=user_entity.id,
                            module=AuditModule.AUTH.value,
                            action=AuditAction.PASSWORD_RESET_REQUESTED.value,
                            entity=AuditEntity.USER.value,
                            entity_id=user_entity.id,
                        )
                        await self._audit_repository.create_log(audit)
                except Exception as e:
                    # Security: Delivery failures must not leak account existence or change the public response.
                    # The savepoint automatically rolls back uncommitted reset tokens and invalidations.
                    # Failure is logged without the raw reset token or reset URL.
                    logger.error(
                        "Password reset email delivery failed for user %s: %s",
                        user_entity.email,
                        e,
                    )
            else:
                # Anti-enumeration timing mitigation: perform dummy cryptographic token generation
                dummy_token = secrets.token_urlsafe(32)
                TokenService.hash_token(dummy_token)
        finally:
            # Enforce uniform minimum duration across every branch so response timing is independent of account existence
            elapsed = time.monotonic() - start_time
            min_duration = getattr(
                settings, "PASSWORD_RESET_MIN_DURATION_SECONDS", 0.15
            )
            if elapsed < min_duration:
                await asyncio.sleep(min_duration - elapsed)

    async def reset_password(self, payload: ResetPasswordRequest) -> None:
        token_hash = TokenService.hash_token(payload.token)
        token_record = await self._user_repository.get_password_reset_token(token_hash)

        if not token_record or token_record.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_RESET_TOKEN",
                    "message": "Invalid or expired reset token.",
                },
            )

        expiration_datetime = token_record.expires_at
        if expiration_datetime.tzinfo is None:
            expiration_datetime = expiration_datetime.replace(tzinfo=timezone.utc)

        if expiration_datetime < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "EXPIRED_RESET_TOKEN",
                    "message": "Invalid or expired reset token.",
                },
            )

        # Serialize password reset and token revocation with refresh token issuance for the same user
        async with self.get_user_lock(token_record.user_id):
            user_entity = await self._user_repository.get_for_update(
                token_record.user_id
            )
            if not user_entity or not user_entity.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_USER",
                        "message": "Invalid request.",
                    },
                )

            # Re-check under lock in case of concurrent execution
            if token_record.is_used:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_RESET_TOKEN",
                        "message": "Invalid or expired reset token.",
                    },
                )

            # Update password hash
            user_entity.password_hash = PasswordService.hash_password(
                payload.new_password
            )
            token_record.is_used = True

            # Invalidate all active sessions for this user
            await self._user_repository.revoke_all_refresh_tokens(user_entity.id)

            # Audit log
            audit = AuditLog(
                user_id=user_entity.id,
                module=AuditModule.AUTH.value,
                action=AuditAction.PASSWORD_RESET_COMPLETED.value,
                entity=AuditEntity.USER.value,
                entity_id=user_entity.id,
            )
            await self._audit_repository.create_log(audit)
