import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import training_run as _training_run  # noqa: F401
from app.models.attempt_resume_decision import AttemptResumeDecision

_RECORD_SQL = text(
    """
    INSERT INTO attempt_resume_decisions
        (training_run_id, attempt_number, resumed_from_step, decided_at)
    SELECT :training_run_id, :attempt_number, :resumed_from_step, :decided_at
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
    resumed_from_step: int | None,
) -> bool:
    """Fencing-conditioned (ADR 016) -- records, once per attempt, the
    outcome of checkpoint discovery (ADR 015), for lineage completeness."""
    result = db.execute(
        _RECORD_SQL,
        {
            "training_run_id": str(training_run_id),
            "attempt_number": attempt_number,
            "resumed_from_step": resumed_from_step,
            "decided_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    db.commit()
    return result.rowcount > 0


def get(db: Session, training_run_id: uuid.UUID, attempt_number: int) -> AttemptResumeDecision | None:
    return (
        db.query(AttemptResumeDecision)
        .filter(
            AttemptResumeDecision.training_run_id == training_run_id,
            AttemptResumeDecision.attempt_number == attempt_number,
        )
        .one_or_none()
    )
