import enum
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    UUID,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.organization import Designation
from app.models.user import User


class LeaveTypeEnum(str, enum.Enum):
    CASUAL = "casual"
    SICK = "sick"
    EARNED = "earned"
    UNPAID = "unpaid"


class LeaveStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AccrualFrequency(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"
    MANUAL = "manual"


class HalfDayType(str, enum.Enum):
    NONE = "none"
    FIRST_HALF = "first_half"
    SECOND_HALF = "second_half"


class LeaveType(Base):
    __tablename__ = "leave_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[LeaveTypeEnum] = mapped_column(
        SQLEnum(LeaveTypeEnum), unique=True, nullable=False
    )
    default_quota: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_approve_threshold: Mapped[int] = mapped_column(Integer, default=0)


class LeaveAccrualPolicy(Base):
    __tablename__ = "leave_accrual_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("designations.id", ondelete="CASCADE"),
        nullable=True,
    )
    frequency: Mapped[AccrualFrequency] = mapped_column(
        SQLEnum(AccrualFrequency), default=AccrualFrequency.MONTHLY, nullable=False
    )
    accrual_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    max_quota: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    leave_type: Mapped[Optional["LeaveType"]] = relationship(
        "LeaveType", lazy="selectin"
    )
    designation: Mapped[Optional["Designation"]] = relationship(
        "Designation", lazy="selectin"
    )


class LeaveAllocation(Base):
    __tablename__ = "leave_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_days: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    used_days: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0.00, nullable=False
    )
    comp_off_credits: Mapped[float] = mapped_column(
        Numeric(5, 2), default=0.00, nullable=False
    )
    last_accrual_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    leave_type: Mapped[Optional["LeaveType"]] = relationship(
        "LeaveType", lazy="selectin"
    )


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    half_day_type: Mapped[HalfDayType] = mapped_column(
        SQLEnum(HalfDayType), default=HalfDayType.NONE, nullable=False
    )
    total_days: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[LeaveStatus] = mapped_column(
        SQLEnum(LeaveStatus), default=LeaveStatus.PENDING, nullable=False
    )
    is_auto_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[user_id], lazy="selectin"
    )
    approver: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[approver_id], lazy="selectin"
    )
    leave_type: Mapped[Optional["LeaveType"]] = relationship(
        "LeaveType", lazy="selectin"
    )


class LeaveApproval(Base):
    __tablename__ = "leave_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    leave_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leave_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tier_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[LeaveStatus] = mapped_column(SQLEnum(LeaveStatus), nullable=False)
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
