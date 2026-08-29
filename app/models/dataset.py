import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    # Composite PK mirrors attempts/reservations (ADR 006/010 precedent).
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("datasets.id"), primary_key=True)
    version_number = Column(Integer, primary_key=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
