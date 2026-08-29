import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=128)
    config: Optional[dict[str, Any]] = None
    priority: int = Field(default=50, ge=0, le=100)


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
    priority: int = 50
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobList(BaseModel):
    items: list[JobOut]
    limit: int
    offset: int
    total: int


class SchedulingDecisionOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    decided_at: datetime
    decision: str
    reason: str
    requested_cpu: int
    requested_memory_mb: int
    requested_gpu: int
    available_cpu_snapshot: int
    available_memory_mb_snapshot: int
    available_gpu_snapshot: int
    effective_priority: float

    model_config = ConfigDict(from_attributes=True)


class CapacityOut(BaseModel):
    total_cpu: int
    allocated_cpu: int
    available_cpu: int
    total_memory_mb: int
    allocated_memory_mb: int
    available_memory_mb: int
    total_gpu: int
    allocated_gpu: int
    available_gpu: int


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
