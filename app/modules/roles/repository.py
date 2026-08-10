import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.role import Permission, Role
from app.repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(Role, database_session)

    async def get_by_name(self, name: str) -> Optional[Role]:
        query_result = await self._database_session.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.name == name)
        )
        return query_result.scalar_one_or_none()

    async def get_with_permissions(self, role_id: uuid.UUID) -> Optional[Role]:
        query_result = await self._database_session.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.id == role_id)
        )
        return query_result.scalar_one_or_none()

    async def list_roles(self) -> Sequence[Role]:
        query_result = await self._database_session.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .where(Role.is_active.is_(True))
            .order_by(Role.created_at.asc())
        )
        return query_result.scalars().all()

    async def search_roles(
        self,
        offset: int = 0,
        limit: int = 20,
        search_term: Optional[str] = None,
    ) -> tuple[Sequence[Role], int]:
        from sqlalchemy import func, or_

        query = (
            select(Role)
            .where(Role.is_active.is_(True))
            .options(selectinload(Role.permissions))
        )

        if search_term:
            search_pattern = f"%{search_term}%"
            query = query.where(
                or_(
                    Role.name.ilike(search_pattern),
                    Role.description.ilike(search_pattern),
                )
            )

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = query.order_by(Role.created_at.asc()).offset(offset).limit(limit)
        role_records = (await self._database_session.execute(query)).scalars().all()
        return role_records, total_records

    async def list_permissions(self) -> Sequence[Permission]:
        query_result = await self._database_session.execute(
            select(Permission).order_by(Permission.module.asc(), Permission.code.asc())
        )
        return query_result.scalars().all()

    async def search_permissions(
        self,
        offset: int = 0,
        limit: int = 20,
        search_term: Optional[str] = None,
        module: Optional[str] = None,
    ) -> tuple[Sequence[Permission], int]:
        from sqlalchemy import func, or_

        query = select(Permission)

        if module:
            query = query.where(Permission.module == module)
        if search_term:
            search_pattern = f"%{search_term}%"
            query = query.where(
                or_(
                    Permission.code.ilike(search_pattern),
                    Permission.module.ilike(search_pattern),
                    Permission.description.ilike(search_pattern),
                )
            )

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = (
            query.order_by(Permission.module.asc(), Permission.code.asc())
            .offset(offset)
            .limit(limit)
        )
        perm_records = (await self._database_session.execute(query)).scalars().all()
        return perm_records, total_records

    async def get_role_permissions(self, role_id: uuid.UUID) -> Sequence[Permission]:
        role = await self.get_with_permissions(role_id)
        if not role:
            return []
        return role.permissions

    async def get_permission_by_code(self, code: str) -> Optional[Permission]:
        query_result = await self._database_session.execute(
            select(Permission).where(Permission.code == code)
        )
        return query_result.scalar_one_or_none()

    async def create_permission(self, permission: Permission) -> Permission:
        self._database_session.add(permission)
        await self._database_session.flush()
        return permission

    async def count_assigned_users(self, role_id: uuid.UUID) -> int:
        from sqlalchemy import func

        from app.models.user import User

        res = await self._database_session.execute(
            select(func.count(User.id)).where(User.role_id == role_id)
        )
        return res.scalar() or 0

    async def get_permissions_by_ids(
        self, permission_ids: list[uuid.UUID]
    ) -> Sequence[Permission]:
        if not permission_ids:
            return []
        query_result = await self._database_session.execute(
            select(Permission).where(Permission.id.in_(permission_ids))
        )
        return query_result.scalars().all()
