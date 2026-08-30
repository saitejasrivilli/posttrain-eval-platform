import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.evaluation_config import EvaluationConfig


def create(
    db: Session,
    task_type: str,
    metric_definitions: dict,
    batch_size: int,
    evaluator_code_commit: str,
    container_image: str,
    max_examples: int | None = None,
    max_sequence_length: int | None = None,
    random_seed: int | None = None,
) -> EvaluationConfig:
    """No update path after creation -- immutable declaration (ADR 018)."""
    config = EvaluationConfig(
        task_type=task_type,
        metric_definitions=metric_definitions,
        batch_size=batch_size,
        max_examples=max_examples,
        max_sequence_length=max_sequence_length,
        evaluator_code_commit=evaluator_code_commit,
        container_image=container_image,
        random_seed=random_seed,
        created_at=datetime.now(timezone.utc),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def get(db: Session, config_id: uuid.UUID) -> EvaluationConfig | None:
    return db.query(EvaluationConfig).filter(EvaluationConfig.id == config_id).one_or_none()
