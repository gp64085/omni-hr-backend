import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, UUID, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AuditModule(str, enum.Enum):
    SYSTEM = "system"
    AUTH = "auth"
    USERS = "users"
    ROLES = "roles"
    LEAVES = "leaves"
    TIMESHEETS = "timesheets"
    PAYROLL = "payroll"
    ATTENDANCE = "attendance"
    EXPENSES = "expenses"
    HOLIDAYS = "holidays"


class AuditEntity(str, enum.Enum):
    USER = "user"
    ROLE = "role"
    PERMISSION = "permission"
    LEAVE_TYPE = "leave_type"
    LEAVE_ALLOCATION = "leave_allocation"
    LEAVE_REQUEST = "leave_request"
    LEAVE_POLICY = "leave_accrual_policy"
    HOLIDAY = "company_holiday"
    TIMESHEET = "timesheet_entry"
    PROJECT = "project"


class AuditAction(str, enum.Enum):
    USER_LOGIN = "USER_LOGIN"
    USER_LOGOUT = "USER_LOGOUT"
    TOKEN_REFRESH = "TOKEN_REFRESH"
    USER_CREATE = "USER_CREATE"
    USER_UPDATE = "USER_UPDATE"
    USER_DELETE = "USER_DELETE"
    PROFILE_UPDATE = "PROFILE_UPDATE"
    ROLE_CREATE = "ROLE_CREATE"
    ROLE_UPDATE = "ROLE_UPDATE"
    ROLE_DELETE = "ROLE_DELETE"
    LEAVE_APPLY = "LEAVE_APPLY"
    LEAVE_STATUS_UPDATE = "LEAVE_STATUS_UPDATE"
    LEAVE_CANCEL = "LEAVE_CANCEL"
    MANUAL_LEAVE_GRANT = "MANUAL_LEAVE_GRANT"
    PERIODIC_LEAVE_ACCRUAL = "PERIODIC_LEAVE_ACCRUAL"
    ACCRUAL_POLICY_CONFIGURED = "ACCRUAL_POLICY_CONFIGURED"
    HOLIDAY_CREATE = "HOLIDAY_CREATE"
    PROJECT_CREATE = "PROJECT_CREATE"
    PROJECT_UPDATE = "PROJECT_UPDATE"
    TIMESHEET_CREATE = "TIMESHEET_CREATE"
    TIMESHEET_UPDATE = "TIMESHEET_UPDATE"
    TIMESHEET_SUBMIT = "TIMESHEET_SUBMIT"
    TIMESHEET_STATUS_UPDATE = "TIMESHEET_STATUS_UPDATE"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    module: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    extra_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
