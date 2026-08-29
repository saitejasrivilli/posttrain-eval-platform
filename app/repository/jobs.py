import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, update as sa_update
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


def claim(db: Session, job_id: uuid.UUID, worker_id: str, lease_duration_seconds: int) -> Job | None:
    """Atomic claim: QUEUED -> RUNNING, incrementing attempt_number (the
    fencing token, ADR 004). Fails to match (returns None) if the job is not
    QUEUED, has cancel_requested=true, or its next_retry_at backoff hasn't
    elapsed yet -- all three are enforced as one WHERE clause, per
    ARCHITECTURE_V0.3.md's "claim/reclaim precondition" list."""
    now = datetime.now(timezone.utc)
    stmt = (
        sa_update(Job)
        .where(
            Job.id == job_id,
            Job.status == "QUEUED",
            Job.cancel_requested.is_(False),
            (Job.next_retry_at.is_(None)) | (Job.next_retry_at <= now),
            Job.deleted_at.is_(None),
        )
        .values(
            status="RUNNING",
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_duration_seconds),
            attempt_number=Job.attempt_number + 1,
            claimed_at=now,
            next_retry_at=None,
        )
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, job_id)


def heartbeat(
    db: Session, job_id: uuid.UUID, worker_id: str, attempt_number: int, lease_duration_seconds: int
) -> bool:
    """Renew the lease. Fencing-conditioned: succeeds only if this worker still
    holds this exact attempt_number. rowcount 0 means this worker has been
    fenced out and must abandon the attempt (ADR 004)."""
    stmt = (
        sa_update(Job)
        .where(
            Job.id == job_id,
            Job.status == "RUNNING",
            Job.lease_owner == worker_id,
            Job.attempt_number == attempt_number,
        )
        .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds))
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount > 0


def finalize_attempt(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    attempt_number: int,
    to_status: str,
    extra_values: dict | None = None,
) -> Job | None:
    """Fencing-conditioned terminal write for an attempt: SUCCEEDED, FAILED,
    CANCELLED, or QUEUED-for-retry. Succeeds only if this worker still holds
    this exact attempt_number -- exactly the same check as heartbeat(), reused
    rather than reinvented (ADR 004). rowcount 0 means the caller's result is
    stale and MUST be discarded, never retried."""
    values = {"status": to_status, "lease_owner": None, "lease_expires_at": None}
    if extra_values:
        values.update(extra_values)
    stmt = (
        sa_update(Job)
        .where(
            Job.id == job_id,
            Job.status == "RUNNING",
            Job.lease_owner == worker_id,
            Job.attempt_number == attempt_number,
        )
        .values(**values)
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount == 0:
        return None
    return get(db, job_id)


def list_stale_leases(db: Session) -> list[Job]:
    now = datetime.now(timezone.utc)
    return (
        db.query(Job)
        .filter(Job.status == "RUNNING", Job.lease_expires_at < now, Job.deleted_at.is_(None))
        .all()
    )


def reclaim_stale(
    db: Session, job_id: uuid.UUID, max_attempts: int, computed_next_retry_at: datetime
) -> tuple[int, str] | None:
    """Recovery process's atomic reclaim (ADR 004). Fences out the old owner
    by moving status away from RUNNING -- the heartbeat/finalize fencing
    checks both require `status='RUNNING'`, so this alone invalidates every
    future write the old worker makes; attempt_number is NOT incremented here
    (that would create a numbering gap, since claim() is the only place a
    NEW attempt actually starts running -- see the note this fix added after
    catching the gap during implementation). Branches, in the SAME statement,
    on cancel_requested / MAX_ATTEMPTS to decide the resulting status:
    CANCELLED, FAILED (attempts exhausted), or QUEUED (retry scheduled).
    Returns (attempt_number, new_status) or None if another process already
    reclaimed this job first (or its lease was renewed in time)."""
    now = datetime.now(timezone.utc)
    new_status = case(
        (Job.cancel_requested.is_(True), "CANCELLED"),
        (Job.attempt_number >= max_attempts, "FAILED"),
        else_="QUEUED",
    )
    new_next_retry_at = case(
        (Job.cancel_requested.is_(True), None),
        (Job.attempt_number >= max_attempts, None),
        else_=computed_next_retry_at,
    )
    stmt = (
        sa_update(Job)
        .where(Job.id == job_id, Job.status == "RUNNING", Job.lease_expires_at < now)
        .values(
            lease_owner=None,
            lease_expires_at=None,
            status=new_status,
            next_retry_at=new_next_retry_at,
        )
        .returning(Job.attempt_number, Job.status)
    )
    result = db.execute(stmt)
    row = result.first()
    db.commit()
    if row is None:
        return None
    attempt_number, resulting_status = row
    return attempt_number, resulting_status


def list_retry_due(db: Session) -> list[Job]:
    now = datetime.now(timezone.utc)
    return (
        db.query(Job)
        .filter(
            Job.status == "QUEUED",
            Job.next_retry_at.isnot(None),
            Job.next_retry_at <= now,
            Job.deleted_at.is_(None),
        )
        .all()
    )


def clear_retry_dispatched(db: Session, job_id: uuid.UUID) -> None:
    """Marks a retry as dispatched so the same due job isn't re-emitted every
    Recovery poll cycle. Called in the same transaction as the outbox insert."""
    stmt = sa_update(Job).where(Job.id == job_id).values(next_retry_at=None)
    db.execute(stmt)


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
