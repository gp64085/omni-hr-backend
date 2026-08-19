import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.leave import AccrualFrequency, HalfDayType, LeaveStatus, LeaveTypeEnum


class LeaveTypeRead(BaseModel):
    id: uuid.UUID
    name: LeaveTypeEnum
    default_quota: float
    requires_approval: bool
    auto_approve_threshold: int
    model_config = ConfigDict(from_attributes=True)


class LeaveAccrualPolicyCreatePayload(BaseModel):
    leave_type_id: uuid.UUID
    designation_id: Optional[uuid.UUID] = None
    frequency: AccrualFrequency = AccrualFrequency.MONTHLY
    accrual_rate: float = Field(..., gt=0)
    max_quota: Optional[float] = None
    is_active: bool = True


class LeaveAccrualPolicyRead(BaseModel):
    id: uuid.UUID
    leave_type_id: uuid.UUID
    leave_type: Optional[LeaveTypeRead] = None
    designation_id: Optional[uuid.UUID] = None
    frequency: AccrualFrequency
    accrual_rate: float
    max_quota: Optional[float] = None
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ManualAllocationGrantPayload(BaseModel):
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    year: int
    granted_days: float = Field(..., gt=0)
    reason: Optional[str] = None


class LeaveAllocationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    leave_type: Optional[LeaveTypeRead] = None
    year: int
    allocated_days: float
    used_days: float
    comp_off_credits: float
    remaining_days: float = 0.0
    pending_days: float = 0.0
    scheduled_future_days: float = 0.0
    last_accrual_date: Optional[date] = None
    model_config = ConfigDict(from_attributes=True)


class LeaveRequestCreate(BaseModel):
    leave_type_id: Optional[uuid.UUID] = None
    start_date: date
    end_date: date
    half_day_type: HalfDayType = HalfDayType.NONE
    reason: Optional[str] = None


class UserBaseRead(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    model_config = ConfigDict(from_attributes=True)


class LeaveRequestRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user: Optional[UserBaseRead] = None
    leave_type_id: uuid.UUID
    leave_type: Optional[LeaveTypeRead] = None
    start_date: date
    end_date: date
    half_day_type: HalfDayType
    total_days: float
    status: LeaveStatus
    is_auto_approved: bool
    reason: Optional[str] = None
    approver_id: Optional[uuid.UUID] = None
    rejection_reason: Optional[str] = None
    extra_metadata: Optional[dict] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LeaveStatusUpdatePayload(BaseModel):
    status: LeaveStatus
    rejection_reason: Optional[str] = None
    comments: Optional[str] = None
    approved_dates: Optional[list[date]] = None
    rejected_dates: Optional[list[date]] = None


class HolidayCreatePayload(BaseModel):
    name: str = Field(..., max_length=150)
    holiday_date: date
    is_optional: bool = False
    description: Optional[str] = None


class HolidayRead(BaseModel):
    id: uuid.UUID
    name: str
    holiday_date: date
    is_optional: bool
    description: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditLogRead(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    module: Optional[str] = None
    action: str
    entity: str
    entity_id: Optional[uuid.UUID] = None
    extra_metadata: Optional[dict] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
