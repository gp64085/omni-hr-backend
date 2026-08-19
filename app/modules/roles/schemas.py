import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    module: str
    description: Optional[str] = None
    created_at: datetime


class PermissionCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=100)
    module: str = Field(..., min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class PermissionUpdate(BaseModel):
    code: Optional[str] = Field(None, min_length=2, max_length=100)
    module: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_system: bool
    permission_ids: list[uuid.UUID] = []
    created_at: datetime
    updated_at: datetime


class RoleWithPermissionsRead(RoleRead):
    permissions: list[PermissionRead] = []


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    permission_ids: list[uuid.UUID] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    permission_ids: Optional[list[uuid.UUID]] = None
