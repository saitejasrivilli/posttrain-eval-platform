from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class AttemptResumeDecision(Base):
    __tablename__ = "attempt_resume_decisions"

    training_run_id = Column(UUID(as_uuid=True), ForeignKey("training_runs.id"), primary_key=True)
    attempt_number = Column(Integer, primary_key=True)
    resumed_from_step = Column(Integer, nullable=True)  # NULL = trained from scratch
    decided_at = Column(DateTime(timezone=True), nullable=False)
