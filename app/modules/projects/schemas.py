import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DepartmentReference(BaseModel):
    id: uuid.UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class ProjectCreatePayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    code: str = Field(..., min_length=2, max_length=50)
    department_ids: list[uuid.UUID] = Field(default_factory=list)
    is_active: bool = True


class ProjectUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=150)
    code: Optional[str] = Field(None, min_length=2, max_length=50)
    department_ids: Optional[list[uuid.UUID]] = None
    is_active: Optional[bool] = None


class ProjectRead(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    departments: list[DepartmentReference] = Field(default_factory=list)
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
