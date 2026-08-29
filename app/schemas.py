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


# --- V0.5: ML artifact & lineage platform ---


class ArtifactOut(BaseModel):
    id: uuid.UUID
    content_hash: str
    storage_key: str
    artifact_type: str
    size_bytes: Optional[int] = None
    status: str
    job_id: Optional[uuid.UUID] = None
    attempt_number: Optional[int] = None
    created_at: datetime
    uploaded_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ArtifactList(BaseModel):
    items: list[ArtifactOut]
    limit: int
    offset: int
    total: int


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DatasetVersionOut(BaseModel):
    dataset_id: uuid.UUID
    version_number: int
    artifact_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DatasetVersionList(BaseModel):
    items: list[DatasetVersionOut]
    limit: int
    offset: int
    total: int


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class ModelOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelVersionCreate(BaseModel):
    artifact_id: uuid.UUID
    training_run_id: Optional[uuid.UUID] = None


class ModelVersionOut(BaseModel):
    model_id: uuid.UUID
    version_number: int
    artifact_id: uuid.UUID
    training_run_id: Optional[uuid.UUID] = None
    registered_at: datetime

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class ModelVersionList(BaseModel):
    items: list[ModelVersionOut]
    limit: int
    offset: int
    total: int


class TrainingRunCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=128)
    dataset_id: uuid.UUID
    dataset_version_number: int
    training_config: dict[str, Any] = Field(default_factory=dict)
    code_commit: str
    container_image: str
    base_model_id: Optional[uuid.UUID] = None
    base_model_version_number: Optional[int] = None
    random_seed: Optional[int] = None
    priority: int = Field(default=50, ge=0, le=100)
    job_config: Optional[dict[str, Any]] = None


class TrainingRunOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_version_number: int
    base_model_id: Optional[uuid.UUID] = None
    base_model_version_number: Optional[int] = None
    training_config: dict[str, Any]
    code_commit: str
    container_image: str
    random_seed: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
