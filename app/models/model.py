import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Model(Base):
    __tablename__ = "models"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    model_id = Column(UUID(as_uuid=True), ForeignKey("models.id"), primary_key=True)
    version_number = Column(Integer, primary_key=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False, unique=True)
    training_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("training_runs.id", use_alter=True, name="fk_model_versions_training_run"),
        nullable=True,
    )
    registered_at = Column(DateTime(timezone=True), nullable=False)
