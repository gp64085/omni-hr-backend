import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PlatformType(str, Enum):
    SLACK = "slack"
    GROQ = "groq"


def mask_secret_string(val: str) -> str:
    """Mask a secret key showing only prefix and suffix if long enough."""
    if not val:
        return ""
    if len(val) <= 8:
        return "***"
    return f"{val[:6]}...{val[-4:]}"


def mask_credentials_dict(creds: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of credentials dictionary with sensitive values masked."""
    masked: dict[str, Any] = {}
    for k, v in creds.items():
        if isinstance(v, str):
            if any(
                term in k.lower() for term in ["token", "secret", "key", "password"]
            ):
                masked[k] = mask_secret_string(v)
            else:
                masked[k] = v
        else:
            masked[k] = v
    return masked


class SaveIntegrationRequest(BaseModel):
    is_enabled: bool = True
    credentials: dict[str, Any] = Field(default_factory=dict)


class IntegrationResponse(BaseModel):
    id: uuid.UUID
    platform: str
    is_enabled: bool
    credentials: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TestConnectionRequest(BaseModel):
    credentials: Optional[dict[str, Any]] = None


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    details: Optional[dict[str, Any]] = None
