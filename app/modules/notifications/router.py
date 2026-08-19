import uuid

from fastapi import Depends, HTTPException, Query, status

from app.api.deps import (
    ProtectedAPIRouter,
    get_current_user,
    get_notification_service,
)
from app.models.user import User
from app.modules.notifications.schemas import (
    NotificationCountRead,
    NotificationRead,
)
from app.modules.notifications.service import NotificationService
from app.schemas.common import StandardResponse

notifications_router = ProtectedAPIRouter()


@notifications_router.get(
    "",
    response_model=StandardResponse[list[NotificationRead]],
    response_model_exclude_none=True,
)
async def list_user_notifications(
    limit: int = Query(30, ge=1, le=100),
    unread_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    notification_service: NotificationService = Depends(get_notification_service),
):
    notifications = await notification_service.list_user_notifications(
        user_id=current_user.id, limit=limit, unread_only=unread_only
    )
    return StandardResponse.ok(data=notifications)


@notifications_router.get(
    "/unread-count",
    response_model=StandardResponse[NotificationCountRead],
    response_model_exclude_none=True,
)
async def get_unread_notification_count(
    current_user: User = Depends(get_current_user),
    notification_service: NotificationService = Depends(get_notification_service),
):
    count = await notification_service.get_unread_count(current_user.id)
    return StandardResponse.ok(data=NotificationCountRead(unread_count=count))


@notifications_router.patch(
    "/read-all",
    response_model=StandardResponse[dict],
    response_model_exclude_none=True,
)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    notification_service: NotificationService = Depends(get_notification_service),
):
    count = await notification_service.mark_all_as_read(current_user.id)
    return StandardResponse.ok(
        data={"message": f"Marked {count} notifications as read."}
    )


@notifications_router.patch(
    "/{notification_id}/read",
    response_model=StandardResponse[NotificationRead],
    response_model_exclude_none=True,
)
async def mark_notification_as_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    notification_service: NotificationService = Depends(get_notification_service),
):
    updated = await notification_service.mark_as_read(
        notification_id=notification_id, user_id=current_user.id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )
    return StandardResponse.ok(data=updated)
