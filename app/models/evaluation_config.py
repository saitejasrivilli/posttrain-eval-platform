import uuid

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db import Base


class EvaluationConfig(Base):
    __tablename__ = "evaluation_configs"

    # V0.7: immutable declaration of how an evaluation is performed
    # (EVALUATION_MODEL_V0.7.md). No application code path updates any column
    # after INSERT.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type = Column(String, nullable=False)
    metric_definitions = Column(JSONB, nullable=False)
    batch_size = Column(Integer, nullable=False)
    max_examples = Column(Integer, nullable=True)
    max_sequence_length = Column(Integer, nullable=True)
    evaluator_code_commit = Column(String, nullable=False)
    container_image = Column(String, nullable=False)
    random_seed = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
