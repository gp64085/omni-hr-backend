import uuid
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.timesheet import Timesheet
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
        notification_service: Optional[Any] = None,
        user_repository: Optional[Any] = None,
    ):
        self._project_repo = project_repository
        self._timesheet_repo = timesheet_repository
        self._audit_repo = audit_repository
        self._notif_service = notification_service
        self._user_repo = user_repository

    async def _normalize_activity_summary(
        self, summary_raw: Any, hours_spent: Optional[float] = None
    ) -> tuple[Any, float]:
        if not summary_raw:
            final_h = round(float(hours_spent or 0.0), 2)
            return [], final_h

        if isinstance(summary_raw, str):
            final_h = round(float(hours_spent or 0.0), 2)
            return summary_raw, final_h

        if not isinstance(summary_raw, list):
            final_h = round(float(hours_spent or 0.0), 2)
            return summary_raw, final_h

        normalized = []
        computed_hours = 0.0

        for item in summary_raw:
            if not isinstance(item, dict):
                continue
            item_dict = dict(item)

            if "tasks" in item_dict and isinstance(item_dict["tasks"], list):
                norm_tasks = []
                proj_hours = 0.0
                for t in item_dict["tasks"]:
                    if not isinstance(t, dict):
                        continue
                    t_dict = dict(t)
                    t_hrs = float(t_dict.get("hours", 0.0) or 0.0)
                    t_mins = int(t_dict.get("minutes", 0) or 0)
                    if t_mins and not t_hrs:
                        t_hrs = t_mins / 60.0
                    elif t_mins:
                        t_hrs = t_hrs + (t_mins / 60.0)
                    t_dict["hours"] = round(t_hrs, 2)
                    proj_hours += t_hrs
                    norm_tasks.append(t_dict)

                item_dict["tasks"] = norm_tasks
                item_dict["total_hours"] = round(proj_hours, 2)
                computed_hours += proj_hours
            else:
                t_hrs = float(
                    item_dict.get("hours", 0.0)
                    or item_dict.get("hours_spent", 0.0)
                    or 0.0
                )
                computed_hours += t_hrs

            normalized.append(item_dict)

        final_hours = (
            float(hours_spent)
            if hours_spent is not None and hours_spent > 0
            else round(computed_hours, 2)
        )
        return normalized, final_hours

    async def create_entry(
        self, user_id: uuid.UUID, payload: TimesheetEntryCreatePayload
    ) -> TimesheetEntryRead:
        if payload.work_date > date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for future dates.",
            )

        if payload.work_date < date.today() - timedelta(days=7):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for dates older than 7 days.",
            )

        normalized_summary, total_hours = await self._normalize_activity_summary(
            payload.activity_summary, payload.hours_spent
        )

        if total_hours <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Timesheet duration must be greater than 0 hours.",
            )

        if total_hours > 24.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Total hours logged for a single day cannot exceed 24 hours.",
            )

        # Check existing entry for user on that day
        existing_entry = await self._timesheet_repo.get_by_user_and_date(
            user_id, payload.work_date
        )

        if existing_entry:
            # Update existing daily entry with new consolidated allocations
            updated_entry = await self._timesheet_repo.update(
                existing_entry,
                {
                    "hours_spent": total_hours,
                    "activity_summary": normalized_summary,
                    "project_id": payload.project_id or existing_entry.project_id,
                    "status": "submitted",
                },
            )
            entry = updated_entry
        else:
            new_entry = Timesheet(
                user_id=user_id,
                project_id=payload.project_id,
                work_date=payload.work_date,
                hours_spent=total_hours,
                is_billable=payload.is_billable,
                activity_summary=normalized_summary,
                status="submitted",
            )
            entry = await self._timesheet_repo.create(new_entry)

        audit_entry = AuditLog(
            user_id=user_id,
            action=AuditAction.TIMESHEET_CREATE.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=entry.id,
            extra_metadata={
                "work_date": str(entry.work_date),
                "hours": entry.hours_spent,
            },
        )
        await self._audit_repo.create_log(audit_entry)

        if self._notif_service and self._user_repo:
            try:
                employee_user = await self._user_repo.get_with_details(user_id)
                if employee_user:
                    await self._notif_service.notify_timesheet_submitted(
                        employee=employee_user,
                        work_date=payload.work_date,
                        total_hours=total_hours,
                    )
            except Exception:
                pass

        project_name = None
        if entry.project_id:
            project = await self._project_repo.get_by_id(entry.project_id)
            if project:
                project_name = project.name
        elif (
            isinstance(entry.activity_summary, list) and len(entry.activity_summary) > 0
        ):
            project_name = entry.activity_summary[0].get("project_name")

        read_dto = TimesheetEntryRead.model_validate(entry)
        read_dto.project_name = project_name
        return read_dto

    async def create_batch_entries(
        self, user_id: uuid.UUID, payloads: list[TimesheetEntryCreatePayload]
    ) -> list[TimesheetEntryRead]:
        if not payloads:
            return []

        # If multiple individual project/task entries are passed, group by work_date
        date_groups: dict[date, list[TimesheetEntryCreatePayload]] = {}
        for p in payloads:
            date_groups.setdefault(p.work_date, []).append(p)

        created_dtos = []
        for target_date, items in date_groups.items():
            allocations = []
            primary_project_id = None

            for item in items:
                if item.project_id and not primary_project_id:
                    primary_project_id = item.project_id

                if (
                    isinstance(item.activity_summary, list)
                    and len(item.activity_summary) > 0
                ):
                    for block in item.activity_summary:
                        allocations.append(block)
                else:
                    proj_name = None
                    if item.project_id:
                        proj = await self._project_repo.get_by_id(item.project_id)
                        if proj:
                            proj_name = proj.name
                    allocations.append(
                        {
                            "project_id": str(item.project_id)
                            if item.project_id
                            else None,
                            "project_name": proj_name,
                            "tasks": [
                                {
                                    "summary": str(item.activity_summary),
                                    "hours": float(item.hours_spent or 0.0),
                                }
                            ],
                            "total_hours": float(item.hours_spent or 0.0),
                        }
                    )

            consolidated_payload = TimesheetEntryCreatePayload(
                project_id=primary_project_id,
                work_date=target_date,
                hours_spent=None,
                activity_summary=allocations,
            )
            dto = await self.create_entry(user_id, consolidated_payload)
            created_dtos.append(dto)

        audit_entry = AuditLog(
            user_id=user_id,
            action=AuditAction.TIMESHEET_CREATE.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=None,
            extra_metadata={"batch_count": len(created_dtos)},
        )
        await self._audit_repo.create_log(audit_entry)

        return created_dtos

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
        if target_date > date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for future dates.",
            )
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

        audit_entry = AuditLog(
            user_id=user_id,
            module=AuditModule.TIMESHEETS,
            action=AuditAction.TIMESHEET_UPDATE.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=updated_entry.id,
            extra_metadata={"updated_fields": list(update_fields.keys())},
        )
        await self._audit_repo.create_log(audit_entry)

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

        audit_entry = AuditLog(
            user_id=user_id,
            module=AuditModule.TIMESHEETS.value,
            action=AuditAction.TIMESHEET_DELETE.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=entry_id,
        )
        await self._audit_repo.create_log(audit_entry)

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
    ) -> tuple[list[TimesheetEntryRead], int]:
        entries, total = await self._timesheet_repo.list_entries(
            user_id=user_id,
            user_ids=user_ids,
            exclude_user_id=exclude_user_id,
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
            elif (
                isinstance(entry.activity_summary, list)
                and len(entry.activity_summary) > 0
            ):
                first_item = entry.activity_summary[0]
                if isinstance(first_item, dict):
                    dto.project_name = first_item.get("project_name")
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

        if entry.user_id == approver_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot approve or reject your own timesheet. It must be reviewed by your manager or an administrator.",
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

        if self._notif_service and self._user_repo:
            try:
                approver_user = await self._user_repo.get_with_details(approver_id)
                approver_name = (
                    f"{approver_user.first_name} {approver_user.last_name}".strip()
                    if approver_user
                    else "Manager"
                )
                await self._notif_service.notify_timesheet_status_updated(
                    employee_id=entry.user_id,
                    approver_name=approver_name,
                    work_date=entry.work_date,
                    status_str=payload.status,
                    comments=payload.rejection_reason,
                )
            except Exception:
                pass

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
