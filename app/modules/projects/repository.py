import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization import Department
from app.models.timesheet import Project
from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(Project, database_session)

    async def get_by_id(self, entity_id: uuid.UUID) -> Optional[Project]:
        query_result = await self._database_session.execute(
            select(Project)
            .options(selectinload(Project.departments))
            .where(Project.id == entity_id)
        )
        return query_result.scalar_one_or_none()

    async def get_by_code(self, project_code: str) -> Optional[Project]:
        query_result = await self._database_session.execute(
            select(Project)
            .options(selectinload(Project.departments))
            .where(Project.code == project_code)
        )
        return query_result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Project]:
        query_result = await self._database_session.execute(
            select(Project)
            .options(selectinload(Project.departments))
            .where(Project.name == name)
        )
        return query_result.scalar_one_or_none()

    async def list_active_projects(
        self, department_id: Optional[uuid.UUID] = None
    ) -> Sequence[Project]:
        query = (
            select(Project)
            .options(selectinload(Project.departments))
            .where(Project.is_active.is_(True))
        )
        if department_id:
            query = query.where(Project.departments.any(Department.id == department_id))
        query_result = await self._database_session.execute(query)
        return query_result.scalars().all()

    async def get_departments_by_ids(
        self, department_ids: list[uuid.UUID]
    ) -> Sequence[Department]:
        if not department_ids:
            return []
        query_result = await self._database_session.execute(
            select(Department).where(Department.id.in_(department_ids))
        )
        return query_result.scalars().all()
