import uuid
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.timesheet import TimesheetEntry
from app.models.user import User
from app.repositories.base import BaseRepository


class TimesheetRepository(BaseRepository[TimesheetEntry]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(TimesheetEntry, database_session)

    async def list_entries(
        self,
        user_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_status: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[TimesheetEntry], int]:
        filter_conditions = []
        if user_id:
            filter_conditions.append(TimesheetEntry.user_id == user_id)
        if project_id:
            filter_conditions.append(TimesheetEntry.project_id == project_id)
        if start_date:
            filter_conditions.append(TimesheetEntry.work_date >= start_date)
        if end_date:
            filter_conditions.append(TimesheetEntry.work_date <= end_date)
        if entry_status:
            filter_conditions.append(TimesheetEntry.status == entry_status)

        return await self.list_paginated(
            offset=offset, limit=limit, filter_conditions=filter_conditions
        )

    async def get_user_entries_for_date_range(
        self,
        user_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> Sequence[TimesheetEntry]:
        query_result = await self._database_session.execute(
            select(TimesheetEntry).where(
                TimesheetEntry.user_id == user_id,
                TimesheetEntry.work_date >= start_date,
                TimesheetEntry.work_date <= end_date,
            )
        )
        return query_result.scalars().all()

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

        query = select(func.coalesce(func.sum(TimesheetEntry.hours_spent), 0)).where(
            TimesheetEntry.user_id == user_id,
            TimesheetEntry.work_date == target_date,
        )
        if exclude_entry_id:
            query = query.where(TimesheetEntry.id != exclude_entry_id)

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
            select(TimesheetEntry).where(TimesheetEntry.id.in_(entry_ids))
        )
        entries = query_result.scalars().all()
        for entry in entries:
            entry.status = new_status
            if approver_id:
                entry.approver_id = approver_id
        await self._database_session.flush()
        return len(entries)
