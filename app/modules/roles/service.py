import uuid
from typing import Optional, Sequence

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.role import Permission, Role
from app.modules.audit.repository import AuditLogRepository
from app.modules.roles.repository import RoleRepository
from app.modules.roles.schemas import PermissionCreate, RoleCreate, RoleUpdate


class RoleService:
    def __init__(
        self,
        role_repository: RoleRepository,
        audit_repository: Optional[AuditLogRepository] = None,
    ):
        self._role_repo = role_repository
        self._audit_repo = audit_repository

    async def _validate_and_get_permissions(
        self, requested_ids: list[uuid.UUID]
    ) -> list[Permission]:
        if not requested_ids:
            return []
        unique_ids = set(requested_ids)
        permissions = list(
            await self._role_repo.get_permissions_by_ids(list(unique_ids))
        )
        if len(permissions) != len(unique_ids):
            found_ids = {p.id for p in permissions}
            missing_ids = unique_ids - found_ids
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_PERMISSION_IDS",
                    "message": f"One or more requested permission IDs do not exist: {[str(i) for i in missing_ids]}",
                },
            )
        return permissions

    async def list_roles(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[Sequence[Role], int]:
        offset = (page - 1) * limit
        return await self._role_repo.search_roles(
            offset=offset, limit=limit, search_term=search
        )

    async def list_permissions(
        self,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        module: Optional[str] = None,
    ) -> tuple[Sequence[Permission], int]:
        offset = (page - 1) * limit
        return await self._role_repo.search_permissions(
            offset=offset, limit=limit, search_term=search, module=module
        )

    async def get_role_permissions(self, role_id: uuid.UUID) -> Sequence[Permission]:
        role = await self.get_role(role_id)
        return role.permissions

    async def create_permission(self, perm_in: PermissionCreate) -> Permission:
        existing = await self._role_repo.get_permission_by_code(perm_in.code)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "PERMISSION_ALREADY_EXISTS",
                    "message": f"Permission code '{perm_in.code}' already exists.",
                },
            )

        perm = Permission(
            code=perm_in.code,
            module=perm_in.module,
            description=perm_in.description,
        )
        return await self._role_repo.create_permission(perm)

    async def get_role(self, role_id: uuid.UUID) -> Role:
        role = await self._role_repo.get_with_permissions(role_id)
        if not role or not role.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ROLE_NOT_FOUND", "message": "Role not found."},
            )
        return role

    async def create_role(self, role_in: RoleCreate) -> Role:
        existing_role = await self._role_repo.get_by_name(role_in.name)
        if existing_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "ROLE_ALREADY_EXISTS",
                    "message": f"A role with name '{role_in.name}' already exists.",
                },
            )

        permissions = await self._validate_and_get_permissions(role_in.permission_ids)

        role = Role(
            name=role_in.name,
            description=role_in.description,
            is_system=False,
            permissions=permissions,
        )
        await self._role_repo.create(role)
        created_role = await self.get_role(role.id)

        if self._audit_repo:
            audit = AuditLog(
                module=AuditModule.ROLES.value,
                action=AuditAction.ROLE_CREATE.value,
                entity=AuditEntity.ROLE.value,
                entity_id=created_role.id,
                extra_metadata={"role_name": created_role.name},
            )
            await self._audit_repo.create_log(audit)

        return created_role

    async def update_role(self, role_id: uuid.UUID, role_in: RoleUpdate) -> Role:
        role = await self.get_role(role_id)

        if role_in.name and role_in.name != role.name:
            if role.is_system:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "SYSTEM_ROLE_PROTECTED",
                        "message": "Cannot rename a system role.",
                    },
                )
            existing_role = await self._role_repo.get_by_name(role_in.name)
            if existing_role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "ROLE_ALREADY_EXISTS",
                        "message": f"A role with name '{role_in.name}' already exists.",
                    },
                )
            role.name = role_in.name

        if role_in.description is not None:
            role.description = role_in.description

        if role_in.permission_ids is not None:
            permissions = await self._validate_and_get_permissions(
                role_in.permission_ids
            )
            role.permissions = permissions

        updated_role = await self.get_role(role_id)

        if self._audit_repo:
            audit = AuditLog(
                module=AuditModule.ROLES.value,
                action=AuditAction.ROLE_UPDATE.value,
                entity=AuditEntity.ROLE.value,
                entity_id=role_id,
                extra_metadata={"role_name": updated_role.name},
            )
            await self._audit_repo.create_log(audit)

        return updated_role

    async def delete_role(self, role_id: uuid.UUID) -> None:
        role = await self.get_role(role_id)
        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SYSTEM_ROLE_PROTECTED",
                    "message": "System roles cannot be deleted.",
                },
            )

        assigned_user_count = await self._role_repo.count_assigned_users(role_id)
        if assigned_user_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "ROLE_IN_USE",
                    "message": "Cannot delete role while users are assigned to it.",
                },
            )

        await self._role_repo.update(role, {"is_active": False})

        if self._audit_repo:
            audit = AuditLog(
                module=AuditModule.ROLES.value,
                action=AuditAction.ROLE_DELETE.value,
                entity=AuditEntity.ROLE.value,
                entity_id=role_id,
                extra_metadata={"deleted_role_name": role.name},
            )
            await self._audit_repo.create_log(audit)
