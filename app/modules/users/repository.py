import uuid
from typing import Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import EmployeeProfile, RefreshToken, User
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
