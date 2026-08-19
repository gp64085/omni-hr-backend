import uuid
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.timesheet import Timesheet
from app.models.user import User
from app.repositories.base import BaseRepository


class TimesheetRepository(BaseRepository[Timesheet]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(Timesheet, database_session)

    async def list_entries(
        self,
        user_id: Optional[uuid.UUID] = None,
        user_ids: Optional[list[uuid.UUID]] = None,
        exclude_user_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_status: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[Timesheet], int]:
        filter_conditions = []
        if user_id:
            filter_conditions.append(Timesheet.user_id == user_id)
        elif user_ids is not None:
            filter_conditions.append(Timesheet.user_id.in_(user_ids))
        if exclude_user_id:
            filter_conditions.append(Timesheet.user_id != exclude_user_id)
        if project_id:
            filter_conditions.append(Timesheet.project_id == project_id)
        if start_date:
            filter_conditions.append(Timesheet.work_date >= start_date)
        if end_date:
            filter_conditions.append(Timesheet.work_date <= end_date)
        if entry_status:
            filter_conditions.append(Timesheet.status == entry_status)

        return await self.list_paginated(
            offset=offset, limit=limit, filter_conditions=filter_conditions
        )

    async def get_user_entries_for_date_range(
        self,
        user_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> Sequence[Timesheet]:
        query_result = await self._database_session.execute(
            select(Timesheet).where(
                Timesheet.user_id == user_id,
                Timesheet.work_date >= start_date,
                Timesheet.work_date <= end_date,
            )
        )
        return query_result.scalars().all()

    async def get_by_user_and_date(
        self, user_id: uuid.UUID, target_date: date
    ) -> Optional[Timesheet]:
        query_result = await self._database_session.execute(
            select(Timesheet).where(
                Timesheet.user_id == user_id,
                Timesheet.work_date == target_date,
            )
        )
        return query_result.scalar_one_or_none()

    async def get_user_daily_logged_hours(
        self,
        user_id: uuid.UUID,
        target_date: date,
        exclude_entry_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> float:
        if for_update:
            await self._database_session.execute(
                select(User.id).where(User.id == user_id).with_for_update()
            )

        query = select(func.coalesce(func.sum(Timesheet.hours_spent), 0)).where(
            Timesheet.user_id == user_id,
            Timesheet.work_date == target_date,
        )
        if exclude_entry_id:
            query = query.where(Timesheet.id != exclude_entry_id)

        query_result = await self._database_session.execute(query)
        total_hours = query_result.scalar()
        return float(total_hours or 0.0)

    async def bulk_update_status(
        self,
        entry_ids: list[uuid.UUID],
        new_status: str,
        approver_id: Optional[uuid.UUID] = None,
    ) -> int:
        query_result = await self._database_session.execute(
            select(Timesheet).where(Timesheet.id.in_(entry_ids))
        )
        entries = query_result.scalars().all()
        for entry in entries:
            entry.status = new_status
            if approver_id:
                entry.approver_id = approver_id
        await self._database_session.flush()
        return len(entries)
