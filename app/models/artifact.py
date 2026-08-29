import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    # V0.5 -- see ADR 011 (content-addressed storage), ADR 013 (consistency
    # model: PENDING -> UPLOADED/FAILED, with an upload lease -- ADR 004's
    # fencing mechanism reused -- to distinguish "actively uploading" from
    # "abandoned").
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_hash = Column(String, nullable=False, unique=True)
    storage_key = Column(String, nullable=False)
    artifact_type = Column(String, nullable=False)  # DATASET | MODEL | CHECKPOINT
    size_bytes = Column(BigInteger, nullable=True)
    attempt_number = Column(Integer, nullable=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True)
    status = Column(String, nullable=False, default="PENDING")  # PENDING | UPLOADED | FAILED
    uploader_id = Column(String, nullable=True)
    upload_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
