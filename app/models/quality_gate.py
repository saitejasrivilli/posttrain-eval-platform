import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db import Base


class QualityGate(Base):
    __tablename__ = "quality_gates"

    # Immutable declarative policy over named aggregate metrics
    # (QUALITY_GATE_MODEL_V0.7.md / ADR 019). No update path after creation.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    rules = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
