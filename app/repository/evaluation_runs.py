import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401 -- registers Job so FKs resolve
from app.models.evaluation_run import EvaluationRun

# Fencing-conditioned terminal/status write (ADR 016, same shape as
# checkpoints/training_run_outputs). A stale evaluator whose lease was
# reclaimed -- so the job is no longer RUNNING under its (lease_owner,
# attempt_number) -- can never flip the run's status. This is what makes
# "a stale evaluator cannot write SUCCESS/FAILED/complete" true structurally.
_MARK_STATUS_SQL = text(
    """
    UPDATE evaluation_runs
    SET status = :status,
        completed_at = :completed_at
    WHERE id = :evaluation_run_id
      AND EXISTS (
        SELECT 1 FROM jobs
        WHERE id = :job_id AND status = 'RUNNING'
          AND lease_owner = :worker_id AND attempt_number = :attempt_number
      )
    """
)


def create(
    db: Session,
    job_id: uuid.UUID,
    model_id: uuid.UUID,
    model_version_number: int,
    dataset_id: uuid.UUID,
    dataset_version_number: int,
    evaluation_config_id: uuid.UUID,
    evaluator_code_commit: str,
    container_image: str,
    baseline_model_id: uuid.UUID | None = None,
    baseline_model_version_number: int | None = None,
) -> EvaluationRun:
    """Immutable input references (ADR 018). Only status/completed_at ever
    change afterward, and only through the fenced mark_status() below."""
    run = EvaluationRun(
        job_id=job_id,
        model_id=model_id,
        model_version_number=model_version_number,
        dataset_id=dataset_id,
        dataset_version_number=dataset_version_number,
        evaluation_config_id=evaluation_config_id,
        baseline_model_id=baseline_model_id,
        baseline_model_version_number=baseline_model_version_number,
        status="QUEUED",
        evaluator_code_commit=evaluator_code_commit,
        container_image=container_image,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get(db: Session, evaluation_run_id: uuid.UUID) -> EvaluationRun | None:
    return db.query(EvaluationRun).filter(EvaluationRun.id == evaluation_run_id).one_or_none()


def get_by_job_id(db: Session, job_id: uuid.UUID) -> EvaluationRun | None:
    return db.query(EvaluationRun).filter(EvaluationRun.job_id == job_id).one_or_none()


def list_for_model_version(
    db: Session, model_id: uuid.UUID, model_version_number: int
) -> list[EvaluationRun]:
    return (
        db.query(EvaluationRun)
        .filter(
            EvaluationRun.model_id == model_id,
            EvaluationRun.model_version_number == model_version_number,
        )
        .order_by(EvaluationRun.created_at.desc())
        .all()
    )


def mark_status(
    db: Session,
    evaluation_run_id: uuid.UUID,
    job_id: uuid.UUID,
    worker_id: str,
    attempt_number: int,
    status: str,
    set_completed: bool,
    commit: bool = True,
) -> bool:
    """Returns True iff the fencing check passed and the run status was
    updated. False means the writer has been fenced out."""
    result = db.execute(
        _MARK_STATUS_SQL,
        {
            "status": status,
            "completed_at": datetime.now(timezone.utc) if set_completed else None,
            "evaluation_run_id": str(evaluation_run_id),
            "job_id": str(job_id),
            "worker_id": worker_id,
            "attempt_number": attempt_number,
        },
    )
    if commit:
        db.commit()
    return result.rowcount > 0
