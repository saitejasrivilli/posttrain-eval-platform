from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"

    # Aggregate metric values. (evaluation_run_id, metric_name, split) is the
    # unique identity (DB_SCHEMA_CHANGES_V0.7.md). Immutable once the run
    # reaches terminal success; a retry fills any missing metric via
    # ON CONFLICT DO NOTHING without rewriting existing rows.
    evaluation_run_id = Column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id"), primary_key=True)
    metric_name = Column(String, primary_key=True)
    split = Column(String, primary_key=True)
    metric_value = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    attempt_number = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
