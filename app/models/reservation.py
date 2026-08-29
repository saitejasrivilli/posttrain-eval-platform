from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Reservation(Base):
    __tablename__ = "reservations"

    # Composite PK mirrors `attempts` (ADR 006 precedent) -- one reservation
    # per attempt, never reused across retries. See ADR 007/DB_SCHEMA_CHANGES_V0.4.md.
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    attempt_number = Column(Integer, primary_key=True)
    cpu = Column(Integer, nullable=False)
    memory_mb = Column(Integer, nullable=False)
    gpu = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="ACTIVE")  # ACTIVE | RELEASED
    created_at = Column(DateTime(timezone=True), nullable=False)
    released_at = Column(DateTime(timezone=True), nullable=True)
