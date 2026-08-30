import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.training_run import TrainingRun


def create(
    db: Session,
    job_id: uuid.UUID,
    dataset_id: uuid.UUID,
    dataset_version_number: int,
    training_config: dict,
    code_commit: str,
    container_image: str,
    base_model_id: uuid.UUID | None = None,
    base_model_version_number: int | None = None,
    random_seed: int | None = None,
) -> TrainingRun:
    """No update path exists for this row after creation -- immutable
    historical record (STATE_TRANSITIONS_V0.5.md #3)."""
    run = TrainingRun(
        job_id=job_id,
        dataset_id=dataset_id,
        dataset_version_number=dataset_version_number,
        base_model_id=base_model_id,
        base_model_version_number=base_model_version_number,
        training_config=training_config,
        code_commit=code_commit,
        container_image=container_image,
        random_seed=random_seed,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get(db: Session, training_run_id: uuid.UUID) -> TrainingRun | None:
    return db.query(TrainingRun).filter(TrainingRun.id == training_run_id).one_or_none()


def get_by_job_id(db: Session, job_id: uuid.UUID) -> TrainingRun | None:
    return db.query(TrainingRun).filter(TrainingRun.job_id == job_id).one_or_none()
