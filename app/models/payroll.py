import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import UUID, DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class PayRunStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SalaryStructure(Base):
    __tablename__ = "salary_structures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    base_salary: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    allowances: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    deductions: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship("User", back_populates="salary_structure")


class PayRun(Base):
    __tablename__ = "pay_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pay_month: Mapped[str] = mapped_column(
        String(7), unique=True, nullable=False
    )  # 'YYYY-MM'
    status: Mapped[PayRunStatus] = mapped_column(
        SQLEnum(PayRunStatus), default=PayRunStatus.DRAFT, nullable=False
    )
    executed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Payslip(Base):
    __tablename__ = "payslips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pay_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pay_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    base_pay: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    allowances: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    lop_deductions: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    other_deductions: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    gross_pay: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    net_pay: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    pdf_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
