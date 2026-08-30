from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class TrainingRunOutput(Base):
    __tablename__ = "training_run_outputs"

    # Exactly one row per training run, ever (STATE_TRANSITIONS_V0.6.md).
    training_run_id = Column(UUID(as_uuid=True), ForeignKey("training_runs.id"), primary_key=True)
    final_artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False, unique=True)
    attempt_number = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
