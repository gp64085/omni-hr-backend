import uuid
from datetime import date
from typing import Any, Optional

from app.models.audit import AuditLog
from app.modules.audit.repository import AuditLogRepository
from app.modules.audit.schemas import AuditLogRead


class AuditLogService:
    def __init__(
        self,
        audit_repository: AuditLogRepository,
    ):
        self._audit_repo = audit_repository

    async def log_event(
        self,
        module: str,
        action: str,
        entity: str,
        user_id: Optional[uuid.UUID] = None,
        entity_id: Optional[uuid.UUID] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> AuditLogRead:
        log_entry = AuditLog(
            user_id=user_id,
            module=module,
            action=action,
            entity=entity,
            entity_id=entity_id,
            extra_metadata=extra_metadata,
        )
        saved = await self._audit_repo.create_log(log_entry)
        return AuditLogRead.model_validate(saved)

    async def list_audit_logs(
        self,
        page: int = 1,
        limit: int = 20,
        module: Optional[str] = None,
        entity: Optional[str] = None,
        action: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> tuple[list[AuditLogRead], int]:
        offset = (page - 1) * limit
        records, total = await self._audit_repo.search_audit_logs(
            offset=offset,
            limit=limit,
            module=module,
            entity=entity,
            action=action,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )
        return [AuditLogRead.model_validate(r) for r in records], total
