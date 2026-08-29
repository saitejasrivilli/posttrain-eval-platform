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
    attempt_number: int = 0
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobList(BaseModel):
    items: list[JobOut]
    limit: int
    offset: int
    total: int


class AttemptOut(BaseModel):
    job_id: uuid.UUID
    attempt_number: int
    worker_id: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    error_classification: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DLQEntryOut(BaseModel):
    job_id: uuid.UUID
    moved_to_dlq_at: datetime
    last_attempt_number: int
    last_error_message: Optional[str] = None
    last_error_classification: str
    total_attempts: int

    model_config = ConfigDict(from_attributes=True)


class DLQList(BaseModel):
    items: list[DLQEntryOut]
    limit: int
    offset: int
    total: int
