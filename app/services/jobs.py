import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.repository import jobs as repo
from app.schemas import JobCreate, JobUpdate
from app.statemachine import sources_for


def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(job_type=payload.job_type, status=JobStatus.PENDING.value, config=payload.config)
    # V0.2: job auto-queues immediately on creation (no separate queueing
    # decision yet -- that's V0.4's scheduler). Insert + transition to QUEUED +
    # outbox row all happen in the one transaction ADR 002 requires.
    return repo.create_and_enqueue(
        db, job, queued_status=JobStatus.QUEUED.value, event_type="job.queued"
    )


def get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = repo.get(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def list_jobs(db: Session, limit: int, offset: int) -> tuple[list[Job], int]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be >= 0")
    return repo.list_(db, limit, offset)


def transition(db: Session, job_id: uuid.UUID, to_status: str) -> Job:
    """Single owner of the job state machine. The client requests a desired
    status; this function decides whether it's reachable from the job's
    current state and performs the transition atomically. No caller may set
    `Job.status` any other way -- see API_CHANGES_V0.2.md."""
    valid_from = sources_for(to_status)
    job = repo.conditional_transition(db, job_id, valid_from, to_status)
    if job is None:
        current = repo.get(db, job_id)
        if current is None:
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(
            status_code=409,
            detail={
                "message": "invalid transition",
                "from": current.status,
                "to": to_status,
            },
        )
    return job


def update_job(db: Session, job_id: uuid.UUID, payload: JobUpdate) -> Job:
    if payload.status is not None:
        job = transition(db, job_id, payload.status)
    else:
        job = get_job(db, job_id)
    if payload.config is not None:
        job.config = payload.config
        job = repo.update(db, job)
    return job


def cancel_job(db: Session, job_id: uuid.UUID) -> Job:
    current = get_job(db, job_id)
    if current.status in (JobStatus.PENDING.value, JobStatus.QUEUED.value):
        return transition(db, job_id, JobStatus.CANCELLED.value)
    if current.status == JobStatus.RUNNING.value:
        job = repo.set_cancel_requested(db, job_id)
        if job is None:
            # lost the race: job left RUNNING between our read and this call
            return get_job(db, job_id)
        return job
    raise HTTPException(
        status_code=409,
        detail={
            "message": "job already in a terminal state, cannot cancel",
            "status": current.status,
        },
    )


def delete_job(db: Session, job_id: uuid.UUID) -> None:
    job = get_job(db, job_id)
    repo.soft_delete(db, job)
