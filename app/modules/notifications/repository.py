import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(Notification, database_session)

    async def list_user_notifications(
        self, user_id: uuid.UUID, limit: int = 30, unread_only: bool = False
    ) -> Sequence[Notification]:
        query = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            query = query.where(Notification.is_read.is_(False))
        query = query.order_by(Notification.created_at.desc()).limit(limit)
        query_result = await self._database_session.execute(query)
        return query_result.scalars().all()

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        query = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )
        query_result = await self._database_session.execute(query)
        return query_result.scalar() or 0

    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[Notification]:
        query = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        query_result = await self._database_session.execute(query)
        notification = query_result.scalar_one_or_none()
        if notification:
            notification.is_read = True
            await self._database_session.flush()
        return notification

    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        statement = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        query_result = await self._database_session.execute(statement)
        await self._database_session.flush()
        return int(getattr(query_result, "rowcount", 0) or 0)

    async def create_notification(self, notification: Notification) -> Notification:
        self._database_session.add(notification)
        await self._database_session.flush()
        return notification

    async def create_bulk_notifications(
        self, notifications: list[Notification]
    ) -> list[Notification]:
        if not notifications:
            return []
        self._database_session.add_all(notifications)
        await self._database_session.flush()
        return notifications
