import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import job as _job  # noqa: F401 -- registers Job so Attempt's FK resolves
from app.models.attempt import Attempt


def get(db: Session, job_id: uuid.UUID, attempt_number: int) -> Attempt | None:
    return (
        db.query(Attempt)
        .filter(Attempt.job_id == job_id, Attempt.attempt_number == attempt_number)
        .one_or_none()
    )


def list_for_job(db: Session, job_id: uuid.UUID) -> list[Attempt]:
    return (
        db.query(Attempt)
        .filter(Attempt.job_id == job_id)
        .order_by(Attempt.attempt_number.asc())
        .all()
    )


def insert(db: Session, job_id: uuid.UUID, attempt_number: int, worker_id: str) -> Attempt:
    attempt = Attempt(
        job_id=job_id,
        attempt_number=attempt_number,
        worker_id=worker_id,
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def finalize(
    db: Session,
    job_id: uuid.UUID,
    attempt_number: int,
    status: str,
    error_message: str | None = None,
    error_classification: str | None = None,
    failure_domain: str | None = None,
    commit: bool = True,
) -> Attempt | None:
    attempt = get(db, job_id, attempt_number)
    if attempt is None:
        return None
    attempt.status = status
    attempt.finished_at = datetime.now(timezone.utc)
    attempt.error_message = error_message
    attempt.error_classification = error_classification
    attempt.failure_domain = failure_domain
    db.add(attempt)
    if commit:
        db.commit()
        db.refresh(attempt)
    else:
        db.flush()
    return attempt


def mark_lost(
    db: Session, job_id: uuid.UUID, attempt_number: int, worker_id: str, commit: bool = True
) -> Attempt:
    """Recovery writes this for the OLD attempt it just fenced out. Distinct
    from finalize() because Recovery may be recording an attempt that never
    had an `attempts` row at all (worker died before even inserting one) --
    insert-or-update, not update-only. See ADR 006 on why LOST is separate
    from FAILED."""
    attempt = get(db, job_id, attempt_number)
    now = datetime.now(timezone.utc)
    if attempt is None:
        attempt = Attempt(
            job_id=job_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            status="LOST",
            started_at=now,
            finished_at=now,
            error_classification="transient",
            error_message="worker_lost: lease expired",
        )
        db.add(attempt)
    else:
        attempt.status = "LOST"
        attempt.finished_at = now
        attempt.error_classification = "transient"
        attempt.error_message = "worker_lost: lease expired"
        db.add(attempt)
    if commit:
        db.commit()
        db.refresh(attempt)
    else:
        db.flush()
    return attempt
