from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Execution(Base):
    __tablename__ = "executions"

    # V0.2: job_id is the primary key -- one execution record per job (no retry
    # engine yet). V0.3+ migrates this to a composite (job_id, attempt_id) key;
    # see ADR 003.
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    worker_id = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String, nullable=True)
