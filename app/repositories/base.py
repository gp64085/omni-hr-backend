import uuid
from typing import Any, Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic Base Repository providing standardized async CRUD and pagination operations."""

    def __init__(self, model_class: Type[ModelType], database_session: AsyncSession):
        self.model_class = model_class
        self._database_session = database_session

    async def get_by_id(self, entity_id: uuid.UUID) -> Optional[ModelType]:
        result = await self._database_session.execute(
            select(self.model_class).where(self.model_class.id == entity_id)  # type: ignore
        )
        return result.scalar_one_or_none()

    async def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        filter_conditions: Optional[list[Any]] = None,
    ) -> tuple[Sequence[ModelType], int]:
        query = select(self.model_class)
        if filter_conditions:
            for condition in filter_conditions:
                query = query.where(condition)

        count_query = select(func.count()).select_from(query.subquery())
        total_records = (
            await self._database_session.execute(count_query)
        ).scalar() or 0

        query = query.offset(offset).limit(limit)
        results = (await self._database_session.execute(query)).scalars().all()
        return results, total_records

    async def create(self, entity_instance: ModelType) -> ModelType:
        self._database_session.add(entity_instance)
        await self._database_session.flush()
        return entity_instance

    async def update(
        self, entity_instance: ModelType, update_data: dict[str, Any]
    ) -> ModelType:
        for field_name, field_value in update_data.items():
            if hasattr(entity_instance, field_name):
                setattr(entity_instance, field_name, field_value)
        await self._database_session.flush()
        return entity_instance

    async def delete(self, entity_instance: ModelType) -> None:
        await self._database_session.delete(entity_instance)
        await self._database_session.flush()
