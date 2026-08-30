from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Attempt(Base):
    __tablename__ = "attempts"

    # (job_id, attempt_number) is the natural identity of an attempt -- see
    # ADR 006. attempt_number here is fixed at the value the attempt held when
    # it ran; jobs.attempt_number is the live fencing token (ADR 004).
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    attempt_number = Column(Integer, primary_key=True)
    worker_id = Column(String, nullable=False)
    status = Column(String, nullable=False)  # RUNNING | SUCCEEDED | FAILED | CANCELLED | LOST
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(String, nullable=True)
    error_classification = Column(String, nullable=True)  # transient | permanent | unknown
    # V0.6: orthogonal to error_classification -- INFRASTRUCTURE | TRAINING.
    # Keeps ML failure metrics honest (GPU_WORKER_MODEL_V0.6.md).
    failure_domain = Column(String, nullable=True)
