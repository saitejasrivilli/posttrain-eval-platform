import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db import Base


class QualityGateResult(Base):
    __tablename__ = "quality_gate_results"

    # Durable evaluation of one gate against one EvaluationRun. Status is
    # PASS / FAIL / ERROR (never silently PASS on missing evidence). Unique
    # (evaluation_run_id, quality_gate_id) makes the logical decision
    # idempotent (FAILURE_SCENARIOS_V0.7.md #20).
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_run_id = Column(UUID(as_uuid=True), ForeignKey("evaluation_runs.id"), nullable=False)
    quality_gate_id = Column(UUID(as_uuid=True), ForeignKey("quality_gates.id"), nullable=False)
    status = Column(String, nullable=False)
    rule_results = Column(JSONB, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "quality_gate_id", name="uq_quality_gate_results_run_gate"),
    )
