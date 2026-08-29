import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=128)
    config: Optional[dict[str, Any]] = None


class JobUpdate(BaseModel):
    status: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class JobOut(BaseModel):
    id: uuid.UUID
    job_type: str
    status: str
    config: Optional[dict[str, Any]] = None
    cancel_requested: bool = False
    claimed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobList(BaseModel):
    items: list[JobOut]
    limit: int
    offset: int
    total: int
