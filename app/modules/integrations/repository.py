from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationConfig
from app.repositories.base import BaseRepository


class IntegrationRepository(BaseRepository[IntegrationConfig]):
    def __init__(self, database_session: AsyncSession):
        super().__init__(IntegrationConfig, database_session)

    async def get_by_platform(self, platform: str) -> Optional[IntegrationConfig]:
        query = select(IntegrationConfig).where(
            IntegrationConfig.platform == platform.lower()
        )
        result = await self._database_session.execute(query)
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[IntegrationConfig]:
        query = select(IntegrationConfig).order_by(IntegrationConfig.platform)
        result = await self._database_session.execute(query)
        return result.scalars().all()
