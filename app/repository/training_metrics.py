import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import training_run as _training_run  # noqa: F401
from app.models.training_metric import TrainingMetric

_RECORD_SQL = text(
    """
    INSERT INTO training_metrics
        (id, training_run_id, attempt_number, step, loss, learning_rate,
         gpu_memory_allocated_mb, recorded_at)
    SELECT :id, :training_run_id, :attempt_number, :step, :loss, :learning_rate,
           :gpu_memory_allocated_mb, :recorded_at
    WHERE EXISTS (
        SELECT 1 FROM jobs
        WHERE id = :job_id AND status = 'RUNNING'
          AND lease_owner = :worker_id AND attempt_number = :attempt_number
    )
    """
)


def record(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    training_run_id: uuid.UUID,
    attempt_number: int,
    step: int,
    loss: float | None,
    learning_rate: float | None,
    gpu_memory_allocated_mb: int | None,
) -> bool:
    """Fencing-conditioned (ADR 016), same rationale as checkpoint
    registration -- a fenced-out worker's metric reports are discarded."""
    result = db.execute(
        _RECORD_SQL,
        {
            "id": str(uuid.uuid4()),
            "training_run_id": str(training_run_id),
            "attempt_number": attempt_number,
            "step": step,
            "loss": loss,
            "learning_rate": learning_rate,
            "gpu_memory_allocated_mb": gpu_memory_allocated_mb,
            "recorded_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    db.commit()
    return result.rowcount > 0


def list_for_run(db: Session, training_run_id: uuid.UUID, limit: int, offset: int) -> tuple[list[TrainingMetric], int]:
    base = db.query(TrainingMetric).filter(TrainingMetric.training_run_id == training_run_id)
    total = base.count()
    items = base.order_by(TrainingMetric.step.asc()).limit(limit).offset(offset).all()
    return items, total
