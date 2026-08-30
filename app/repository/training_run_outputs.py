import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import training_run as _training_run  # noqa: F401
from app.models.training_run_output import TrainingRunOutput

_REGISTER_SQL = text(
    """
    INSERT INTO training_run_outputs
        (training_run_id, final_artifact_id, attempt_number, created_at)
    SELECT :training_run_id, :final_artifact_id, :attempt_number, :created_at
    WHERE EXISTS (
        SELECT 1 FROM jobs
        WHERE id = :job_id AND status = 'RUNNING'
          AND lease_owner = :worker_id AND attempt_number = :attempt_number
    )
    """
)


def register(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    training_run_id: uuid.UUID,
    attempt_number: int,
    final_artifact_id: uuid.UUID,
) -> bool:
    """Fencing-conditioned (ADR 016). Also structurally at-most-once per
    training_run_id (primary key) -- a second attempt cannot register a
    second output even if it somehow tried."""
    result = db.execute(
        _REGISTER_SQL,
        {
            "training_run_id": str(training_run_id),
            "final_artifact_id": str(final_artifact_id),
            "attempt_number": attempt_number,
            "created_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    db.commit()
    return result.rowcount > 0


def get(db: Session, training_run_id: uuid.UUID) -> TrainingRunOutput | None:
    return (
        db.query(TrainingRunOutput)
        .filter(TrainingRunOutput.training_run_id == training_run_id)
        .one_or_none()
    )
