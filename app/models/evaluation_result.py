from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    # One durable result per evaluation example. (evaluation_run_id,
    # example_id) is the logical identity (DB_SCHEMA_CHANGES_V0.7.md) -- a
    # retried attempt re-emitting the same example is an idempotent no-op
    # (ON CONFLICT DO NOTHING), never a duplicate logical row.
    evaluation_run_id = Column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id"), primary_key=True)
    example_id = Column(String, primary_key=True)
    prediction = Column(Text, nullable=True)
    expected_output = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    attempt_number = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
