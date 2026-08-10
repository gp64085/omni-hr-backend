import uuid
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(AuditLog, database_session)

    async def create_log(self, audit_log: AuditLog) -> AuditLog:
        self._database_session.add(audit_log)
        await self._database_session.flush()
        return audit_log

    async def search_audit_logs(
        self,
        offset: int = 0,
        limit: int = 20,
        module: Optional[str] = None,
        entity: Optional[str] = None,
        action: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> tuple[Sequence[AuditLog], int]:
        query = select(AuditLog)

        if module:
            query = query.where(AuditLog.module == module)
        if entity:
            query = query.where(AuditLog.entity == entity)
        if action:
            query = query.where(AuditLog.action == action)
        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if start_date:
            query = query.where(func.date(AuditLog.created_at) >= start_date)
        if end_date:
            query = query.where(func.date(AuditLog.created_at) <= end_date)

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
        records = (await self._database_session.execute(query)).scalars().all()
        return records, total_records
