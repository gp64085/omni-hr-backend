import uuid
from typing import Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import EmployeeProfile, RefreshToken, User, UserRole
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(User, database_session)

    async def get_by_email(self, email_address: str) -> Optional[User]:
        query_result = await self._database_session.execute(
            select(User)
            .options(
                selectinload(User.department),
                selectinload(User.designation),
                selectinload(User.profile),
                selectinload(User.role),
            )
            .where(User.email == email_address)
        )
        return query_result.scalar_one_or_none()

    async def get_with_details(self, user_id: uuid.UUID) -> Optional[User]:
        query_result = await self._database_session.execute(
            select(User)
            .options(
                selectinload(User.department),
                selectinload(User.designation),
                selectinload(User.profile),
                selectinload(User.role),
            )
            .where(User.id == user_id)
        )
        return query_result.scalar_one_or_none()

    async def search_users(
        self,
        offset: int = 0,
        limit: int = 20,
        search_term: Optional[str] = None,
        department_id: Optional[uuid.UUID] = None,
        role_id: Optional[uuid.UUID] = None,
        role_name: Optional[str] = None,
    ) -> tuple[Sequence[User], int]:
        query = (
            select(User)
            .where(User.is_active.is_(True))
            .options(
                selectinload(User.department),
                selectinload(User.designation),
                selectinload(User.profile),
                selectinload(User.role),
            )
        )

        if department_id:
            query = query.where(User.department_id == department_id)
        if role_id:
            query = query.where(User.role_id == role_id)
        elif role_name:
            from app.models.role import Role

            query = query.join(User.role).where(Role.name == role_name)
        if search_term:
            search_pattern = f"%{search_term}%"
            query = query.where(
                or_(
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = query.order_by(User.created_at.desc()).offset(offset).limit(limit)
        user_records = (await self._database_session.execute(query)).scalars().all()
        return user_records, total_records

    async def get_refresh_token(self, token_hash: str) -> Optional[RefreshToken]:
        query_result = await self._database_session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked.is_(False),
            )
        )
        return query_result.scalar_one_or_none()

    async def save_refresh_token(
        self, refresh_token_entity: RefreshToken
    ) -> RefreshToken:
        self._database_session.add(refresh_token_entity)
        await self._database_session.flush()
        return refresh_token_entity

    async def get_profile(self, user_id: uuid.UUID) -> Optional[EmployeeProfile]:
        query_result = await self._database_session.execute(
            select(EmployeeProfile).where(EmployeeProfile.user_id == user_id)
        )
        return query_result.scalar_one_or_none()

    async def save_profile(self, profile: EmployeeProfile) -> EmployeeProfile:
        self._database_session.add(profile)
        await self._database_session.flush()
        return profile

    async def get_authorized_viewable_user_ids(
        self, current_user: User, requested_user_id: Optional[uuid.UUID] = None
    ) -> Optional[list[uuid.UUID]]:

        role_name = current_user.role.name if current_user.role else ""
        if role_name in [UserRole.SUPER_ADMIN.value, UserRole.HR_MANAGER.value]:
            if requested_user_id:
                return [requested_user_id]
            return None

        # Fetch direct subordinates where manager_id == current_user.id
        sub_query = select(User.id).where(User.manager_id == current_user.id)
        sub_res = await self._database_session.execute(sub_query)
        subordinate_ids = set(sub_res.scalars().all())

        # If Department Lead, also include members of their department
        if role_name == UserRole.DEPARTMENT_LEAD.value and current_user.department_id:
            dept_query = select(User.id).where(
                User.department_id == current_user.department_id
            )
            dept_res = await self._database_session.execute(dept_query)
            subordinate_ids.update(dept_res.scalars().all())

        # Always include the user's own id
        subordinate_ids.add(current_user.id)

        if requested_user_id:
            if requested_user_id in subordinate_ids:
                return [requested_user_id]
            # Not authorized to view requested user -> restrict to own user id
            return [current_user.id]

        return list(subordinate_ids)

    async def get_user_ids_by_role_names(
        self, role_names: list[str]
    ) -> list[uuid.UUID]:
        from app.models.role import Role

        query = (
            select(User.id)
            .join(User.role)
            .where(
                User.is_active.is_(True),
                Role.name.in_(role_names),
            )
        )
        res = await self._database_session.execute(query)
        return list(res.scalars().all())
