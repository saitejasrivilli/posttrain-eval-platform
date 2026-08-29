from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class DLQEntry(Base):
    __tablename__ = "dlq"

    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    moved_to_dlq_at = Column(DateTime(timezone=True), nullable=False)
    last_attempt_number = Column(Integer, nullable=False)
    last_error_message = Column(String, nullable=True)
    last_error_classification = Column(String, nullable=False)
    total_attempts = Column(Integer, nullable=False)
