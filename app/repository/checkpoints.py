import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401
from app.models import training_run as _training_run  # noqa: F401
from app.models.checkpoint import Checkpoint

# ADR 016: checkpoint registration is fencing-conditioned on the job's LIVE
# state -- status='RUNNING', lease_owner, attempt_number -- in addition to the
# artifact's own upload-lease fencing (V0.5/ADR 013). A stale worker's
# checkpoint may exist as bytes (independent lease) but never becomes a
# TRUSTED checkpoint if this INSERT's WHERE EXISTS fails to match.
_REGISTER_SQL = text(
    """
    INSERT INTO checkpoints
        (training_run_id, attempt_number, step, artifact_id,
         base_model_id, base_model_version_number, checkpoint_format_version, created_at)
    SELECT :training_run_id, :attempt_number, :step, :artifact_id,
           :base_model_id, :base_model_version_number, :checkpoint_format_version, :created_at
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
    step: int,
    artifact_id: uuid.UUID,
    base_model_id: uuid.UUID | None,
    base_model_version_number: int | None,
    checkpoint_format_version: int,
) -> bool:
    """Returns True iff the fencing check passed and the checkpoint was
    registered. False means the worker has been fenced out -- the artifact
    may still exist in storage (orphaned, per ADR 016), but this checkpoint
    is never trusted."""
    result = db.execute(
        _REGISTER_SQL,
        {
            "training_run_id": str(training_run_id),
            "attempt_number": attempt_number,
            "step": step,
            "artifact_id": str(artifact_id),
            "base_model_id": str(base_model_id) if base_model_id else None,
            "base_model_version_number": base_model_version_number,
            "checkpoint_format_version": checkpoint_format_version,
            "created_at": datetime.now(timezone.utc),
            "job_id": str(job_id),
            "worker_id": worker_id,
        },
    )
    db.commit()
    return result.rowcount > 0


def list_for_run(db: Session, training_run_id: uuid.UUID) -> list[Checkpoint]:
    return (
        db.query(Checkpoint)
        .filter(Checkpoint.training_run_id == training_run_id)
        .order_by(Checkpoint.step.desc(), Checkpoint.attempt_number.desc(), Checkpoint.created_at.desc())
        .all()
    )
