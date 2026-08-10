import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimesheetEntryCreatePayload(BaseModel):
    project_id: Optional[uuid.UUID] = None
    work_date: date
    hours_spent: float = Field(..., gt=0, le=24)
    is_billable: bool = True
    activity_summary: str = Field(..., min_length=3)


class TimesheetEntryUpdatePayload(BaseModel):
    project_id: Optional[uuid.UUID] = None
    work_date: Optional[date] = None
    hours_spent: Optional[float] = Field(None, gt=0, le=24)
    is_billable: Optional[bool] = None
    activity_summary: Optional[str] = Field(None, min_length=3)


class TimesheetStatusUpdatePayload(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected|submitted|draft)$")
    rejection_reason: Optional[str] = None


class TimesheetSubmitPayload(BaseModel):
    start_date: date
    end_date: date


class TimesheetEntryRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    work_date: date
    hours_spent: float
    is_billable: bool
    activity_summary: str
    status: str
    approver_id: Optional[uuid.UUID] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WeeklyTimesheetSummaryRead(BaseModel):
    start_date: date
    end_date: date
    total_hours: float
    billable_hours: float
    non_billable_hours: float
    entries_count: int
    status_breakdown: dict[str, int]
