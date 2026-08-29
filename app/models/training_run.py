import uuid

from sqlalchemy import Column, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class TrainingRun(Base):
    __tablename__ = "training_runs"

    # V0.5: immutable historical record once created (STATE_TRANSITIONS_V0.5.md
    # #3) -- no application code path updates any column here after INSERT.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False)
    dataset_id = Column(UUID(as_uuid=True), nullable=False)
    dataset_version_number = Column(Integer, nullable=False)
    base_model_id = Column(UUID(as_uuid=True), nullable=True)
    base_model_version_number = Column(Integer, nullable=True)
    training_config = Column(JSON, nullable=False)
    code_commit = Column(String, nullable=False)
    container_image = Column(String, nullable=False)
    random_seed = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version_number"],
            ["dataset_versions.dataset_id", "dataset_versions.version_number"],
            name="fk_training_runs_dataset_version",
        ),
        ForeignKeyConstraint(
            ["base_model_id", "base_model_version_number"],
            ["model_versions.model_id", "model_versions.version_number"],
            name="fk_training_runs_base_model_version",
        ),
    )
