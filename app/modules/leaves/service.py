import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.holiday import CompanyHoliday
from app.models.leave import (
    AccrualFrequency,
    HalfDayType,
    LeaveAccrualPolicy,
    LeaveAllocation,
    LeaveApproval,
    LeaveRequest,
    LeaveStatus,
    LeaveTypeEnum,
)
from app.modules.audit.repository import AuditLogRepository
from app.modules.leaves.repository import LeaveRepository
from app.modules.leaves.schemas import (
    AuditLogRead,
    HolidayCreatePayload,
    HolidayRead,
    LeaveAccrualPolicyCreatePayload,
    LeaveAccrualPolicyRead,
    LeaveAllocationRead,
    LeaveRequestCreate,
    LeaveRequestRead,
    LeaveStatusUpdatePayload,
    LeaveTypeRead,
    ManualAllocationGrantPayload,
)


class LeaveService:
    def __init__(
        self,
        leave_repository: LeaveRepository,
        audit_repository: AuditLogRepository,
    ):
        self._leave_repo = leave_repository
        self._audit_repo = audit_repository

    def calculate_working_days(
        self,
        start_date: date,
        end_date: date,
        half_day_type: HalfDayType,
        holiday_dates: set[date],
    ) -> float:
        """Calculate working days excluding weekends (Sat/Sun) and official company holidays."""
        current = start_date
        total_days = 0.0

        while current <= end_date:
            # 5 = Saturday, 6 = Sunday
            if current.weekday() < 5 and current not in holiday_dates:
                total_days += 1.0
            current += timedelta(days=1)

        if half_day_type != HalfDayType.NONE and total_days > 0:
            total_days = max(0.5, total_days - 0.5)

        return total_days

    async def get_leave_types(self) -> list[LeaveTypeRead]:
        types = await self._leave_repo.get_leave_types()
        return [LeaveTypeRead.model_validate(t) for t in types]

    async def get_user_balances(
        self, user_id: uuid.UUID, year: int
    ) -> list[LeaveAllocationRead]:
        leave_types = await self._leave_repo.get_leave_types()
        existing_allocations = await self._leave_repo.get_allocations(user_id, year)
        existing_type_map = {a.leave_type_id: a for a in existing_allocations}

        result_allocations = []
        for lt in leave_types:
            if lt.id not in existing_type_map:
                new_allocation = LeaveAllocation(
                    user_id=user_id,
                    leave_type_id=lt.id,
                    year=year,
                    allocated_days=lt.default_quota or 0,
                    used_days=0.0,
                    comp_off_credits=0.0,
                )
                saved = await self._leave_repo.save_allocation(new_allocation)
                # Refresh eager loading
                alloc = await self._leave_repo.get_allocation_for_type(
                    user_id, lt.id, year
                )
                if alloc:
                    saved = alloc
                existing_type_map[lt.id] = saved

            alloc_entity = existing_type_map[lt.id]
            alloc_dict = LeaveAllocationRead.model_validate(alloc_entity)
            alloc_dict.remaining_days = (
                alloc_entity.allocated_days + alloc_entity.comp_off_credits
            ) - alloc_entity.used_days
            result_allocations.append(alloc_dict)

        return result_allocations

    async def apply_leave(
        self, user_id: uuid.UUID, payload: LeaveRequestCreate
    ) -> LeaveRequestRead:
        if payload.end_date < payload.start_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_DATE_RANGE",
                    "message": "Leave end date cannot be earlier than start date.",
                },
            )

        leave_type = await self._leave_repo.get_leave_type_by_id(payload.leave_type_id)
        if not leave_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LEAVE_TYPE_NOT_FOUND",
                    "message": "The specified leave type does not exist.",
                },
            )

        # Check overlapping leave requests
        overlaps = await self._leave_repo.get_overlapping_requests(
            user_id, payload.start_date, payload.end_date
        )
        if overlaps:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OVERLAPPING_LEAVE_REQUEST",
                    "message": "Leave request overlaps with an existing pending or approved leave.",
                    "details": {"conflicting_request_id": str(overlaps[0].id)},
                },
            )

        # Calculate working days excluding weekends & company holidays
        holidays = await self._leave_repo.get_company_holidays(payload.start_date.year)
        holiday_dates = {h.holiday_date for h in holidays}
        total_working_days = self.calculate_working_days(
            payload.start_date, payload.end_date, payload.half_day_type, holiday_dates
        )

        if total_working_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "NO_WORKING_DAYS_IN_RANGE",
                    "message": "The requested leave period contains only weekends or company holidays.",
                },
            )

        # Verify allocation balance for paid leave types
        balances = await self.get_user_balances(user_id, payload.start_date.year)
        target_balance = next(
            (b for b in balances if b.leave_type_id == payload.leave_type_id), None
        )

        if (
            leave_type.name != LeaveTypeEnum.UNPAID
            and target_balance
            and total_working_days > target_balance.remaining_days
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INSUFFICIENT_LEAVE_BALANCE",
                    "message": f"Requested {total_working_days} days exceed remaining leave balance ({target_balance.remaining_days} days).",
                },
            )

        # Determine auto-approval status
        is_auto_approved = False
        if not leave_type.requires_approval:
            is_auto_approved = True
        elif (
            leave_type.auto_approve_threshold > 0
            and total_working_days <= leave_type.auto_approve_threshold
        ):
            is_auto_approved = True

        leave_status = LeaveStatus.APPROVED if is_auto_approved else LeaveStatus.PENDING

        new_request = LeaveRequest(
            user_id=user_id,
            leave_type_id=payload.leave_type_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            half_day_type=payload.half_day_type,
            total_days=total_working_days,
            status=leave_status,
            is_auto_approved=is_auto_approved,
            reason=payload.reason,
        )
        await self._leave_repo.create(new_request)

        # Update used days if auto-approved
        if is_auto_approved and leave_type.name != LeaveTypeEnum.UNPAID:
            alloc = await self._leave_repo.get_allocation_for_type(
                user_id, payload.leave_type_id, payload.start_date.year
            )
            if alloc:
                alloc.used_days = alloc.used_days + total_working_days
                await self._leave_repo.save_allocation(alloc)

        created_details = await self._leave_repo.get_leave_request_with_details(
            new_request.id
        )

        if self._audit_repo:
            audit = AuditLog(
                user_id=user_id,
                module=AuditModule.LEAVES.value,
                action=AuditAction.LEAVE_APPLY.value,
                entity=AuditEntity.LEAVE_REQUEST.value,
                entity_id=new_request.id,
                extra_metadata={
                    "start_date": payload.start_date.isoformat(),
                    "end_date": payload.end_date.isoformat(),
                    "total_days": total_working_days,
                    "status": leave_status.value,
                },
            )
            await self._audit_repo.create_log(audit)

        return LeaveRequestRead.model_validate(created_details)

    async def list_leave_requests(
        self,
        page: int = 1,
        limit: int = 20,
        user_id: Optional[uuid.UUID] = None,
        leave_status: Optional[LeaveStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> tuple[list[LeaveRequestRead], int]:
        offset = (page - 1) * limit
        requests, total = await self._leave_repo.search_leave_requests(
            offset=offset,
            limit=limit,
            user_id=user_id,
            status=leave_status,
            start_date=start_date,
            end_date=end_date,
        )
        return [LeaveRequestRead.model_validate(r) for r in requests], total

    async def update_leave_status(
        self,
        request_id: uuid.UUID,
        payload: LeaveStatusUpdatePayload,
        approver_id: uuid.UUID,
    ) -> LeaveRequestRead:
        """Approve or reject a pending leave request (Single-Tier Manager/HR Approval Workflow)."""
        leave_request = await self._leave_repo.get_leave_request_with_details(
            request_id
        )

        if not leave_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LEAVE_REQUEST_NOT_FOUND",
                    "message": "Leave request with the specified ID was not found.",
                },
            )

        if payload.status not in [LeaveStatus.APPROVED, LeaveStatus.REJECTED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_UPDATE",
                    "message": "Leave request status can only be updated to APPROVED or REJECTED.",
                },
            )

        if leave_request.status != LeaveStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_LEAVE_STATUS",
                    "message": f"Only PENDING leave requests can be approved or rejected. Current status is {leave_request.status.value}.",
                },
            )

        leave_request.status = payload.status
        leave_request.approver_id = approver_id
        if payload.rejection_reason:
            leave_request.rejection_reason = payload.rejection_reason

        # If approved, deduct used_days from allocation
        if (
            payload.status == LeaveStatus.APPROVED
            and leave_request.leave_type != LeaveTypeEnum.UNPAID
        ):
            alloc = await self._leave_repo.get_allocation_for_type(
                leave_request.user_id,
                leave_request.leave_type_id,
                leave_request.start_date.year,
            )
            if alloc:
                alloc.used_days = alloc.used_days + leave_request.total_days

                await self._leave_repo.save_allocation(alloc)

        # Audit log entry
        approval_audit = LeaveApproval(
            leave_request_id=request_id,
            approver_id=approver_id,
            tier_level=1,
            status=payload.status,
            comments=payload.comments or payload.rejection_reason,
        )
        await self._leave_repo.save_approval(approval_audit)

        if self._audit_repo:
            audit = AuditLog(
                user_id=approver_id,
                module=AuditModule.LEAVES.value,
                action=AuditAction.LEAVE_STATUS_UPDATE.value,
                entity=AuditEntity.LEAVE_REQUEST.value,
                entity_id=request_id,
                extra_metadata={
                    "new_status": payload.status.value,
                    "target_user_id": str(leave_request.user_id),
                },
            )
            await self._audit_repo.create_log(audit)

        updated = await self._leave_repo.get_leave_request_with_details(request_id)
        return LeaveRequestRead.model_validate(updated)

    async def cancel_leave(self, request_id: uuid.UUID, user_id: uuid.UUID) -> None:
        leave_request = await self._leave_repo.get_leave_request_with_details(
            request_id
        )
        if not leave_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LEAVE_REQUEST_NOT_FOUND",
                    "message": "Leave request with the specified ID was not found.",
                },
            )

        if leave_request.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": "You can only cancel your own leave requests.",
                },
            )

        if leave_request.status == LeaveStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "ALREADY_CANCELLED",
                    "message": "This leave request has already been cancelled.",
                },
            )

        # Restore used_days if request was previously approved
        if (
            leave_request.status == LeaveStatus.APPROVED
            and leave_request.leave_type != LeaveTypeEnum.UNPAID
        ):
            if leave_request.start_date <= date.today():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "CANNOT_CANCEL_STARTED_LEAVE",
                        "message": "Cannot cancel an approved leave that has already started or passed.",
                    },
                )
            alloc = await self._leave_repo.get_allocation_for_type(
                user_id,
                leave_request.leave_type_id,
                leave_request.start_date.year,
            )
            if alloc:
                alloc.used_days = max(
                    0.0, (alloc.used_days or 0.0) - leave_request.total_days
                )
                await self._leave_repo.save_allocation(alloc)

        leave_request.status = LeaveStatus.CANCELLED

        if self._audit_repo:
            audit = AuditLog(
                user_id=user_id,
                module=AuditModule.LEAVES.value,
                action=AuditAction.LEAVE_CANCEL.value,
                entity=AuditEntity.LEAVE_REQUEST.value,
                entity_id=request_id,
            )
            await self._audit_repo.create_log(audit)

    async def list_holidays(self, year: Optional[int] = None) -> list[HolidayRead]:
        holidays = await self._leave_repo.get_company_holidays(year)
        return [HolidayRead.model_validate(h) for h in holidays]

    async def create_holiday(self, payload: HolidayCreatePayload) -> HolidayRead:
        existing = await self._leave_repo.get_holiday_by_date(payload.holiday_date)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "HOLIDAY_ALREADY_EXISTS",
                    "message": f"A company holiday is already scheduled on {payload.holiday_date}.",
                },
            )

        holiday = CompanyHoliday(
            name=payload.name,
            holiday_date=payload.holiday_date,
            is_optional=payload.is_optional,
            description=payload.description,
        )
        created = await self._leave_repo.create_holiday(holiday)

        if self._audit_repo:
            audit = AuditLog(
                module=AuditModule.HOLIDAYS.value,
                action=AuditAction.HOLIDAY_CREATE.value,
                entity=AuditEntity.HOLIDAY.value,
                entity_id=created.id,
                extra_metadata={
                    "name": created.name,
                    "holiday_date": created.holiday_date.isoformat(),
                },
            )
            await self._audit_repo.create_log(audit)

        return HolidayRead.model_validate(created)

    async def create_or_update_accrual_policy(
        self, payload: LeaveAccrualPolicyCreatePayload
    ) -> LeaveAccrualPolicyRead:
        leave_type = await self._leave_repo.get_leave_type_by_id(payload.leave_type_id)
        if not leave_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LEAVE_TYPE_NOT_FOUND",
                    "message": "Target leave type for policy was not found.",
                },
            )

        existing = await self._leave_repo.get_policy_by_designation_and_type(
            payload.leave_type_id, payload.designation_id
        )
        if existing:
            existing.frequency = payload.frequency
            existing.accrual_rate = payload.accrual_rate
            existing.max_quota = payload.max_quota
            existing.is_active = payload.is_active
            policy = existing
        else:
            policy = LeaveAccrualPolicy(
                leave_type_id=payload.leave_type_id,
                designation_id=payload.designation_id,
                frequency=payload.frequency,
                accrual_rate=payload.accrual_rate,
                max_quota=payload.max_quota,
                is_active=payload.is_active,
            )
            await self._leave_repo.save_accrual_policy(policy)

        if self._audit_repo:
            audit = AuditLog(
                module=AuditModule.LEAVES.value,
                action=AuditAction.ACCRUAL_POLICY_CONFIGURED.value,
                entity=AuditEntity.LEAVE_POLICY.value,
                entity_id=policy.id,
                extra_metadata={
                    "leave_type_id": str(payload.leave_type_id),
                    "designation_id": str(payload.designation_id)
                    if payload.designation_id
                    else None,
                    "frequency": payload.frequency.value,
                    "accrual_rate": payload.accrual_rate,
                },
            )
            await self._audit_repo.create_log(audit)

        return LeaveAccrualPolicyRead.model_validate(policy)

    async def list_accrual_policies(self) -> list[LeaveAccrualPolicyRead]:
        policies = await self._leave_repo.get_active_accrual_policies()
        return [LeaveAccrualPolicyRead.model_validate(p) for p in policies]

    async def grant_manual_allocation(
        self, payload: ManualAllocationGrantPayload
    ) -> LeaveAllocationRead:
        alloc = await self._leave_repo.get_allocation_for_type(
            payload.user_id, payload.leave_type_id, payload.year
        )
        if not alloc:
            alloc = LeaveAllocation(
                user_id=payload.user_id,
                leave_type_id=payload.leave_type_id,
                year=payload.year,
                allocated_days=payload.granted_days,
                used_days=0.0,
                comp_off_credits=0.0,
            )
        else:
            alloc.allocated_days = alloc.allocated_days + payload.granted_days

        await self._leave_repo.save_allocation(alloc)
        saved = await self._leave_repo.get_allocation_for_type(
            payload.user_id, payload.leave_type_id, payload.year
        )

        # Audit log for manual grant
        audit_entry = AuditLog(
            user_id=payload.user_id,
            module=AuditModule.LEAVES.value,
            action=AuditAction.MANUAL_LEAVE_GRANT.value,
            entity=AuditEntity.LEAVE_ALLOCATION.value,
            entity_id=saved.id if saved else None,
            extra_metadata={
                "granted_days": payload.granted_days,
                "reason": payload.reason,
                "year": payload.year,
                "new_allocated_days": saved.allocated_days if saved else None,
            },
        )
        await self._audit_repo.create_log(audit_entry)

        alloc_read = LeaveAllocationRead.model_validate(saved)

        if saved:
            alloc_read.remaining_days = (
                saved.allocated_days + saved.comp_off_credits
            ) - saved.used_days

        return alloc_read

    async def trigger_periodic_accruals(
        self, target_date: Optional[date] = None
    ) -> int:
        """Process periodic leave accruals for all active users based on configured policies."""
        today = target_date or date.today()
        policies = await self._leave_repo.get_active_accrual_policies()
        if not policies:
            return 0

        active_users = await self._leave_repo.get_active_users()
        total_accrued_count = 0

        for user in active_users:
            user_designation_id = user.designation_id

            for policy in policies:
                # Designation matching: policy specifically for user's designation OR fallback default policy (designation_id is None)
                if (
                    policy.designation_id
                    and policy.designation_id != user_designation_id
                ):
                    continue

                if policy.frequency == AccrualFrequency.MANUAL:
                    continue

                alloc = await self._leave_repo.get_allocation_for_type(
                    user.id, policy.leave_type_id, today.year, for_update=True
                )

                if not alloc:
                    alloc = LeaveAllocation(
                        user_id=user.id,
                        leave_type_id=policy.leave_type_id,
                        year=today.year,
                        allocated_days=0.0,
                        used_days=0.0,
                        comp_off_credits=0.0,
                    )
                    await self._leave_repo.save_allocation(alloc)

                last_date = alloc.last_accrual_date

                should_accrue = False
                if not last_date:
                    should_accrue = True
                else:
                    if policy.frequency == AccrualFrequency.MONTHLY:
                        should_accrue = (
                            today.year > last_date.year or today.month > last_date.month
                        )
                    elif policy.frequency == AccrualFrequency.QUARTERLY:
                        curr_q = (today.month - 1) // 3
                        last_q = (last_date.month - 1) // 3
                        should_accrue = today.year > last_date.year or curr_q > last_q
                    elif policy.frequency == AccrualFrequency.HALF_YEARLY:
                        curr_h = 1 if today.month <= 6 else 2
                        last_h = 1 if last_date.month <= 6 else 2
                        should_accrue = today.year > last_date.year or curr_h > last_h
                    elif policy.frequency == AccrualFrequency.YEARLY:
                        should_accrue = today.year > last_date.year

                if should_accrue:
                    prev_allocated = alloc.allocated_days
                    new_allocation = prev_allocated + policy.accrual_rate
                    if policy.max_quota is not None:
                        new_allocation = min(policy.max_quota, new_allocation)

                    alloc.allocated_days = new_allocation
                    alloc.last_accrual_date = today
                    await self._leave_repo.save_allocation(alloc)

                    # Audit log for periodic accrual execution
                    accrual_audit = AuditLog(
                        user_id=user.id,
                        module=AuditModule.LEAVES.value,
                        action=AuditAction.PERIODIC_LEAVE_ACCRUAL.value,
                        entity=AuditEntity.LEAVE_ALLOCATION.value,
                        entity_id=alloc.id,
                        extra_metadata={
                            "policy_id": str(policy.id),
                            "leave_type_id": str(policy.leave_type_id),
                            "frequency": policy.frequency.value,
                            "accrual_rate": policy.accrual_rate,
                            "previous_allocated_days": prev_allocated,
                            "new_allocated_days": alloc.allocated_days,
                            "accrual_date": today.isoformat(),
                        },
                    )
                    await self._audit_repo.create_log(accrual_audit)

                    total_accrued_count += 1

        return total_accrued_count

    async def list_audit_logs(
        self,
        page: int = 1,
        limit: int = 20,
        user_id: Optional[uuid.UUID] = None,
        action: Optional[str] = None,
        entity: Optional[str] = None,
    ) -> tuple[list[AuditLogRead], int]:
        offset = (page - 1) * limit
        records, total = await self._leave_repo.search_audit_logs(
            offset=offset, limit=limit, user_id=user_id, action=action, entity=entity
        )
        return [AuditLogRead.model_validate(r) for r in records], total
