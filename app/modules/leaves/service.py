import uuid
from datetime import date, timedelta
from typing import Any, Optional, Sequence

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
    LeaveType,
    LeaveTypeEnum,
)
from app.modules.audit.repository import AuditLogRepository
from app.modules.leaves.repository import LeaveRepository
from app.modules.leaves.schemas import (
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
from app.modules.timesheets.repository import TimesheetRepository


class LeaveService:
    def __init__(
        self,
        leave_repository: LeaveRepository,
        audit_repository: AuditLogRepository,
        timesheet_repository: Optional[TimesheetRepository] = None,
        notification_service: Optional[Any] = None,
        user_repository: Optional[Any] = None,
    ):
        self._leave_repo = leave_repository
        self._audit_repo = audit_repository
        self._timesheet_repo = timesheet_repository
        self._notif_service = notification_service
        self._user_repo = user_repository

    # -------------------------------------------------------------------------
    # Day & Calendar Helpers
    # -------------------------------------------------------------------------

    def generate_working_day_items(
        self,
        start_date: date,
        end_date: date,
        half_day_type: HalfDayType,
        holiday_dates: set[date],
        initial_status: str = "pending",
    ) -> list[dict]:
        """Generate day-wise breakdown items for every working day in date range."""
        current = start_date
        valid_days = []
        while current <= end_date:
            if current.weekday() < 5 and current not in holiday_dates:
                valid_days.append(current)
            current += timedelta(days=1)

        items = []
        for d in valid_days:
            day_half = HalfDayType.NONE.value
            duration = 1.0
            if half_day_type != HalfDayType.NONE and len(valid_days) == 1:
                day_half = half_day_type.value
                duration = 0.5

            items.append(
                {
                    "date": d.isoformat(),
                    "day_status": initial_status,
                    "total_days": duration,
                    "half_day_type": day_half,
                    "settled": False,
                    "paid_days": 0.0,
                    "lwp_days": 0.0,
                    "rejection_reason": None,
                }
            )
        return items

    def calculate_working_days(
        self,
        start_date: date,
        end_date: date,
        half_day_type: HalfDayType,
        holiday_dates: set[date],
    ) -> float:
        """Calculate working days excluding weekends (Sat/Sun) and official holidays."""
        current = start_date
        total_days = 0.0

        while current <= end_date:
            if current.weekday() < 5 and current not in holiday_dates:
                total_days += 1.0
            current += timedelta(days=1)

        if half_day_type != HalfDayType.NONE and total_days > 0:
            total_days = max(0.5, total_days - 0.5)

        return total_days

    # -------------------------------------------------------------------------
    # Leave Types & Balances
    # -------------------------------------------------------------------------

    async def get_leave_types(self) -> list[LeaveTypeRead]:
        types = await self._leave_repo.get_leave_types()
        return [LeaveTypeRead.model_validate(t) for t in types]

    def _compute_allocation_balances(
        self,
        alloc_entity: LeaveAllocation,
        user_requests: Sequence[LeaveRequest],
        lt_id: uuid.UUID,
        today: date,
    ) -> LeaveAllocationRead:
        """Helper to calculate consumed, pending, scheduled, and remaining days for allocation."""
        alloc_dict = LeaveAllocationRead.model_validate(alloc_entity)
        type_requests = [r for r in user_requests if r.leave_type_id == lt_id]

        future_approved_days = 0.0
        pending_days = 0.0

        for r in type_requests:
            if r.status == LeaveStatus.APPROVED and r.start_date > today:
                future_approved_days += float(r.total_days)
            elif r.status == LeaveStatus.PENDING:
                pending_days += float(r.total_days)

        consumed_days = float(alloc_entity.used_days)
        alloc_dict.used_days = round(consumed_days, 2)
        alloc_dict.scheduled_future_days = round(future_approved_days, 2)
        alloc_dict.pending_days = round(pending_days, 2)
        alloc_dict.remaining_days = round(
            float(alloc_entity.allocated_days + alloc_entity.comp_off_credits)
            - consumed_days,
            2,
        )
        return alloc_dict

    async def get_user_balances(
        self, user_id: uuid.UUID, year: int
    ) -> list[LeaveAllocationRead]:
        today = date.today()
        # Settle any passed-due leaves up to today
        await self.settle_daily_leaves(target_date=today)

        leave_types = await self._leave_repo.get_leave_types()
        existing_allocations = await self._leave_repo.get_allocations(user_id, year)
        existing_type_map = {a.leave_type_id: a for a in existing_allocations}

        user_requests = await self._leave_repo.get_user_requests_for_year(user_id, year)

        result_allocations = []
        for lt in leave_types:
            if lt.id not in existing_type_map:
                new_allocation = LeaveAllocation(
                    user_id=user_id,
                    leave_type_id=lt.id,
                    year=year,
                    allocated_days=float(lt.default_quota)
                    if lt.default_quota is not None
                    else 0.0,
                    used_days=0.0,
                    comp_off_credits=0.0,
                )
                saved = await self._leave_repo.save_allocation(new_allocation)
                alloc = await self._leave_repo.get_allocation_for_type(
                    user_id, lt.id, year
                )
                if alloc:
                    saved = alloc
                existing_type_map[lt.id] = saved

            alloc_entity = existing_type_map[lt.id]
            alloc_dict = self._compute_allocation_balances(
                alloc_entity, user_requests, lt.id, today
            )
            result_allocations.append(alloc_dict)

        return result_allocations

    # -------------------------------------------------------------------------
    # Leave Application Core & Sub-helpers
    # -------------------------------------------------------------------------

    def _validate_date_range(self, start_date: date, end_date: date) -> None:
        """Validate leave start and end date ordering."""
        if end_date < start_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_DATE_RANGE",
                    "message": "Leave end date cannot be earlier than start date.",
                },
            )

    async def _resolve_leave_type(
        self, leave_type_id: Optional[uuid.UUID]
    ) -> LeaveType:
        """Resolve explicitly requested leave type or dynamically fall back to default type."""
        leave_type = None
        if leave_type_id:
            leave_type = await self._leave_repo.get_leave_type_by_id(leave_type_id)

        if not leave_type:
            all_types = await self._leave_repo.get_leave_types()
            leave_type = next(
                (t for t in all_types if t.name == LeaveTypeEnum.CASUAL),
                all_types[0] if all_types else None,
            )

        if not leave_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "NO_LEAVE_TYPES_CONFIGURED",
                    "message": "No leave types are currently configured in the system.",
                },
            )
        return leave_type

    async def _check_conflicts(
        self, user_id: uuid.UUID, start_date: date, end_date: date
    ) -> None:
        """Verify no overlapping leaves or logged timesheet collisions exist."""
        # 1. Overlapping leaves
        overlaps = await self._leave_repo.get_overlapping_requests(
            user_id, start_date, end_date
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

        # 2. Existing timesheet entries
        if self._timesheet_repo:
            existing_timesheets = (
                await self._timesheet_repo.get_user_entries_for_date_range(
                    user_id, start_date, end_date
                )
            )
            if existing_timesheets:
                conflict_dates = sorted(
                    {t.work_date.isoformat() for t in existing_timesheets}
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "TIMESHEET_ALREADY_EXISTS_FOR_DATE",
                        "message": f"Cannot apply leave for date(s) with an existing timesheet: {', '.join(conflict_dates)}.",
                        "details": {"conflicting_dates": conflict_dates},
                    },
                )

    def _determine_auto_approval(
        self, leave_type: LeaveType, total_working_days: float
    ) -> bool:
        """Determine if a leave request qualifies for auto-approval."""
        if not leave_type.requires_approval:
            return True
        if (
            leave_type.auto_approve_threshold > 0
            and total_working_days <= leave_type.auto_approve_threshold
        ):
            return True
        return False

    async def _notify_leave_submission(
        self,
        user_id: uuid.UUID,
        leave_type_name: str,
        total_working_days: float,
        start_date: date,
        end_date: date,
    ) -> None:
        """Dispatch submission notification to managers and admins."""
        if self._notif_service and self._user_repo:
            try:
                applicant_user = await self._user_repo.get_with_details(user_id)
                if applicant_user:
                    await self._notif_service.notify_leave_submitted(
                        applicant=applicant_user,
                        leave_type_name=leave_type_name,
                        total_days=total_working_days,
                        start_date=start_date,
                        end_date=end_date,
                    )
            except Exception:
                pass

    async def apply_leave(
        self, user_id: uuid.UUID, payload: LeaveRequestCreate
    ) -> LeaveRequestRead:
        """Submit a leave request with dynamic validation, decomposition, and settlement."""
        self._validate_date_range(payload.start_date, payload.end_date)
        leave_type = await self._resolve_leave_type(payload.leave_type_id)
        await self._check_conflicts(user_id, payload.start_date, payload.end_date)

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

        is_auto_approved = self._determine_auto_approval(leave_type, total_working_days)
        leave_status = LeaveStatus.APPROVED if is_auto_approved else LeaveStatus.PENDING

        working_days_items = self.generate_working_day_items(
            payload.start_date,
            payload.end_date,
            payload.half_day_type,
            holiday_dates,
            initial_status=leave_status.value,
        )

        new_request = LeaveRequest(
            user_id=user_id,
            leave_type_id=leave_type.id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            half_day_type=payload.half_day_type,
            total_days=total_working_days,
            status=leave_status,
            is_auto_approved=is_auto_approved,
            reason=payload.reason,
            extra_metadata={"settled": False, "days": working_days_items},
        )
        await self._leave_repo.create(new_request)

        # If auto-approved and start_date has already arrived, settle immediately
        if is_auto_approved and payload.start_date <= date.today():
            await self.settle_daily_leaves(target_date=date.today())

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
                    "day_count": len(working_days_items),
                },
            )
            await self._audit_repo.create_log(audit)

        await self._notify_leave_submission(
            user_id=user_id,
            leave_type_name=leave_type.name.value,
            total_working_days=total_working_days,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )

        return LeaveRequestRead.model_validate(created_details)

    async def list_leave_requests(
        self,
        page: int = 1,
        limit: int = 20,
        user_id: Optional[uuid.UUID] = None,
        user_ids: Optional[list[uuid.UUID]] = None,
        exclude_user_id: Optional[uuid.UUID] = None,
        leave_status: Optional[LeaveStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> tuple[list[LeaveRequestRead], int]:
        offset = (page - 1) * limit
        requests, total = await self._leave_repo.search_leave_requests(
            offset=offset,
            limit=limit,
            user_id=user_id,
            user_ids=user_ids,
            exclude_user_id=exclude_user_id,
            status=leave_status,
            start_date=start_date,
            end_date=end_date,
        )
        return [LeaveRequestRead.model_validate(r) for r in requests], total

    async def get_leave_request(
        self, request_id: uuid.UUID
    ) -> Optional[LeaveRequestRead]:
        req = await self._leave_repo.get_leave_request_with_details(request_id)
        if not req:
            return None
        return LeaveRequestRead.model_validate(req)

    # -------------------------------------------------------------------------
    # Status Updates & Partial Decisions
    # -------------------------------------------------------------------------

    def _validate_status_update_permissions(
        self,
        leave_request: LeaveRequest,
        approver_id: uuid.UUID,
        new_status: LeaveStatus,
    ) -> None:
        """Validate that the request can be transitioned and is not self-approved."""
        if new_status not in [LeaveStatus.APPROVED, LeaveStatus.REJECTED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_STATUS_UPDATE",
                    "message": "Leave request status can only be updated to APPROVED or REJECTED.",
                },
            )
        if leave_request.user_id == approver_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SELF_APPROVAL_NOT_ALLOWED",
                    "message": "You cannot approve or reject your own leave request. It must be reviewed by your manager or an administrator.",
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

    def _apply_partial_decision(
        self,
        days: list[dict],
        approved_dates: Optional[list[date]],
        rejected_dates: Optional[list[date]],
        rejection_reason: Optional[str],
    ) -> tuple[float, list[dict]]:
        """Compute approved days and update individual day item statuses for partial decision."""
        rejected_set = {
            (d.isoformat() if hasattr(d, "isoformat") else str(d))
            for d in (rejected_dates or [])
        }
        approved_set = {
            (d.isoformat() if hasattr(d, "isoformat") else str(d))
            for d in (approved_dates or [])
        }

        approved_days_count = 0.0
        for d_item in days:
            d_date = d_item.get("date")
            if d_date in rejected_set or (approved_set and d_date not in approved_set):
                d_item["day_status"] = LeaveStatus.REJECTED.value
                d_item["rejection_reason"] = (
                    rejection_reason or "Day rejected by manager"
                )
            else:
                d_item["day_status"] = LeaveStatus.APPROVED.value
                approved_days_count += float(d_item.get("total_days", 1.0))

        return approved_days_count, days

    async def _notify_status_decision(
        self,
        leave_request: LeaveRequest,
        approver_id: uuid.UUID,
        new_status: str,
        comments: Optional[str],
    ) -> None:
        """Dispatch notification to employee on manager approval or rejection decision."""
        if self._notif_service and self._user_repo:
            try:
                approver_user = await self._user_repo.get_with_details(approver_id)
                approver_name = (
                    f"{approver_user.first_name} {approver_user.last_name}".strip()
                    if approver_user
                    else "Manager"
                )
                leave_type_name = (
                    leave_request.leave_type.name.value
                    if leave_request.leave_type
                    else "leave"
                )
                await self._notif_service.notify_leave_status_updated(
                    applicant_id=leave_request.user_id,
                    approver_name=approver_name,
                    leave_type_name=leave_type_name,
                    status_str=new_status,
                    comments=comments,
                )
            except Exception:
                pass

    async def update_leave_status(
        self,
        request_id: uuid.UUID,
        payload: LeaveStatusUpdatePayload,
        approver_id: uuid.UUID,
    ) -> LeaveRequestRead:
        """Approve, reject, or partially approve/reject a pending leave request."""
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
        self._validate_status_update_permissions(
            leave_request, approver_id, payload.status
        )

        meta = dict(leave_request.extra_metadata or {})
        days = list(meta.get("days", []))

        # Handle Day-Wise Partial Decision
        if payload.rejected_dates or payload.approved_dates:
            approved_count, days = self._apply_partial_decision(
                days,
                payload.approved_dates,
                payload.rejected_dates,
                payload.rejection_reason,
            )
            if approved_count > 0:
                leave_request.status = LeaveStatus.APPROVED
                leave_request.total_days = approved_count
            else:
                leave_request.status = LeaveStatus.REJECTED
                leave_request.total_days = 0.0
            meta["partial_decision"] = True
        else:
            for d_item in days:
                d_item["day_status"] = payload.status.value
                if payload.status == LeaveStatus.REJECTED:
                    d_item["rejection_reason"] = payload.rejection_reason
            leave_request.status = payload.status

        meta["days"] = days
        leave_request.extra_metadata = meta
        leave_request.approver_id = approver_id
        if payload.rejection_reason:
            leave_request.rejection_reason = payload.rejection_reason

        await self._leave_repo.save_request(leave_request)

        # If approved and date has already arrived/passed, settle immediately
        if (
            leave_request.status == LeaveStatus.APPROVED
            and leave_request.start_date <= date.today()
        ):
            await self.settle_daily_leaves(target_date=date.today())

        # Audit log entry
        approval_audit = LeaveApproval(
            leave_request_id=request_id,
            approver_id=approver_id,
            tier_level=1,
            status=leave_request.status,
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
                    "new_status": leave_request.status.value,
                    "target_user_id": str(leave_request.user_id),
                    "partial": meta.get("partial_decision", False),
                },
            )
            await self._audit_repo.create_log(audit)

        await self._notify_status_decision(
            leave_request=leave_request,
            approver_id=approver_id,
            new_status=payload.status.value,
            comments=payload.comments or payload.rejection_reason,
        )

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

    async def create_holiday(
        self, payload: HolidayCreatePayload, user_id: Optional[uuid.UUID] = None
    ) -> HolidayRead:
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
                user_id=user_id,
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
        self,
        payload: LeaveAccrualPolicyCreatePayload,
        user_id: Optional[uuid.UUID] = None,
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
                user_id=user_id,
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
                    "accrual_rate": float(payload.accrual_rate),
                },
            )
            await self._audit_repo.create_log(audit)

        return LeaveAccrualPolicyRead.model_validate(policy)

    async def list_accrual_policies(self) -> list[LeaveAccrualPolicyRead]:
        policies = await self._leave_repo.get_active_accrual_policies()
        return [LeaveAccrualPolicyRead.model_validate(p) for p in policies]

    async def grant_manual_allocation(
        self,
        payload: ManualAllocationGrantPayload,
        granter_id: Optional[uuid.UUID] = None,
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
            alloc.allocated_days = float(alloc.allocated_days) + float(
                payload.granted_days
            )

        await self._leave_repo.save_allocation(alloc)
        saved = await self._leave_repo.get_allocation_for_type(
            payload.user_id, payload.leave_type_id, payload.year
        )

        # Audit log for manual grant
        audit_entry = AuditLog(
            user_id=granter_id or payload.user_id,
            module=AuditModule.LEAVES.value,
            action=AuditAction.MANUAL_LEAVE_GRANT.value,
            entity=AuditEntity.LEAVE_ALLOCATION.value,
            entity_id=saved.id if saved else None,
            extra_metadata={
                "granted_days": float(payload.granted_days),
                "reason": payload.reason,
                "year": payload.year,
                "new_allocated_days": float(saved.allocated_days) if saved else None,
            },
        )
        await self._audit_repo.create_log(audit_entry)

        alloc_read = LeaveAllocationRead.model_validate(saved)
        if saved:
            alloc_read.remaining_days = float(
                saved.allocated_days + saved.comp_off_credits
            ) - float(saved.used_days)

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
                    prev_allocated = float(alloc.allocated_days)
                    new_allocation = prev_allocated + float(policy.accrual_rate)
                    if policy.max_quota is not None:
                        new_allocation = min(float(policy.max_quota), new_allocation)

                    alloc.allocated_days = new_allocation
                    alloc.last_accrual_date = today
                    await self._leave_repo.save_allocation(alloc)

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
                            "accrual_rate": float(policy.accrual_rate),
                            "previous_allocated_days": prev_allocated,
                            "new_allocated_days": float(alloc.allocated_days),
                            "accrual_date": today.isoformat(),
                        },
                    )
                    await self._audit_repo.create_log(accrual_audit)
                    total_accrued_count += 1

        return total_accrued_count

    async def _ensure_allocation_exists(
        self, req: LeaveRequest, year: int
    ) -> Optional[LeaveAllocation]:
        """Fetch or create/seed allocation for leave request user/type/year."""
        if not req.leave_type or req.leave_type.name == LeaveTypeEnum.UNPAID:
            return None

        alloc = await self._leave_repo.get_allocation_for_type(
            req.user_id,
            req.leave_type_id,
            year,
            for_update=True,
        )
        if not alloc:
            alloc = LeaveAllocation(
                user_id=req.user_id,
                leave_type_id=req.leave_type_id,
                year=year,
                allocated_days=float(req.leave_type.default_quota)
                if req.leave_type and req.leave_type.default_quota
                else 0.0,
                used_days=0.0,
                comp_off_credits=0.0,
            )
            alloc = await self._leave_repo.save_allocation(alloc)
        return alloc

    async def _settle_day_item(
        self,
        req: LeaveRequest,
        d_item: dict,
        alloc: Optional[LeaveAllocation],
        current_date: date,
    ) -> bool:
        """Evaluate and settle a single day item, returning True if item was changed."""
        d_str = d_item.get("date")
        d_date = date.fromisoformat(d_str) if d_str else None
        if not d_date:
            return False

        # Only evaluate approved, unsettled days that have arrived
        if d_item.get("day_status") != LeaveStatus.APPROVED.value or d_item.get(
            "settled"
        ):
            return False
        if d_date > current_date:
            return False

        d_days = float(d_item.get("total_days", 1.0))

        # 1. Timesheet submitted on that date overrides leave
        if self._timesheet_repo:
            existing_ts = await self._timesheet_repo.get_by_user_and_date(
                req.user_id, d_date
            )
            if existing_ts:
                d_item["settled"] = True
                d_item["timesheet_override"] = True
                d_item["paid_days"] = 0.0
                d_item["lwp_days"] = 0.0
                d_item["settled_at"] = current_date.isoformat()
                return True

        # 2. Explicit Unpaid Leave
        if req.leave_type and req.leave_type.name == LeaveTypeEnum.UNPAID:
            d_item["settled"] = True
            d_item["paid_days"] = 0.0
            d_item["lwp_days"] = d_days
            d_item["auto_lwp_applied"] = True
            d_item["settled_at"] = current_date.isoformat()
            return True

        # 3. Dynamic Paid Leave Deduction
        if alloc:
            available_quota = max(
                0.0,
                float(alloc.allocated_days + alloc.comp_off_credits)
                - float(alloc.used_days),
            )
            if available_quota >= d_days:
                paid = d_days
                lwp = 0.0
            else:
                paid = available_quota
                lwp = round(d_days - paid, 2)

            alloc.used_days = float(alloc.used_days) + paid
            await self._leave_repo.save_allocation(alloc)

            d_item["settled"] = True
            d_item["paid_days"] = paid
            d_item["lwp_days"] = lwp
            d_item["auto_lwp_applied"] = lwp > 0
            d_item["settled_at"] = current_date.isoformat()
            return True

        return False

    async def settle_daily_leaves(self, target_date: Optional[date] = None) -> int:
        """
        Evaluates and settles approved leaves reaching target_date (default: today).
        Runs every night at midnight globally across the entire database or on demand.
        """
        current_date = target_date or date.today()
        year = current_date.year

        unsettled_requests = await self._leave_repo.get_unsettled_approved_requests(
            cutoff_date=current_date, year=year
        )

        settled_count = 0
        for req in unsettled_requests:
            meta = dict(req.extra_metadata or {})
            days = list(meta.get("days", []))

            if days:
                alloc = await self._ensure_allocation_exists(req, req.start_date.year)
                req_changed = False
                all_days_settled = True

                for d_item in days:
                    changed = await self._settle_day_item(
                        req, d_item, alloc, current_date
                    )
                    if changed:
                        req_changed = True

                    d_str = d_item.get("date")
                    d_date = date.fromisoformat(d_str) if d_str else None
                    if (
                        d_date
                        and d_date > current_date
                        and not d_item.get("settled")
                        and d_item.get("day_status") == LeaveStatus.APPROVED.value
                    ):
                        all_days_settled = False

                if req_changed:
                    meta["days"] = days
                    meta["paid_days"] = sum(
                        float(d.get("paid_days", 0.0)) for d in days
                    )
                    meta["lwp_days"] = sum(float(d.get("lwp_days", 0.0)) for d in days)
                    meta["auto_lwp_applied"] = meta["lwp_days"] > 0
                    meta["settled"] = all_days_settled
                    meta["settled_at"] = current_date.isoformat()
                    req.extra_metadata = meta
                    await self._leave_repo.save_request(req)
                    settled_count += 1
            else:
                req_days = float(req.total_days)

                if req.leave_type and req.leave_type.name == LeaveTypeEnum.UNPAID:
                    meta["settled"] = True
                    meta["paid_days"] = 0.0
                    meta["lwp_days"] = req_days
                    meta["auto_lwp_applied"] = True
                    meta["settled_at"] = current_date.isoformat()
                    req.extra_metadata = meta
                    await self._leave_repo.save_request(req)
                    settled_count += 1
                    continue

                alloc = await self._leave_repo.get_allocation_for_type(
                    req.user_id, req.leave_type_id, req.start_date.year, for_update=True
                )
                if alloc:
                    available_quota = max(
                        0.0,
                        float(alloc.allocated_days + alloc.comp_off_credits)
                        - float(alloc.used_days),
                    )
                    if available_quota >= req_days:
                        paid = req_days
                        lwp = 0.0
                    else:
                        paid = available_quota
                        lwp = round(req_days - paid, 2)

                    alloc.used_days = float(alloc.used_days) + paid
                    await self._leave_repo.save_allocation(alloc)

                    meta["settled"] = True
                    meta["paid_days"] = paid
                    meta["lwp_days"] = lwp
                    meta["auto_lwp_applied"] = lwp > 0
                    meta["settled_at"] = current_date.isoformat()
                    req.extra_metadata = meta
                    await self._leave_repo.save_request(req)
                    settled_count += 1

        return settled_count
