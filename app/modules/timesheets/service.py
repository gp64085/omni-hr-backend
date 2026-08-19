import uuid
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import HTTPException, status

from app.models.audit import AuditAction, AuditEntity, AuditLog, AuditModule
from app.models.timesheet import Timesheet
from app.models.user import User, UserRole
from app.modules.audit.repository import AuditLogRepository
from app.modules.notifications.service import NotificationService
from app.modules.projects.repository import ProjectRepository
from app.modules.timesheets.repository import TimesheetRepository
from app.modules.timesheets.schemas import (
    ProjectAllocationSchema,
    TimesheetEntryCreatePayload,
    TimesheetEntryRead,
    TimesheetEntryUpdatePayload,
    TimesheetStatusUpdatePayload,
    TimesheetSubmitPayload,
    WeeklyTimesheetSummaryRead,
)
from app.modules.users.repository import UserRepository


class TimesheetService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        timesheet_repository: TimesheetRepository,
        audit_repository: AuditLogRepository,
        notification_service: Optional[NotificationService] = None,
        user_repository: Optional[UserRepository] = None,
    ):
        self._project_repo = project_repository
        self._timesheet_repo = timesheet_repository
        self._audit_repo = audit_repository
        self._notif_service = notification_service
        self._user_repo = user_repository

    def _is_super_admin(self, user: Optional[User]) -> bool:
        if not user or not user.role:
            return False
        return user.role.name in [UserRole.SUPER_ADMIN.value, "super_admin"]

    def _validate_creation_dates_and_minutes(
        self, work_date: date, total_minutes: int
    ) -> None:
        today = date.today()
        if work_date > today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for future dates.",
            )
        if work_date < today - timedelta(days=7):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for dates older than 7 days.",
            )
        if total_minutes <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Timesheet duration must be greater than 0 minutes.",
            )
        if total_minutes > 24 * 60:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Total duration logged for a single day cannot exceed 24 hours (1440 minutes).",
            )

    async def _normalize_activity_summary(
        self,
        summary_raw: list[ProjectAllocationSchema],
        minutes_spent: Optional[int] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        default_mins = (
            minutes_spent if minutes_spent is not None and minutes_spent > 0 else 0
        )

        if not summary_raw or not isinstance(summary_raw, list):
            return [], default_mins

        normalized: list[dict[str, Any]] = []
        computed_minutes = 0

        for item in summary_raw:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump(mode="json")
            elif isinstance(item, dict):
                item_dict = dict(item)
            else:
                continue

            if "tasks" in item_dict and isinstance(item_dict["tasks"], list):
                norm_tasks: list[dict[str, Any]] = []
                proj_minutes = 0
                for task in item_dict["tasks"]:
                    if hasattr(task, "model_dump"):
                        task_dict = task.model_dump(mode="json")
                    elif isinstance(task, dict):
                        task_dict = dict(task)
                    else:
                        continue

                    raw_hours = float(task_dict.get("hours", 0.0) or 0.0)
                    raw_minutes = int(task_dict.get("minutes", 0) or 0)
                    formatted_time = task_dict.get("formatted_time")

                    if (
                        formatted_time
                        and isinstance(formatted_time, str)
                        and ":" in formatted_time
                    ):
                        parts = formatted_time.split(":")
                        try:
                            task_hours = int(parts[0])
                            task_minutes = int(parts[1])
                        except ValueError:
                            task_hours = int(raw_hours)
                            task_minutes = raw_minutes
                    elif raw_hours % 1 != 0:
                        task_total_minutes = round(raw_hours * 60)
                        task_hours = task_total_minutes // 60
                        task_minutes = task_total_minutes % 60
                    else:
                        task_hours = int(raw_hours)
                        task_minutes = raw_minutes

                    task_minutes_spent = task_hours * 60 + task_minutes
                    task_dict["hours"] = task_hours
                    task_dict["minutes"] = task_minutes
                    task_dict["formatted_time"] = f"{task_hours:02d}:{task_minutes:02d}"
                    proj_minutes += task_minutes_spent
                    norm_tasks.append(task_dict)

                item_dict["tasks"] = norm_tasks
                item_dict["total_minutes_spent"] = proj_minutes
                item_dict.pop("total_hours", None)
                computed_minutes += proj_minutes
            else:
                computed_minutes += int(item_dict.get("total_minutes_spent", 0) or 0)

            normalized.append(item_dict)

        final_minutes = (
            minutes_spent
            if minutes_spent is not None and minutes_spent > 0
            else computed_minutes
            if computed_minutes > 0
            else default_mins
        )
        return normalized, final_minutes

    async def _resolve_project_name(self, entry: Timesheet) -> Optional[str]:
        if entry.project_id:
            project = await self._project_repo.get_by_id(entry.project_id)
            if project:
                return project.name
        elif (
            isinstance(entry.activity_summary, list) and len(entry.activity_summary) > 0
        ):
            first_item = entry.activity_summary[0]
            if isinstance(first_item, dict):
                return first_item.get("project_name")
        return None

    async def _enrich_entries_with_project_names(
        self, entries: list[Timesheet]
    ) -> list[TimesheetEntryRead]:
        project_ids = {e.project_id for e in entries if e.project_id}
        project_map: dict[uuid.UUID, str] = {}
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

        return result_dtos

    async def _record_audit_log(
        self,
        user_id: uuid.UUID,
        action: AuditAction,
        entity_id: Optional[uuid.UUID],
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        audit_entry = AuditLog(
            user_id=user_id,
            action=action.value,
            module=AuditModule.TIMESHEETS.value,
            entity=AuditEntity.TIMESHEET.value,
            entity_id=entity_id,
            extra_metadata=extra_metadata,
        )
        await self._audit_repo.create(audit_entry)

    def _validate_status_transition(
        self,
        entry: Timesheet,
        approver_id: uuid.UUID,
        is_super: bool,
        payload: TimesheetStatusUpdatePayload,
    ) -> None:
        if entry.status == "approved" and not is_super:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only Super Admin can modify approved timesheets.",
            )

        if entry.user_id == approver_id and not is_super:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot approve or reject your own timesheet.",
            )

        if payload.status == "rejected" and not payload.rejection_reason:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Rejection reason is required when rejecting a timesheet.",
            )

    async def _dispatch_status_notification(
        self,
        entry: Timesheet,
        approver_user: Optional[User],
        payload: TimesheetStatusUpdatePayload,
    ) -> None:
        if not self._notif_service:
            return
        approver_name = (
            f"{approver_user.first_name} {approver_user.last_name}".strip()
            if approver_user
            else "Manager"
        )
        try:
            await self._notif_service.notify_timesheet_status_updated(
                employee_id=entry.user_id,
                approver_name=approver_name,
                work_date=entry.work_date,
                status_str=payload.status,
                comments=payload.rejection_reason,
            )
        except Exception:
            pass

    async def create_entry(
        self, user_id: uuid.UUID, payload: TimesheetEntryCreatePayload
    ) -> TimesheetEntryRead:
        normalized_summary, total_mins = await self._normalize_activity_summary(
            payload.activity_summary, minutes_spent=payload.total_minutes_spent
        )
        self._validate_creation_dates_and_minutes(payload.work_date, total_mins)

        existing_entry = await self._timesheet_repo.get_by_user_and_date(
            user_id, payload.work_date
        )

        if existing_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A timesheet entry already exists for {payload.work_date} (Status: '{existing_entry.status}'). You cannot create multiple entries for the same date.",
            )

        new_entry = Timesheet(
            user_id=user_id,
            project_id=payload.project_id,
            work_date=payload.work_date,
            total_minutes_spent=total_mins,
            is_billable=payload.is_billable,
            activity_summary=normalized_summary,
            status="submitted",
        )
        entry = await self._timesheet_repo.create(new_entry)

        await self._record_audit_log(
            user_id=user_id,
            action=AuditAction.TIMESHEET_CREATE,
            entity_id=entry.id,
            extra_metadata={
                "work_date": str(entry.work_date),
                "total_minutes_spent": entry.total_minutes_spent,
            },
        )

        if self._notif_service and self._user_repo:
            try:
                employee_user = await self._user_repo.get_with_details(user_id)
                if employee_user:
                    formatted_time = f"{total_mins // 60:02d}:{total_mins % 60:02d}"
                    await self._notif_service.notify_timesheet_submitted(
                        employee=employee_user,
                        work_date=payload.work_date,
                        formatted_time=formatted_time,
                    )
            except Exception:
                pass

        project_name = await self._resolve_project_name(entry)
        read_dto = TimesheetEntryRead.model_validate(entry)
        read_dto.project_name = project_name
        return read_dto

    async def create_batch_entries(
        self, user_id: uuid.UUID, payloads: list[TimesheetEntryCreatePayload]
    ) -> list[TimesheetEntryRead]:
        if not payloads:
            return []

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
                    proj = (
                        await self._project_repo.get_by_id(item.project_id)
                        if item.project_id
                        else None
                    )
                    item_mins = item.total_minutes_spent or 0
                    allocations.append(
                        {
                            "project_id": str(item.project_id)
                            if item.project_id
                            else None,
                            "project_name": proj.name if proj else None,
                            "tasks": [
                                {
                                    "summary": str(item.activity_summary),
                                    "hours": item_mins // 60,
                                    "minutes": item_mins % 60,
                                    "formatted_time": f"{item_mins // 60:02d}:{item_mins % 60:02d}",
                                }
                            ],
                            "total_minutes_spent": item_mins,
                        }
                    )

            consolidated_payload = TimesheetEntryCreatePayload(
                project_id=primary_project_id,
                work_date=target_date,
                total_minutes_spent=sum(
                    (a.get("total_minutes_spent", 0) if isinstance(a, dict) else 0)
                    for a in allocations
                ),
                activity_summary=allocations,
            )
            dto = await self.create_entry(user_id, consolidated_payload)
            created_dtos.append(dto)

        await self._record_audit_log(
            user_id=user_id,
            action=AuditAction.TIMESHEET_CREATE,
            entity_id=None,
            extra_metadata={"batch_count": len(created_dtos)},
        )
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

        if entry.status == "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot edit timesheet entry with status 'approved'.",
            )

        update_fields = payload.model_dump(exclude_unset=True)
        target_date = update_fields.get("work_date", entry.work_date)
        if target_date > date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot log timesheet for future dates.",
            )

        if (
            "activity_summary" in update_fields
            or "total_minutes_spent" in update_fields
        ):
            normalized_summary, total_mins = await self._normalize_activity_summary(
                update_fields.get("activity_summary", entry.activity_summary),
                minutes_spent=update_fields.get("total_minutes_spent"),
            )
            update_fields["activity_summary"] = normalized_summary
            update_fields["total_minutes_spent"] = total_mins

        target_mins = update_fields.get(
            "total_minutes_spent", entry.total_minutes_spent or 0
        )
        existing_mins = await self._timesheet_repo.get_user_daily_logged_minutes(
            user_id=user_id,
            target_date=target_date,
            exclude_entry_id=entry.id,
            for_update=True,
        )
        if existing_mins + target_mins > 1440:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total duration logged for {target_date} would exceed 24 hours (1440 minutes).",
            )

        if entry.status == "rejected":
            update_fields["status"] = "submitted"
            update_fields["rejection_reason"] = None

        updated_entry = await self._timesheet_repo.update(entry, update_fields)
        await self._record_audit_log(
            user_id=user_id,
            action=AuditAction.TIMESHEET_UPDATE,
            entity_id=updated_entry.id,
            extra_metadata={"updated_fields": list(update_fields.keys())},
        )

        project_name = await self._resolve_project_name(updated_entry)
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

        if entry.status == "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete timesheet entry with status 'approved'.",
            )

        await self._timesheet_repo.delete(entry)
        await self._record_audit_log(
            user_id=user_id,
            action=AuditAction.TIMESHEET_DELETE,
            entity_id=entry_id,
        )

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
        dtos = await self._enrich_entries_with_project_names(list(entries))
        return dtos, total

    async def list_user_or_team_entries(
        self,
        current_user: User,
        requested_user_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_status: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TimesheetEntryRead], int]:
        is_super = self._is_super_admin(current_user)

        if requested_user_id:
            authorized_user_ids = (
                await self._user_repo.get_authorized_viewable_user_ids(
                    current_user, requested_user_id
                )
                if self._user_repo
                else [requested_user_id]
            )
            return await self.list_entries(
                user_id=None,
                user_ids=authorized_user_ids,
                project_id=project_id,
                start_date=start_date,
                end_date=end_date,
                entry_status=entry_status,
                offset=offset,
                limit=limit,
            )

        if entry_status == "submitted":
            if is_super:
                return await self.list_entries(
                    user_id=None,
                    user_ids=None,
                    exclude_user_id=None,
                    project_id=project_id,
                    start_date=start_date,
                    end_date=end_date,
                    entry_status=entry_status,
                    offset=offset,
                    limit=limit,
                )

            authorized_user_ids = (
                await self._user_repo.get_authorized_viewable_user_ids(
                    current_user, None
                )
                if self._user_repo
                else None
            )

            if authorized_user_ids is not None:
                filtered_user_ids = [
                    uid for uid in authorized_user_ids if uid != current_user.id
                ]
                if not filtered_user_ids:
                    return [], 0
                return await self.list_entries(
                    user_id=None,
                    user_ids=filtered_user_ids,
                    exclude_user_id=current_user.id,
                    project_id=project_id,
                    start_date=start_date,
                    end_date=end_date,
                    entry_status=entry_status,
                    offset=offset,
                    limit=limit,
                )

            return await self.list_entries(
                user_id=None,
                user_ids=None,
                exclude_user_id=current_user.id,
                project_id=project_id,
                start_date=start_date,
                end_date=end_date,
                entry_status=entry_status,
                offset=offset,
                limit=limit,
            )

        return await self.list_entries(
            user_id=current_user.id,
            user_ids=None,
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
            entry_status=entry_status,
            offset=offset,
            limit=limit,
        )

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

        await self._record_audit_log(
            user_id=user_id,
            action=AuditAction.TIMESHEET_SUBMIT,
            entity_id=None,
            extra_metadata={
                "submitted_count": updated_count,
                "start_date": str(payload.start_date),
                "end_date": str(payload.end_date),
            },
        )
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

        approver_user = (
            await self._user_repo.get_with_details(approver_id)
            if self._user_repo
            else None
        )
        is_super = self._is_super_admin(approver_user)
        self._validate_status_transition(entry, approver_id, is_super, payload)

        update_data = {
            "status": payload.status,
            "approver_id": approver_id,
            "rejection_reason": payload.rejection_reason
            if payload.status == "rejected"
            else None,
        }
        await self._timesheet_repo.update(entry, update_data)

        await self._record_audit_log(
            user_id=approver_id,
            action=AuditAction.TIMESHEET_STATUS_UPDATE,
            entity_id=entry.id,
            extra_metadata={
                "new_status": payload.status,
                "rejection_reason": payload.rejection_reason,
            },
        )

        await self._dispatch_status_notification(entry, approver_user, payload)

        project_name = await self._resolve_project_name(entry)
        dto = TimesheetEntryRead.model_validate(entry)
        dto.project_name = project_name
        return dto

    async def get_weekly_summary(
        self, user_id: uuid.UUID, start_date: date, end_date: date
    ) -> WeeklyTimesheetSummaryRead:
        entries = await self._timesheet_repo.get_user_entries_for_date_range(
            user_id=user_id, start_date=start_date, end_date=end_date
        )

        total_mins = sum((entry.total_minutes_spent or 0) for entry in entries)

        status_breakdown: dict[str, int] = {}
        for entry in entries:
            status_breakdown[entry.status] = status_breakdown.get(entry.status, 0) + 1

        return WeeklyTimesheetSummaryRead(
            start_date=start_date,
            end_date=end_date,
            total_minutes_spent=total_mins,
            entries_count=len(entries),
            status_breakdown=status_breakdown,
        )
