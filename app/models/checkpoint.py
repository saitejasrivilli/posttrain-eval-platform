from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    # (training_run_id, attempt_number, step) mirrors attempts/reservations
    # (ADR 006/010 precedent). See ADR 015/016.
    training_run_id = Column(UUID(as_uuid=True), ForeignKey("training_runs.id"), primary_key=True)
    attempt_number = Column(Integer, primary_key=True)
    step = Column(Integer, primary_key=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False, unique=True)
    base_model_id = Column(UUID(as_uuid=True), nullable=True)
    base_model_version_number = Column(Integer, nullable=True)
    checkpoint_format_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
