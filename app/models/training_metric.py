import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class TrainingMetric(Base):
    __tablename__ = "training_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    training_run_id = Column(UUID(as_uuid=True), ForeignKey("training_runs.id"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    step = Column(Integer, nullable=False)
    loss = Column(Float, nullable=True)
    learning_rate = Column(Float, nullable=True)
    gpu_memory_allocated_mb = Column(Integer, nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
