import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.services.password_service import PasswordService
from app.core.services.token_service import TokenService
from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.user import RefreshToken, User
from app.modules.audit.repository import AuditLogRepository
from app.modules.auth.schemas import LoginRequest, TokenResponse
from app.modules.users.repository import UserRepository


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        audit_repository: AuditLogRepository,
    ):
        self._user_repository = user_repository
        self._audit_repository = audit_repository

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
        if not decoded_payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_REFRESH_TOKEN",
                    "message": "Refresh token is invalid or expired.",
                },
            )

        hashed_refresh_token = TokenService.hash_token(refresh_token_string)
        persisted_token = await self._user_repository.get_refresh_token(
            hashed_refresh_token
        )

        if not persisted_token:
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

        user_entity = await self._user_repository.get_with_details(
            persisted_token.user_id
        )
        if not user_entity or not user_entity.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "USER_INACTIVE",
                    "message": "User account is inactive or deleted.",
                },
            )

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
        if persisted_token and str(persisted_token.user_id) == str(user_id_string):
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
