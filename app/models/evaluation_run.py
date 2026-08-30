import uuid

from sqlalchemy import Column, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    # V0.7: immutable record of one evaluation request (ADR 018). The
    # candidate model, dataset, config, and optional baseline references
    # cannot change after creation. Only `status`/`completed_at` mirror the
    # execution outcome (STATE_TRANSITIONS_V0.7.md) -- fencing-conditioned.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id = Column(UUID(as_uuid=True), nullable=False)
    model_version_number = Column(Integer, nullable=False)
    dataset_id = Column(UUID(as_uuid=True), nullable=False)
    dataset_version_number = Column(Integer, nullable=False)
    evaluation_config_id = Column(UUID(as_uuid=True), ForeignKey("evaluation_configs.id"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    baseline_model_id = Column(UUID(as_uuid=True), nullable=True)
    baseline_model_version_number = Column(Integer, nullable=True)
    status = Column(String, nullable=False)
    evaluator_code_commit = Column(String, nullable=False)
    container_image = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "model_version_number"],
            ["model_versions.model_id", "model_versions.version_number"],
            name="fk_evaluation_runs_model_version",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version_number"],
            ["dataset_versions.dataset_id", "dataset_versions.version_number"],
            name="fk_evaluation_runs_dataset_version",
        ),
        ForeignKeyConstraint(
            ["baseline_model_id", "baseline_model_version_number"],
            ["model_versions.model_id", "model_versions.version_number"],
            name="fk_evaluation_runs_baseline_model_version",
        ),
    )
