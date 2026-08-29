import uuid
from datetime import datetime, timezone

from sqlalchemy import func, update as sa_update
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.outbox import Outbox


def insert(db: Session, job: Job) -> Job:
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def create_and_enqueue(db: Session, job: Job, queued_status: str, event_type: str) -> Job:
    """Insert job, transition it to `queued_status`, and write the outbox row --
    all in one transaction. See ADR 002: this atomicity is the entire point of
    the outbox pattern (no window where the job exists without a durable intent
    to publish, or vice versa)."""
    db.add(job)
    db.flush()
    job.status = queued_status
    outbox_row = Outbox(job_id=job.id, event_type=event_type, payload={"job_id": str(job.id)})
    db.add(outbox_row)
    db.commit()
    db.refresh(job)
    return job


def conditional_transition(
    db: Session,
    job_id: uuid.UUID,
    valid_from: list[str],
    to_status: str,
    extra_values: dict | None = None,
) -> Job | None:
    """Atomic state transition: succeeds only if the job's current status is
    one of `valid_from`. Returns the updated Job, or None if the transition
    did not apply (row not found, already in a different state, or deleted).
    This single primitive is what makes transitions both concurrency-safe
    (only one racing caller can win) and state-machine-legal (illegal
    transitions are simply never attempted with a matching `valid_from`)."""
    values = {"status": to_status}
    if extra_values:
        values.update(extra_values)

    stmt = (
        sa_update(Job)
        .where(Job.id == job_id, Job.status.in_(valid_from), Job.deleted_at.is_(None))
        .values(**values)
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, job_id)


def set_cancel_requested(db: Session, job_id: uuid.UUID) -> Job | None:
    """Cooperative cancellation signal for a RUNNING job -- does not itself
    transition status. See STATE_TRANSITIONS_V0.2.md: cancellation of a RUNNING
    job is cooperative, observed by the worker at its next checkpoint."""
    stmt = (
        sa_update(Job)
        .where(Job.id == job_id, Job.status == "RUNNING", Job.deleted_at.is_(None))
        .values(cancel_requested=True)
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, job_id)


def get(db: Session, job_id: uuid.UUID) -> Job | None:
    return (
        db.query(Job)
        .filter(Job.id == job_id, Job.deleted_at.is_(None))
        .one_or_none()
    )


def list_(db: Session, limit: int, offset: int) -> tuple[list[Job], int]:
    base = db.query(Job).filter(Job.deleted_at.is_(None))
    total = base.with_entities(func.count(Job.id)).scalar()
    items = base.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()
    return items, total


def update(db: Session, job: Job) -> Job:
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def soft_delete(db: Session, job: Job) -> Job:
    job.deleted_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
