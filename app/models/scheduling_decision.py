import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class SchedulingDecision(Base):
    __tablename__ = "scheduling_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    decided_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decision = Column(String, nullable=False)  # ADMITTED | WAITING
    reason = Column(String, nullable=False)
    requested_cpu = Column(Integer, nullable=False)
    requested_memory_mb = Column(Integer, nullable=False)
    requested_gpu = Column(Integer, nullable=False)
    available_cpu_snapshot = Column(Integer, nullable=False)
    available_memory_mb_snapshot = Column(Integer, nullable=False)
    available_gpu_snapshot = Column(Integer, nullable=False)
    effective_priority = Column(Numeric, nullable=False)
