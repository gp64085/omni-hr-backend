import uuid
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditLog
from app.models.holiday import CompanyHoliday
from app.models.leave import (
    LeaveAccrualPolicy,
    LeaveAllocation,
    LeaveApproval,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
)
from app.models.user import User
from app.repositories.base import BaseRepository


class LeaveRepository(BaseRepository[LeaveRequest]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(LeaveRequest, database_session)

    async def get_leave_types(self) -> Sequence[LeaveType]:
        query_result = await self._database_session.execute(select(LeaveType))
        return query_result.scalars().all()

    async def get_leave_type_by_id(
        self, leave_type_id: uuid.UUID
    ) -> Optional[LeaveType]:
        query_result = await self._database_session.execute(
            select(LeaveType).where(LeaveType.id == leave_type_id)
        )
        return query_result.scalar_one_or_none()

    async def get_allocations(
        self, user_id: uuid.UUID, year: int
    ) -> Sequence[LeaveAllocation]:
        query_result = await self._database_session.execute(
            select(LeaveAllocation)
            .options(selectinload(LeaveAllocation.leave_type))
            .where(
                LeaveAllocation.user_id == user_id,
                LeaveAllocation.year == year,
            )
        )
        return query_result.scalars().all()

    async def get_allocation_for_type(
        self,
        user_id: uuid.UUID,
        leave_type_id: uuid.UUID,
        year: int,
        for_update: bool = False,
    ) -> Optional[LeaveAllocation]:
        query = (
            select(LeaveAllocation)
            .options(selectinload(LeaveAllocation.leave_type))
            .where(
                LeaveAllocation.user_id == user_id,
                LeaveAllocation.leave_type_id == leave_type_id,
                LeaveAllocation.year == year,
            )
        )
        if for_update:
            query = query.with_for_update()
        query_result = await self._database_session.execute(query)
        return query_result.scalar_one_or_none()

    async def save_allocation(self, allocation: LeaveAllocation) -> LeaveAllocation:
        self._database_session.add(allocation)
        await self._database_session.flush()
        return allocation

    async def get_overlapping_requests(
        self, user_id: uuid.UUID, start_date: date, end_date: date
    ) -> Sequence[LeaveRequest]:
        """Find non-cancelled and non-rejected leave requests overlapping with given date range."""
        query = select(LeaveRequest).where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
            or_(
                LeaveRequest.start_date.between(start_date, end_date),
                LeaveRequest.end_date.between(start_date, end_date),
                (LeaveRequest.start_date <= start_date)
                & (LeaveRequest.end_date >= end_date),
            ),
        )
        query_result = await self._database_session.execute(query)
        return query_result.scalars().all()

    async def get_leave_request_with_details(
        self, request_id: uuid.UUID
    ) -> Optional[LeaveRequest]:
        query_result = await self._database_session.execute(
            select(LeaveRequest)
            .options(
                selectinload(LeaveRequest.user),
                selectinload(LeaveRequest.leave_type),
            )
            .where(LeaveRequest.id == request_id)
        )
        return query_result.scalar_one_or_none()

    async def search_leave_requests(
        self,
        offset: int = 0,
        limit: int = 20,
        user_id: Optional[uuid.UUID] = None,
        status: Optional[LeaveStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> tuple[Sequence[LeaveRequest], int]:
        query = select(LeaveRequest).options(
            selectinload(LeaveRequest.user),
            selectinload(LeaveRequest.leave_type),
        )

        if user_id:
            query = query.where(LeaveRequest.user_id == user_id)
        if status:
            query = query.where(LeaveRequest.status == status)
        if start_date:
            query = query.where(LeaveRequest.start_date >= start_date)
        if end_date:
            query = query.where(LeaveRequest.end_date <= end_date)

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = (
            query.order_by(LeaveRequest.created_at.desc()).offset(offset).limit(limit)
        )
        records = (await self._database_session.execute(query)).scalars().all()
        return records, total_records

    async def save_approval(self, approval: LeaveApproval) -> LeaveApproval:
        self._database_session.add(approval)
        await self._database_session.flush()
        return approval

    async def get_company_holidays(
        self, year: Optional[int] = None
    ) -> Sequence[CompanyHoliday]:
        query = select(CompanyHoliday)
        if year:
            query = query.where(extract("year", CompanyHoliday.holiday_date) == year)
        query = query.order_by(CompanyHoliday.holiday_date.asc())
        query_result = await self._database_session.execute(query)
        return query_result.scalars().all()

    async def get_holiday_by_date(self, holiday_date: date) -> Optional[CompanyHoliday]:
        query_result = await self._database_session.execute(
            select(CompanyHoliday).where(CompanyHoliday.holiday_date == holiday_date)
        )
        return query_result.scalar_one_or_none()

    async def create_holiday(self, holiday: CompanyHoliday) -> CompanyHoliday:
        self._database_session.add(holiday)
        await self._database_session.flush()
        return holiday

    async def save_accrual_policy(
        self, policy: LeaveAccrualPolicy
    ) -> LeaveAccrualPolicy:
        self._database_session.add(policy)
        await self._database_session.flush()
        return policy

    async def get_active_accrual_policies(self) -> Sequence[LeaveAccrualPolicy]:
        query_result = await self._database_session.execute(
            select(LeaveAccrualPolicy)
            .options(selectinload(LeaveAccrualPolicy.leave_type))
            .where(LeaveAccrualPolicy.is_active.is_(True))
        )
        return query_result.scalars().all()

    async def get_policy_by_designation_and_type(
        self, leave_type_id: uuid.UUID, designation_id: Optional[uuid.UUID]
    ) -> Optional[LeaveAccrualPolicy]:
        query = select(LeaveAccrualPolicy).where(
            LeaveAccrualPolicy.leave_type_id == leave_type_id,
            LeaveAccrualPolicy.is_active.is_(True),
        )
        if designation_id:
            query = query.where(LeaveAccrualPolicy.designation_id == designation_id)
        else:
            query = query.where(LeaveAccrualPolicy.designation_id.is_(None))

        query_result = await self._database_session.execute(query)
        return query_result.scalar_one_or_none()

    async def get_active_users(self) -> Sequence[User]:
        query_result = await self._database_session.execute(
            select(User)
            .options(selectinload(User.designation))
            .where(User.is_active.is_(True))
        )
        return query_result.scalars().all()

    async def save_audit_log(self, audit_log: AuditLog) -> AuditLog:
        self._database_session.add(audit_log)
        await self._database_session.flush()
        return audit_log

    async def search_audit_logs(
        self,
        offset: int = 0,
        limit: int = 20,
        user_id: Optional[uuid.UUID] = None,
        action: Optional[str] = None,
        entity: Optional[str] = None,
    ) -> tuple[Sequence[AuditLog], int]:
        query = select(AuditLog)

        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if action:
            query = query.where(AuditLog.action == action)
        if entity:
            query = query.where(AuditLog.entity == entity)

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
        records = (await self._database_session.execute(query)).scalars().all()
        return records, total_records
