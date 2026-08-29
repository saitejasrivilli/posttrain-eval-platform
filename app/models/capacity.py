import uuid

from sqlalchemy import Column, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base

# Well-known singleton row id -- V0.4's aggregate resource pool is a single
# row by convention (RESOURCE_MODEL_V0.4.md), not enforced by a DB constraint
# beyond application discipline (only ever query/update this one id).
SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Capacity(Base):
    __tablename__ = "capacity"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    total_cpu = Column(Integer, nullable=False)
    allocated_cpu = Column(Integer, nullable=False, default=0)
    total_memory_mb = Column(Integer, nullable=False)
    allocated_memory_mb = Column(Integer, nullable=False, default=0)
    total_gpu = Column(Integer, nullable=False)
    allocated_gpu = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
