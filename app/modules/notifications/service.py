import uuid
from datetime import date
from typing import Optional

from app.models.notification import Notification, NotificationType
from app.models.user import User, UserRole
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import NotificationRead
from app.modules.users.repository import UserRepository


class NotificationService:
    def __init__(
        self,
        notification_repository: NotificationRepository,
        user_repository: UserRepository,
    ):
        self._notification_repo = notification_repository
        self._user_repo = user_repository

    async def list_user_notifications(
        self, user_id: uuid.UUID, limit: int = 30, unread_only: bool = False
    ) -> list[NotificationRead]:
        notifications = await self._notification_repo.list_user_notifications(
            user_id=user_id, limit=limit, unread_only=unread_only
        )
        return [NotificationRead.model_validate(n) for n in notifications]

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        return await self._notification_repo.get_unread_count(user_id)

    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[NotificationRead]:
        updated = await self._notification_repo.mark_as_read(notification_id, user_id)
        if not updated:
            return None
        return NotificationRead.model_validate(updated)

    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        return await self._notification_repo.mark_all_as_read(user_id)

    async def notify_leave_submitted(
        self,
        applicant: User,
        leave_type_name: str,
        total_days: float,
        start_date: date,
        end_date: date,
    ) -> None:
        target_ids: set[uuid.UUID] = set()

        # Manager
        if applicant.manager_id and applicant.manager_id != applicant.id:
            target_ids.add(applicant.manager_id)

        # Super Admin & HR Managers
        admin_hr_ids = await self._user_repo.get_user_ids_by_role_names(
            [UserRole.SUPER_ADMIN.value, UserRole.HR_MANAGER.value]
        )
        target_ids.update(admin_hr_ids)
        target_ids.discard(applicant.id)

        applicant_name = f"{applicant.first_name} {applicant.last_name}".strip()
        date_str = (
            start_date.isoformat()
            if start_date == end_date
            else f"{start_date.isoformat()} to {end_date.isoformat()}"
        )
        title = f"New Leave Request: {applicant_name}"
        message = f"{applicant_name} submitted a request for {total_days:g}d {leave_type_name.upper()} ({date_str})."

        notifications = [
            Notification(
                user_id=t_id,
                title=title,
                message=message,
                type=NotificationType.LEAVE_REQUEST,
                link="/leaves",
            )
            for t_id in target_ids
        ]
        await self._notification_repo.create_bulk_notifications(notifications)

    async def notify_leave_status_updated(
        self,
        applicant_id: uuid.UUID,
        approver_name: str,
        leave_type_name: str,
        status_str: str,
        comments: Optional[str] = None,
    ) -> None:
        is_approved = status_str.lower() == "approved"
        notif_type = (
            NotificationType.LEAVE_APPROVAL
            if is_approved
            else NotificationType.LEAVE_REJECTION
        )
        title = f"Leave Request {status_str.title()}"
        comment_suffix = f' — Note: "{comments}"' if comments else ""
        message = f"Your {leave_type_name.upper()} leave was {status_str.lower()} by {approver_name}{comment_suffix}."

        notif = Notification(
            user_id=applicant_id,
            title=title,
            message=message,
            type=notif_type,
            link="/leaves",
        )
        await self._notification_repo.create_notification(notif)

    async def notify_timesheet_submitted(
        self,
        employee: User,
        work_date: date,
        total_hours: float,
    ) -> None:
        target_ids: set[uuid.UUID] = set()

        if employee.manager_id and employee.manager_id != employee.id:
            target_ids.add(employee.manager_id)

        admin_ids = await self._user_repo.get_user_ids_by_role_names(
            [UserRole.SUPER_ADMIN.value]
        )
        target_ids.update(admin_ids)
        target_ids.discard(employee.id)

        employee_name = f"{employee.first_name} {employee.last_name}".strip()
        title = f"Timesheet Submitted: {employee_name}"
        message = f"{employee_name} logged {total_hours:g}h of work for {work_date.isoformat()}."

        notifications = [
            Notification(
                user_id=t_id,
                title=title,
                message=message,
                type=NotificationType.TIMESHEET_SUBMISSION,
                link="/timesheets",
            )
            for t_id in target_ids
        ]
        await self._notification_repo.create_bulk_notifications(notifications)

    async def notify_timesheet_status_updated(
        self,
        employee_id: uuid.UUID,
        approver_name: str,
        work_date: date,
        status_str: str,
        comments: Optional[str] = None,
    ) -> None:
        is_approved = status_str.lower() == "approved"
        notif_type = (
            NotificationType.TIMESHEET_APPROVAL
            if is_approved
            else NotificationType.TIMESHEET_REJECTION
        )
        title = f"Timesheet {status_str.title()}"
        comment_suffix = f' — Note: "{comments}"' if comments else ""
        message = f"Your timesheet entry for {work_date.isoformat()} was {status_str.lower()} by {approver_name}{comment_suffix}."

        notif = Notification(
            user_id=employee_id,
            title=title,
            message=message,
            type=notif_type,
            link="/timesheets",
        )
        await self._notification_repo.create_notification(notif)
