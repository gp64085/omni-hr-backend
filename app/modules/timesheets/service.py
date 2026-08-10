import uuid
from datetime import date
from typing import Optional

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.timesheet import TimesheetEntry
from app.modules.audit.repository import AuditLogRepository
from app.modules.projects.repository import ProjectRepository
from app.modules.timesheets.repository import TimesheetRepository
from app.modules.timesheets.schemas import (
    TimesheetEntryCreatePayload,
    TimesheetEntryRead,
    TimesheetEntryUpdatePayload,
    TimesheetStatusUpdatePayload,
    TimesheetSubmitPayload,
    WeeklyTimesheetSummaryRead,
)


class TimesheetService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        timesheet_repository: TimesheetRepository,
        audit_repository: AuditLogRepository,
    ):
        self._project_repo = project_repository
        self._timesheet_repo = timesheet_repository
        self._audit_repo = audit_repository

    async def create_entry(
        self, user_id: uuid.UUID, payload: TimesheetEntryCreatePayload
    ) -> TimesheetEntryRead:
        if payload.project_id:
            project = await self._project_repo.get_by_id(payload.project_id)
            if not project or not project.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Specified project is invalid or inactive.",
                )

        existing_hours = float(
            await self._timesheet_repo.get_user_daily_logged_hours(
                user_id=user_id, target_date=payload.work_date, for_update=True
            )
        )
        if existing_hours + float(payload.hours_spent) > 24.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total hours logged for {payload.work_date} would exceed 24 hours. (Already logged: {existing_hours} hrs)",
            )

        new_entry = TimesheetEntry(
            user_id=user_id,
            project_id=payload.project_id,
            work_date=payload.work_date,
            hours_spent=payload.hours_spent,
            is_billable=payload.is_billable,
            activity_summary=payload.activity_summary,
            status="draft",
        )
        created_entry = await self._timesheet_repo.create(new_entry)

        audit_entry = AuditLog(
            user_id=user_id,
            action=AuditAction.TIMESHEET_CREATE.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=created_entry.id,
            extra_metadata={
                "work_date": str(created_entry.work_date),
                "hours": created_entry.hours_spent,
            },
        )
        await self._audit_repo.create_log(audit_entry)

        project_name = None
        if created_entry.project_id:
            project = await self._project_repo.get_by_id(created_entry.project_id)
            if project:
                project_name = project.name

        read_dto = TimesheetEntryRead.model_validate(created_entry)
        read_dto.project_name = project_name
        return read_dto

    async def update_entry(
        self,
        user_id: uuid.UUID,
        entry_id: uuid.UUID,
        payload: TimesheetEntryUpdatePayload,
    ) -> TimesheetEntryRead:
        entry = await self._timesheet_repo.get_by_id(entry_id)
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Timesheet entry with ID '{entry_id}' not found.",
            )

        if entry.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only edit your own timesheet entries.",
            )

        if entry.status in ("approved", "submitted"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit timesheet entry with status '{entry.status}'.",
            )

        update_fields = payload.model_dump(exclude_unset=True)
        target_date = update_fields.get("work_date", entry.work_date)
        target_hours = update_fields.get("hours_spent", entry.hours_spent)

        existing_hours = float(
            await self._timesheet_repo.get_user_daily_logged_hours(
                user_id=user_id,
                target_date=target_date,
                exclude_entry_id=entry.id,
                for_update=True,
            )
        )
        if existing_hours + float(target_hours) > 24.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total hours logged for {target_date} would exceed 24 hours.",
            )

        updated_entry = await self._timesheet_repo.update(entry, update_fields)

        project_name = None
        if updated_entry.project_id:
            project = await self._project_repo.get_by_id(updated_entry.project_id)
            if project:
                project_name = project.name

        read_dto = TimesheetEntryRead.model_validate(updated_entry)
        read_dto.project_name = project_name
        return read_dto

    async def delete_entry(self, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        entry = await self._timesheet_repo.get_by_id(entry_id)
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Timesheet entry with ID '{entry_id}' not found.",
            )

        if entry.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own timesheet entries.",
            )

        if entry.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete timesheet entry with status '{entry.status}'.",
            )

        await self._timesheet_repo.delete(entry)

    async def list_entries(
        self,
        user_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_status: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TimesheetEntryRead], int]:
        entries, total = await self._timesheet_repo.list_entries(
            user_id=user_id,
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
            entry_status=entry_status,
            offset=offset,
            limit=limit,
        )

        project_ids = {e.project_id for e in entries if e.project_id}
        project_map = {}
        if project_ids:
            for pid in project_ids:
                proj = await self._project_repo.get_by_id(pid)
                if proj:
                    project_map[pid] = proj.name

        result_dtos = []
        for entry in entries:
            dto = TimesheetEntryRead.model_validate(entry)
            if entry.project_id and entry.project_id in project_map:
                dto.project_name = project_map[entry.project_id]
            result_dtos.append(dto)

        return result_dtos, total

    async def submit_timesheets(
        self, user_id: uuid.UUID, payload: TimesheetSubmitPayload
    ) -> int:
        entries = await self._timesheet_repo.get_user_entries_for_date_range(
            user_id=user_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )

        draft_entries = [e for e in entries if e.status == "draft"]
        if not draft_entries:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No draft timesheet entries found in the specified date range.",
            )

        draft_ids = [e.id for e in draft_entries]
        updated_count = await self._timesheet_repo.bulk_update_status(
            entry_ids=draft_ids, new_status="submitted"
        )

        audit_entry = AuditLog(
            user_id=user_id,
            action=AuditAction.TIMESHEET_SUBMIT.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            extra_metadata={
                "submitted_count": updated_count,
                "start_date": str(payload.start_date),
                "end_date": str(payload.end_date),
            },
        )
        await self._audit_repo.create_log(audit_entry)
        return updated_count

    async def update_entry_status(
        self,
        approver_id: uuid.UUID,
        entry_id: uuid.UUID,
        payload: TimesheetStatusUpdatePayload,
    ) -> TimesheetEntryRead:
        entry = await self._timesheet_repo.get_by_id(entry_id)
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Timesheet entry with ID '{entry_id}' not found.",
            )

        if entry.status != "submitted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only submitted timesheet entries can be approved or rejected. Current status is '{entry.status}'.",
            )

        if payload.status not in ("approved", "rejected"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target status must be 'approved' or 'rejected'. Received '{payload.status}'.",
            )

        update_data = {
            "status": payload.status,
            "approver_id": approver_id,
            "rejection_reason": payload.rejection_reason
            if payload.status == "rejected"
            else None,
        }
        await self._timesheet_repo.update(entry, update_data)

        audit_entry = AuditLog(
            user_id=approver_id,
            action=AuditAction.TIMESHEET_STATUS_UPDATE.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=entry.id,
            extra_metadata={
                "new_status": payload.status,
                "rejection_reason": payload.rejection_reason,
            },
        )
        await self._audit_repo.create_log(audit_entry)

        project_name = None
        if entry.project_id:
            project = await self._project_repo.get_by_id(entry.project_id)
            if project:
                project_name = project.name

        dto = TimesheetEntryRead.model_validate(entry)
        dto.project_name = project_name
        return dto

    async def get_weekly_summary(
        self, user_id: uuid.UUID, start_date: date, end_date: date
    ) -> WeeklyTimesheetSummaryRead:
        entries = await self._timesheet_repo.get_user_entries_for_date_range(
            user_id=user_id, start_date=start_date, end_date=end_date
        )

        total_hours = sum(float(e.hours_spent) for e in entries)
        billable_hours = sum(float(e.hours_spent) for e in entries if e.is_billable)
        non_billable_hours = total_hours - billable_hours

        status_breakdown = {}
        for entry in entries:
            status_breakdown[entry.status] = status_breakdown.get(entry.status, 0) + 1

        return WeeklyTimesheetSummaryRead(
            start_date=start_date,
            end_date=end_date,
            total_hours=total_hours,
            billable_hours=billable_hours,
            non_billable_hours=non_billable_hours,
            entries_count=len(entries),
            status_breakdown=status_breakdown,
        )
