import uuid
from typing import Optional

from fastapi import HTTPException, status

from app.core.security import get_password_hash
from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.user import EmployeeProfile, User, UserRole
from app.modules.audit.repository import AuditLogRepository
from app.modules.roles.repository import RoleRepository
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import (
    ProfileResponse,
    ProfileUpdate,
    UserCreate,
    UserResponse,
    UserUpdate,
)


class UserService:
    def __init__(
        self,
        user_repository: UserRepository,
        role_repository: RoleRepository,
        audit_repository: Optional[AuditLogRepository] = None,
    ):
        self._user_repo = user_repository
        self._role_repo = role_repository
        self._audit_repo = audit_repository

    async def _get_user_or_404(
        self, user_id: uuid.UUID, with_details: bool = False
    ) -> User:
        if with_details:
            user = await self._user_repo.get_with_details(user_id)
        else:
            user = await self._user_repo.get_by_id(user_id)

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "USER_NOT_FOUND",
                    "message": "User with the specified ID was not found.",
                },
            )

        return user

    def _validate_role_assignment(
        self, target_role_name: str, requesting_user: Optional[User] = None
    ) -> None:
        if target_role_name == UserRole.SUPER_ADMIN.value:
            requester_role_name = (
                requesting_user.role.name
                if requesting_user and requesting_user.role
                else None
            )
            if requester_role_name != UserRole.SUPER_ADMIN.value:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "ROLE_ASSIGNMENT_FORBIDDEN",
                        "message": "Only Super Administrators can assign the Super Admin role.",
                    },
                )

    async def list_users(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        department_id: Optional[uuid.UUID] = None,
        role_id: Optional[uuid.UUID] = None,
        role_name: Optional[str] = None,
    ) -> tuple[list[UserResponse], int]:
        offset = (page - 1) * limit
        users, total = await self._user_repo.search_users(
            offset=offset,
            limit=limit,
            search_term=search,
            department_id=department_id,
            role_id=role_id,
            role_name=role_name,
        )
        return [UserResponse.model_validate(u) for u in users], total

    async def create_user(
        self, payload: UserCreate, requesting_user: Optional[User] = None
    ) -> UserResponse:
        existing = await self._user_repo.get_by_email(payload.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "EMAIL_ALREADY_EXISTS",
                    "message": "An account with this email address already exists.",
                },
            )

        role_id = payload.role_id
        if not role_id:
            default_role = await self._role_repo.get_by_name(UserRole.EMPLOYEE.value)
            if default_role:
                role_id = default_role.id
        else:
            target_role = await self._role_repo.get_with_permissions(role_id)
            if not target_role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "ROLE_NOT_FOUND",
                        "message": "Target role for user creation does not exist.",
                    },
                )
            self._validate_role_assignment(target_role.name, requesting_user)

        new_user = User(
            email=payload.email,
            password_hash=get_password_hash(payload.password),
            first_name=payload.first_name,
            last_name=payload.last_name,
            role_id=role_id,
            department_id=payload.department_id,
            designation_id=payload.designation_id,
            manager_id=payload.manager_id,
            is_active=True,
        )
        await self._user_repo.create(new_user)

        profile = EmployeeProfile(user_id=new_user.id)
        await self._user_repo.save_profile(profile)

        user_details = await self._user_repo.get_with_details(new_user.id)

        if self._audit_repo:
            audit = AuditLog(
                user_id=requesting_user.id if requesting_user else new_user.id,
                module=AuditModule.USERS.value,
                action=AuditAction.USER_CREATE.value,
                entity=AuditEntity.USER.value,
                entity_id=new_user.id,
                extra_metadata={
                    "role_id": str(role_id) if role_id else None,
                },
            )
            await self._audit_repo.create_log(audit)

        return UserResponse.model_validate(user_details)

    async def get_or_create_profile(self, user_id: uuid.UUID) -> ProfileResponse:
        profile = await self._user_repo.get_profile(user_id)
        if not profile:
            profile = EmployeeProfile(user_id=user_id)
            await self._user_repo.save_profile(profile)

        return ProfileResponse.model_validate(profile)

    async def update_profile(
        self, user_id: uuid.UUID, payload: ProfileUpdate
    ) -> ProfileResponse:
        profile = await self._user_repo.get_profile(user_id)
        if not profile:
            profile = EmployeeProfile(user_id=user_id)
            await self._user_repo.save_profile(profile)

        update_data = payload.model_dump(exclude_unset=True)
        for field, val in update_data.items():
            setattr(profile, field, val)

        if self._audit_repo:
            audit = AuditLog(
                user_id=user_id,
                module=AuditModule.USERS.value,
                action=AuditAction.PROFILE_UPDATE.value,
                entity=AuditEntity.USER.value,
                entity_id=user_id,
                extra_metadata={"updated_fields": list(update_data.keys())},
            )
            await self._audit_repo.create_log(audit)

        return ProfileResponse.model_validate(profile)

    async def get_user_by_id(self, user_id: uuid.UUID) -> UserResponse:
        user = await self._get_user_or_404(user_id, with_details=True)
        return UserResponse.model_validate(user)

    async def update_user(
        self,
        user_id: uuid.UUID,
        payload: UserUpdate,
        requesting_user: Optional[User] = None,
    ) -> UserResponse:
        existing_user = await self._get_user_or_404(user_id, with_details=False)

        update_data = payload.model_dump(exclude_unset=True)

        if "role_id" in update_data and update_data["role_id"] is not None:
            target_role = await self._role_repo.get_with_permissions(
                update_data["role_id"]
            )
            if not target_role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "ROLE_NOT_FOUND",
                        "message": "Target role for update does not exist.",
                    },
                )
            self._validate_role_assignment(target_role.name, requesting_user)

        updated_fields = list(update_data.keys())
        await self._user_repo.update(existing_user, update_data)
        updated_user = await self._user_repo.get_with_details(user_id)

        if self._audit_repo and updated_fields:
            audit = AuditLog(
                user_id=requesting_user.id if requesting_user else user_id,
                module=AuditModule.USERS.value,
                action=AuditAction.USER_UPDATE.value,
                entity=AuditEntity.USER.value,
                entity_id=user_id,
                extra_metadata={"updated_fields": updated_fields},
            )
            await self._audit_repo.create_log(audit)

        return UserResponse.model_validate(updated_user)

    async def delete_user(
        self, user_id: uuid.UUID, requesting_user: Optional[User] = None
    ) -> None:
        existing_user = await self._get_user_or_404(user_id, with_details=False)

        if requesting_user and requesting_user.id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CANNOT_DELETE_SELF",
                    "message": "You cannot delete or deactivate your own account.",
                },
            )

        await self._user_repo.update(existing_user, {"is_active": False})

        if self._audit_repo:
            audit = AuditLog(
                user_id=requesting_user.id if requesting_user else user_id,
                module=AuditModule.USERS.value,
                action=AuditAction.USER_DELETE.value,
                entity=AuditEntity.USER.value,
                entity_id=user_id,
            )
            await self._audit_repo.create_log(audit)
