import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401 -- registers Job so Execution's FK to jobs.id resolves
from app.models.execution import Execution


def get(db: Session, job_id: uuid.UUID) -> Execution | None:
    return db.query(Execution).filter(Execution.job_id == job_id).one_or_none()


def insert(db: Session, job_id: uuid.UUID, worker_id: str) -> Execution:
    execution = Execution(job_id=job_id, worker_id=worker_id, started_at=datetime.now(timezone.utc))
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution


def finalize(db: Session, job_id: uuid.UUID, outcome: str) -> Execution | None:
    execution = get(db, job_id)
    if execution is None:
        return None
    execution.outcome = outcome
    execution.finished_at = datetime.now(timezone.utc)
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution
