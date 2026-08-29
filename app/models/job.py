import enum
import uuid

from sqlalchemy import Boolean, Column, Integer, String, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default=JobStatus.PENDING.value)
    config = Column(JSON, nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    # V0.3: attempt_number is the fencing token (ADR 004) as well as the
    # attempt counter. lease_owner/lease_expires_at are set only while RUNNING.
    # next_retry_at gates when a QUEUED job becomes claimable again (ADR 005).
    attempt_number = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    # V0.4: plain scalar priority, no named tiers (see RESOURCE_MODEL_V0.4.md).
    priority = Column(Integer, nullable=False, default=50)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
